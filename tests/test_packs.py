import json
import unittest

from app import db, seed
from tests.test_core import fresh_db


class Packs(unittest.TestCase):
    def test_pack_is_valid_and_loads_once(self):
        fresh_db()
        before = db.one("SELECT COUNT(*) n FROM questions")["n"]
        n = seed.load_packs()
        self.assertGreaterEqual(n, 95)
        self.assertEqual(seed.load_packs(), 0)                      # second start adds nothing
        self.assertEqual(db.one("SELECT COUNT(*) n FROM questions")["n"], before + n)

    def test_every_question_is_well_formed(self):
        texts = set()
        for f in seed.PACK_DIR.glob("*.json"):
            for q in json.loads(f.read_text(encoding="utf-8")):
                self.assertEqual(len(q["options"]), 4)
                self.assertEqual(len(set(q["options"])), 4)
                self.assertIn(q["correct"], range(4))
                self.assertNotIn(seed._norm(q["text"]), texts)
                texts.add(seed._norm(q["text"]))
                self.assertTrue(q["text"].endswith("?"))


if __name__ == "__main__":
    unittest.main()
