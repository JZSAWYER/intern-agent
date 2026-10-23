import math
import unittest
from unittest.mock import patch

from intern_personal.core import Invalid
from intern_personal.turn_limits import deadline, seconds


class TurnLimitTests(unittest.TestCase):
    def test_new_default_and_explicit_zero_have_no_cutoff(self):
        self.assertEqual(deadline({}), math.inf)
        self.assertEqual(deadline({'turn_timeout_seconds': 0}), math.inf)

    def test_explicit_positive_limit_is_preserved(self):
        with patch('intern_personal.turn_limits.time.monotonic', return_value=100):
            self.assertEqual(deadline({'turn_timeout_seconds': 900}), 1000)
            self.assertEqual(deadline({'turn_timeout_seconds': 1.5}), 101.5)

    def test_invalid_limit_is_rejected(self):
        for value in [None, True, False, '900', -1, math.inf, -math.inf, math.nan]:
            with self.subTest(value=value), self.assertRaises(Invalid):
                seconds({'turn_timeout_seconds': value})
