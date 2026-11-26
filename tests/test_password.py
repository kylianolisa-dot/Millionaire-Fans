import unittest

from app import config


class PasswordLimit(unittest.TestCase):
    def test_limit_is_50(self):
        self.assertEqual(config.MAX_PASSWORD_LEN, 50)
