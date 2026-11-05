import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.guild_stock import validate_guild_id, validate_guild_payload
from app.main import app

GUILD_ID = "1521285253204021389"
EXAMPLE = {
    "guild_id": GUILD_ID,
    "offline_members": 9580,
    "online_members": 1092,
    "success": True,
    "total": 10672,
}


class GuildStockTests(unittest.TestCase):
    def test_valid_discord_snowflake(self):
        self.assertEqual(validate_guild_id(GUILD_ID), GUILD_ID)

    def test_reject_invalid_ids_before_provider(self):
        for value in ("", "12345", "1234x6789012345678", "0"*18,
                      "99999999999999999999", GUILD_ID + "0", "-12345678901234567"):
            with self.subTest(value=value), self.assertRaises(HTTPException):
                validate_guild_id(value)

    def test_provider_payload_is_mirrored(self):
        self.assertEqual(validate_guild_payload(EXAMPLE, GUILD_ID), EXAMPLE)

    def test_wrong_guild_or_inconsistent_totals_rejected(self):
        for updates in ({"guild_id": "123"}, {"total": 5},
                        {"offline_members": -1}, {"success": False},
                        {"online_members": "1092"}):
            with self.subTest(changes=updates), self.assertRaises(ValueError):
                validate_guild_payload({**EXAMPLE, **updates}, GUILD_ID)

    def test_invalid_route_never_calls_provider(self):
        with patch("app.guild_stock.httpx.AsyncClient") as client:
            with TestClient(app) as test_client:
                response = test_client.get("/v2/stock/members/check/guild?guild_id=invalid")
            self.assertEqual(response.status_code, 422)
            client.assert_not_called()

    def test_success_route_and_cors(self):
        with patch("app.main.get_guild_stock", new=AsyncMock(return_value=EXAMPLE)):
            with TestClient(app) as client:
                response = client.get("/v2/stock/members/check/guild?guild_id=" + GUILD_ID)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), EXAMPLE)
        self.assertEqual(response.headers.get("access-control-allow-origin"), "*")


if __name__ == "__main__":
    unittest.main()
