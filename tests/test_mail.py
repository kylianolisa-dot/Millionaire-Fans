import asyncio
import time
import unittest

from app import betting, config, db, emailtpl, game, mailer, paystack, settings, settlement, wallet
from tests.test_core import bot, fast_settings, fresh_db, mk_match, mk_user


def emails(kind=None):
    sql = "SELECT * FROM email_queue" + (" WHERE kind=?" if kind else "") + " ORDER BY id"
    return db.rows(sql, (kind,) if kind else ())


class Queue(unittest.TestCase):
    def setUp(self):
        fresh_db()

    def test_templates_escape_everything(self):
        subj, html, text = emailtpl.render("welcome", name='<script>alert(1)</script> Bob')
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("Millionaire Fans", html)
        self.assertTrue(text and subj)

    def test_every_kind_renders(self):
        v = dict(name="Ada", amount_kobo=150050, reference="r1", home="A", away="B", match_id=3, minutes=5, round_no=2,
                 link="https://x/#/reset/t", stage="success")
        for k in ["welcome", "deposit", "withdrawal", "trivia_soon", "prize_won", "password_reset", "password_changed", "test"]:
            s, h, t = emailtpl.render(k, **v)
            self.assertTrue(s and h and t, k)
        for st in emailtpl.WITHDRAWAL_STAGES:
            emailtpl.render("withdrawal", **{**v, "stage": st})

    def test_dedupe_and_disable(self):
        u = mk_user("a", 0)
        self.assertTrue(mailer.enqueue("welcome", "a@t.com", u["id"], "k1", name="A"))
        self.assertFalse(mailer.enqueue("welcome", "a@t.com", u["id"], "k1", name="A"))
        self.assertEqual(len(emails()), 1)
        settings.update({"email_enabled": 0})
        self.assertFalse(mailer.enqueue("welcome", "a@t.com", u["id"], "k2", name="A"))
        self.assertEqual(len(emails()), 1)

    def test_send_retry_then_fail(self):
        mailer.enqueue("test", "a@t.com")
        calls = []

        def boom(*a):
            calls.append(1)
            raise RuntimeError("smtp down")
        for i in range(mailer.MAX_ATTEMPTS):
            db.run("UPDATE email_queue SET next_try_at=0")
            mailer.process_due(sender=boom)
        e = emails()[0]
        self.assertEqual((e["status"], e["attempts"], len(calls)), ("failed", mailer.MAX_ATTEMPTS, mailer.MAX_ATTEMPTS))
        self.assertIn("smtp down", e["error"])

    def test_success_and_unconfigured(self):
        mailer.enqueue("test", "a@t.com")
        sent = []
        mailer.process_due(sender=lambda *a: sent.append(a))
        self.assertEqual((emails()[0]["status"], len(sent)), ("sent", 1))
        mailer.enqueue("test", "b@t.com")
        old = (config.SMTP_USER, config.SMTP_PASSWORD)
        config.SMTP_USER = config.SMTP_PASSWORD = ""
        try:
            mailer.process_due()                       # real sender, no SMTP set up -> skipped, nothing crashes
        finally:
            config.SMTP_USER, config.SMTP_PASSWORD = old
        self.assertEqual(emails()[-1]["status"], "skipped")

    def test_message_has_no_header_injection(self):
        m = mailer.build_message("a@t.com", "Hi", "<p>x</p>", "x")
        self.assertEqual(m["To"], "a@t.com")
        self.assertIn("multipart/alternative", m.get_content_type())


class MoneyEmails(unittest.TestCase):
    def setUp(self):
        fresh_db()
        self.u = mk_user("a", 0)
        db.run("UPDATE users SET recipient_code='RCP_x', bank_name='B', account_number='0123456789' WHERE id=?", (self.u["id"],))

    def test_deposit_emailed_once(self):
        db.run("INSERT INTO payments(user_id,kind,reference,amount_kobo,status,created_at,updated_at) VALUES(?,?,?,?,?,1,1)",
               (self.u["id"], "deposit", "dep1", 500000, "pending"))
        wallet.credit_deposit("dep1", 500000)
        wallet.credit_deposit("dep1", 500000)
        self.assertEqual([e["kind"] for e in emails()], ["deposit"])

    def test_withdrawal_every_stage(self):
        db.run("UPDATE users SET balance_kobo=1000000 WHERE id=?", (self.u["id"],))
        user = dict(db.one("SELECT * FROM users WHERE id=?", (self.u["id"],)))
        r = wallet.request_withdrawal(user, 500000)
        self.assertEqual(r["status"], "pending_approval")
        wallet.send_transfer(r["reference"])                       # admin approves (mock Paystack pays at once)
        stages = [e["dedupe_key"].split(":")[-1] for e in emails("withdrawal")]
        self.assertEqual(stages, ["pending_approval", "processing", "success"])
        # a second withdrawal that the admin declines
        r2 = wallet.request_withdrawal(dict(db.one("SELECT * FROM users WHERE id=?", (self.u["id"],))), 300000)
        wallet.decline_withdrawal(r2["reference"])
        self.assertIn("declined", [e["dedupe_key"].split(":")[-1] for e in emails("withdrawal")])
        wallet.decline_withdrawal(r2["reference"])                 # repeat: no duplicate email
        self.assertEqual(len([e for e in emails("withdrawal") if e["dedupe_key"].endswith(":declined")]), 1)


class GameEmails(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fresh_db()
        fast_settings()
        settings.update({"require_manual_questions": 0})

    def _match(self):
        users = [mk_user(n, 1_000_000) for n in "abc"]
        loser = mk_user("d", 1_000_000)
        m = mk_match()
        for u in users:
            betting.place_stake(u, m, "home", 100_000)
        betting.place_stake(loser, m, "away", 100_000)
        settlement.finish_match(m, 2, 0)
        return m, users, loser

    def test_trivia_reminder_goes_only_to_eligible_fans_once(self):
        m, users, loser = self._match()
        db.run("UPDATE matches SET trivia_start_at=? WHERE id=?", (int(time.time()) + 200, m))
        mailer.due_trivia_reminders()
        mailer.due_trivia_reminders()                              # runs every second: must not repeat
        got = {e["user_id"] for e in emails("trivia_soon")}
        self.assertEqual(got, {u["id"] for u in users})            # winning side only, not the loser

    def test_reminder_respects_opt_out(self):
        m, users, _ = self._match()
        db.run("UPDATE users SET notify_optional=0 WHERE id=?", (users[0]["id"],))
        db.run("UPDATE matches SET trivia_start_at=? WHERE id=?", (int(time.time()) + 200, m))
        mailer.due_trivia_reminders()
        self.assertEqual(len(emails("trivia_soon")), 2)

    async def test_winners_get_prize_email(self):
        m, users, _ = self._match()
        ok, msg = game.start_game(m)
        self.assertTrue(ok, msg)
        tasks = [asyncio.create_task(bot(m, u["id"], lambda g: ("right", 0.02))) for u in users]
        await asyncio.wait_for(game.ACTIVE[m].task, timeout=60)
        for t in tasks:
            t.cancel()
        self.assertEqual(len(emails("prize_won")), 3)
        self.assertEqual(len({e["user_id"] for e in emails("prize_won")}), 3)


if __name__ == "__main__":
    unittest.main()


class FakeSMTP:
    log = []

    def __init__(self, host, port, timeout=None):
        FakeSMTP.log.append(("connect", host, port))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        FakeSMTP.log.append(("starttls",))

    def login(self, u, p):
        FakeSMTP.log.append(("login", u, p))

    def send_message(self, m):
        FakeSMTP.log.append(("send", m["To"], m["From"], m["Subject"]))


class SmtpPath(unittest.TestCase):
    def test_uses_tls_login_and_sends(self):
        old = (mailer.smtplib.SMTP, config.SMTP_USER, config.SMTP_PASSWORD)
        mailer.smtplib.SMTP, config.SMTP_USER, config.SMTP_PASSWORD = FakeSMTP, "me@gmail.com", "apppass"
        try:
            FakeSMTP.log.clear()
            mailer.send_now("fan@x.com", "Hello", "<p>x</p>", "x")
        finally:
            mailer.smtplib.SMTP, config.SMTP_USER, config.SMTP_PASSWORD = old
        kinds = [e[0] for e in FakeSMTP.log]
        self.assertEqual(kinds, ["connect", "starttls", "login", "send"])      # TLS before the password is sent
        self.assertEqual(FakeSMTP.log[0][1:], ("smtp.gmail.com", 587))
        self.assertIn("me@gmail.com", FakeSMTP.log[3][2])
