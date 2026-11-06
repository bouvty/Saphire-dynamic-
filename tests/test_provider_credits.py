import unittest
from app.main import valid_provider_credits, format_delivery_message

class ProviderCreditsTests(unittest.TestCase):
    def test_accepts_valid_credits(self):
        self.assertEqual(valid_provider_credits({"credits_remaining":4995}),4995)
        self.assertEqual(valid_provider_credits({"credits_remaining":0}),0)

    def test_rejects_missing_or_invalid_balance(self):
        for value in (None,-1,True,"4995",3.5,2**63):
            with self.subTest(value=value):
                self.assertIsNone(valid_provider_credits({"credits_remaining":value}))
        self.assertIsNone(valid_provider_credits({}))

    def test_shoppex_plain_text_remains_unchanged(self):
        result = format_delivery_message("EXAMPLE")
        self.assertIn("Thank you for using saphire",result)
        self.assertIn("KEY: EXAMPLE",result)
