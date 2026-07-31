import unittest

from bridge import _relative_delta


class RelativeEncoderTests(unittest.TestCase):
    def test_cw_mode_decodes_zero_as_counter_clockwise(self):
        self.assertEqual(_relative_delta("relative_cw", 0), -1)

    def test_cw_mode_decodes_127_as_clockwise(self):
        self.assertEqual(_relative_delta("relative_cw", 127), 1)

    def test_cw_mode_ignores_other_values(self):
        self.assertIsNone(_relative_delta("relative_cw", 64))

    def test_existing_signed_bit_mode_is_unchanged(self):
        self.assertEqual(_relative_delta("relative", 1), 1)
        self.assertEqual(_relative_delta("relative", 127), -1)
        self.assertIsNone(_relative_delta("relative", 0))


if __name__ == "__main__":
    unittest.main()
