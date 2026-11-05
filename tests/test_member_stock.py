import unittest
from app.member_stock import validate_stock, public_stock, SYNC_SECONDS

class StockValidationTests(unittest.TestCase):
    def test_refresh_exactly_fifteen_minutes(self):
        self.assertEqual(SYNC_SECONDS, 900)

    def test_valid_provider_stock(self):
        self.assertEqual(validate_stock({"offline_members":9580,"online_members":1092,"success":True}),(9580,1092))

    def test_zero_is_valid(self):
        self.assertEqual(validate_stock({"offline_members":0,"online_members":0,"success":True}),(0,0))

    def test_missing_or_invalid_counts_rejected(self):
        for payload in ({"success":False,"offline_members":1,"online_members":1},
            {"success":True,"offline_members":None,"online_members":1},
            {"success":True,"offline_members":1,"online_members":True},
            {"success":True,"offline_members":-1,"online_members":1},
            {"success":True,"offline_members":1},
            {"offline_members":2,"online_members":3}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                validate_stock(payload)


    def test_successful_public_cache_has_exact_json_keys(self):
        import asyncio
        import json

        class Connection:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def fetchrow(self, query):
                return {
                    "offline_members": 9580,
                    "online_members": 1092,
                    "success": True,
                }

        class Pool:
            def acquire(self):
                return Connection()

        response = asyncio.run(public_stock(Pool()))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            json.loads(response.body),
            {"offline_members": 9580, "online_members": 1092, "success": True},
        )

    def test_no_database_is_not_fake_success(self):
        import asyncio
        resp=asyncio.run(public_stock(None))
        self.assertEqual(resp.status_code,503)
        self.assertIn(b'"success":false',resp.body)
