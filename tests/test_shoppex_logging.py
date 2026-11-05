import unittest
from app.main import redact_private, extract

class ShoppexOrderTests(unittest.TestCase):
    def test_store_full_order_fields(self):
        body={"order":"INV-EXAMPLE","product":"prod_2001","quantity":100,
              "customer":{"email":"test@example.com"},"custom_fields":{"Discord Server":"https://discord.gg/example"},
              "line_items":[{"id":"item-1","quantity":100}],"total":1.25}
        self.assertEqual(extract(body)[3],"https://discord.gg/example")
        self.assertEqual(redact_private(body)["customer"]["email"],"test@example.com")
        self.assertEqual(redact_private(body)["line_items"][0]["quantity"],100)
    def test_never_store_embedded_secrets(self):
        body={"token":"a","customer":{"password":"secret","email":"test@example.com"},
              "extra":[{"api_key":"b"}]}
        safe=redact_private(body)
        self.assertEqual(safe["token"],"[REDACTED]")
        self.assertEqual(safe["customer"]["password"],"[REDACTED]")
        self.assertEqual(safe["extra"][0]["api_key"],"[REDACTED]")
