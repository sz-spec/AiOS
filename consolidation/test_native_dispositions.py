import unittest

from check_native_dispositions import validate


class NativeDispositionTests(unittest.TestCase):
    def test_every_open_native_path_has_one_valid_disposition(self):
        result = validate()
        self.assertEqual(result["paths"], 115)
        self.assertEqual(sum(result["dispositions"].values()), 115)
        self.assertEqual(result["states"]["implementation-open"], 5)


if __name__ == "__main__":
    unittest.main()
