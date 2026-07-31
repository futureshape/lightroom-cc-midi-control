import unittest

from bridge import Bridge, _numeric_value, _relative_delta


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


class LightroomValueTests(unittest.IsolatedAsyncioTestCase):
    def test_extracts_lightroom_single_item_list(self):
        self.assertEqual(_numeric_value([-1.3], "Exposure"), -1.3)

    def test_extracts_observer_value(self):
        self.assertEqual(_numeric_value({"Exposure": [0.75]}, "Exposure"), 0.75)

    async def test_current_value_uses_lightroom_value_instead_of_zero(self):
        class FakeLightroom:
            async def call(self, method, param):
                self.request = (method, param)
                return [-1.3]

        bridge = object.__new__(Bridge)
        bridge._lr_cache = {}
        bridge._lr = FakeLightroom()

        self.assertEqual(await bridge._current_value("Exposure"), -1.3)
        self.assertEqual(bridge._lr_cache["Exposure"], -1.3)

    async def test_invalid_response_does_not_fall_back_to_zero(self):
        class FakeLightroom:
            async def call(self, method, param):
                return None

        bridge = object.__new__(Bridge)
        bridge._lr_cache = {}
        bridge._lr = FakeLightroom()

        with self.assertRaisesRegex(RuntimeError, "Could not read current"):
            await bridge._current_value("Exposure")


if __name__ == "__main__":
    unittest.main()
