import asyncio
import unittest

from app import betting, db, game, settings, settlement
from tests.test_core import bot, fast_settings, fresh_db, mk_match, mk_user


def qids(n):
    return [r["id"] for r in db.rows("SELECT id FROM questions WHERE active=1 ORDER BY id LIMIT ?", (n,))]


class ManualQuestions(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fresh_db()
        fast_settings()
        settings.update({"require_manual_questions": 1})

    def _settled(self, home_fans=1):
        users = [mk_user(n, 1_000_000) for n in "abc"[:home_fans]]
        d = mk_user("d", 1_000_000)
        a, m = users[0], mk_match()
        for u in users:
            betting.place_stake(u, m, "home", 100_000)
        betting.place_stake(d, m, "away", 100_000)
        self.home_users = users
        settlement.finish_match(m, 2, 0)
        return m, a

    def test_cannot_start_until_all_three_rounds_chosen(self):
        async def go():
            m, _ = self._settled()
            ok, msg = game.start_game(m)
            self.assertFalse(ok)
            self.assertIn("round 1, 2, 3", msg)
            q = qids(3)
            db.run("INSERT INTO match_questions VALUES(?,?,?,?)", (m, 1, 0, q[0]))
            db.run("INSERT INTO match_questions VALUES(?,?,?,?)", (m, 2, 0, q[1]))
            self.assertEqual(game.missing_questions(m), [3])
            self.assertFalse(game.start_game(m)[0])
        asyncio.run(go())

    async def test_game_uses_exactly_the_chosen_questions(self):
        m, a = self._settled(3)
        q = qids(3)
        for rn, qid in zip((1, 2, 3), q):
            db.run("INSERT INTO match_questions VALUES(?,?,?,?)", (m, rn, 0, qid))
        ok, msg = game.start_game(m)
        self.assertTrue(ok, msg)
        tasks = [asyncio.create_task(bot(m, u["id"], lambda g: ("right", 0.02))) for u in self.home_users]
        await asyncio.wait_for(game.ACTIVE[m].task, timeout=60)
        for t in tasks:
            t.cancel()
        used = [r["question_id"] for r in db.rows("SELECT question_id FROM rounds WHERE match_id=? AND attempt=0 ORDER BY round_no", (m,))]
        self.assertEqual(used, q)

    def test_retake_uses_chosen_question_else_random_unused(self):
        m, _ = self._settled()
        q = qids(5)
        db.run("INSERT INTO match_questions VALUES(?,?,?,?)", (m, 1, 0, q[0]))
        db.run("INSERT INTO match_questions VALUES(?,?,?,?)", (m, 1, 1, q[1]))
        self.assertEqual(game.pick_question(m, 1, 0)["id"], q[0])
        self.assertEqual(game.pick_question(m, 1, 1)["id"], q[1])
        r = game.pick_question(m, 1, 2)                       # not chosen: random, but never one already chosen
        self.assertNotIn(r["id"], q[:2])

    def test_retired_question_counts_as_missing(self):
        m, _ = self._settled()
        for rn, qid in zip((1, 2, 3), qids(3)):
            db.run("INSERT INTO match_questions VALUES(?,?,?,?)", (m, rn, 0, qid))
        db.run("UPDATE questions SET active=0 WHERE id=?", (qids(1)[0],))
        self.assertEqual(game.missing_questions(m), [1])


if __name__ == "__main__":
    unittest.main()
