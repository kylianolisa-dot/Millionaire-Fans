import os, tempfile, time, unittest

os.environ["FM_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
os.environ["FM_UPLOADS"] = tempfile.mkdtemp()
from app import config, db, homepage as hp  # noqa: E402

db.init() if hasattr(db, "init") else None


class Home(unittest.TestCase):
    def test_clean_url_blocks_bad_schemes(self):
        for bad in ["javascript:alert(1)", "http://x.com/a.png", "data:image/png;base64,AA", "//evil.com/a.png", "/uploads/../etc/passwd", '/uploads/a"onerror=x']:
            with self.assertRaises(hp.HomeError, msg=bad):
                hp.clean_url(bad)
        self.assertEqual(hp.clean_url("/uploads/a.png"), "/uploads/a.png")
        self.assertEqual(hp.clean_url("https://x.com/a.png"), "https://x.com/a.png")
        self.assertEqual(hp.clean_url(""), "")
        with self.assertRaises(hp.HomeError):
            hp.clean_url("", required=True)

    def test_clean_link(self):
        for bad in ["javascript:alert(1)", "http://x.com", "data:text/html,x"]:
            with self.assertRaises(hp.HomeError):
                hp.clean_link(bad)
        self.assertEqual(hp.clean_link("#/matches"), "#/matches")

    def test_upload_sniffing(self):
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 50
        self.assertTrue(hp.save_upload(png).startswith("/uploads/"))
        for bad in [b"<svg xmlns='http://www.w3.org/2000/svg'><script>1</script></svg>", b"<html>", b"GIF89a....", b""]:
            with self.assertRaises(hp.HomeError):
                hp.save_upload(bad)
        with self.assertRaises(hp.HomeError):
            hp.save_upload(b"\x89PNG\r\n\x1a\n" + b"0" * (config.MAX_UPLOAD_BYTES + 1))

    def test_winner_needs_consent_and_valid_fields(self):
        good = {"name": "Ada Obi", "team": "Chelsea", "amount_kobo": 500000, "won_on": int(time.time()) - 86400, "consent": True}
        with self.assertRaises(hp.HomeError):
            hp.save_winner({**good, "consent": False})
        with self.assertRaises(hp.HomeError):
            hp.save_winner({k: v for k, v in good.items() if k != "consent"})
        with self.assertRaises(hp.HomeError):
            hp.save_winner({**good, "amount_kobo": 0})
        with self.assertRaises(hp.HomeError):
            hp.save_winner({**good, "won_on": int(time.time()) + 10 * 86400})
        wid = hp.save_winner(good)
        self.assertEqual(hp.public_home()["winners"][0]["name"], "Ada Obi")
        hp.save_winner({**good, "active": False}, wid)
        self.assertEqual(hp.public_home()["winners"], [])

    def test_fallbacks(self):
        h = hp.public_home()
        self.assertEqual(len(h["slides"]), 4)
        self.assertTrue(all(s["image_url"].startswith("/slides/") for s in h["slides"]))
        self.assertTrue(all(l["logo_url"] == "" for l in h["leagues"]))


if __name__ == "__main__":
    unittest.main()
