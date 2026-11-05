import asyncio
import unittest
from unittest.mock import patch

import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app.guild_stock import validate_guild_id, validate_guild_payload, verify_guild_with_discord, get_guild_stock, parse_invite_code, _cache
from app.main import app

GUILD_ID = "1521285253204021389"
EXAMPLE = {"guild_id": GUILD_ID, "offline_members": 9580, "online_members": 1092, "success": True, "total": 10672}

class GuildStockTests(unittest.TestCase):
    def test_valid_discord_snowflake(self):
        self.assertEqual(validate_guild_id(GUILD_ID), GUILD_ID)

    def test_invalid_ids_rejected(self):
        for value in ("", "12345", "1234x6789012345678", "0"*18, "99999999999999999999", GUILD_ID+"0"):
            with self.subTest(value=value), self.assertRaises(HTTPException):
                validate_guild_id(value)

    def test_mirrors_provider_payload(self):
        self.assertEqual(validate_guild_payload(EXAMPLE, GUILD_ID), EXAMPLE)

    def test_rejects_mismatched_provider_data(self):
        for fields in ({"guild_id":"123"},{"total":2},{"success":False},{"offline_members":-1}):
            with self.subTest(fields=fields),self.assertRaises(ValueError):
                validate_guild_payload({**EXAMPLE,**fields},GUILD_ID)

    def test_invite_parser_accepts_only_discord_hosts(self):
        self.assertEqual(parse_invite_code("https://discord.gg/abc123"), "abc123")
        self.assertEqual(parse_invite_code("discord.com/invite/abc123"), "abc123")
        for value in ("https://evil.example/invite/abc", "discord.gg.evil.com/abc",
                      "https://discord.gg/", "https://discord.gg/a/b"):
            with self.subTest(value=value), self.assertRaises(HTTPException):
                parse_invite_code(value)

    def test_live_invite_verified_before_provider(self):
        calls=[]
        def handle(request):
            calls.append(str(request.url))
            if "/invites/" in str(request.url):
                return httpx.Response(200,json={"guild":{"id":GUILD_ID,"name":"My Server"}})
            return httpx.Response(200,json=EXAMPLE)
        original_client=httpx.AsyncClient
        transport=httpx.MockTransport(handle)
        async def run():
            _cache.clear()
            with patch("app.guild_stock.httpx.AsyncClient",side_effect=lambda *a,**kw:original_client(transport=transport)):
                return await get_guild_stock("https://discord.gg/abc123")
        data=asyncio.run(run())
        self.assertEqual(data["guild_id"],GUILD_ID)
        self.assertEqual(data["guild_name"],"My Server")
        self.assertEqual(len(calls),2)
        self.assertIn("discord.com/api/v10/invites/abc123",calls[0])

    def test_no_bot_token_is_not_false_verification(self):
        async def run():
            async with httpx.AsyncClient() as client:
                with patch.dict("os.environ", {"DISCORD_BOT_TOKEN": ""}):
                    await verify_guild_with_discord(client,GUILD_ID)
        # A public preview is checked even if there is no bot configured.
        # An unreachable/undiscoverable guild must not be silently approved.
        original_client=httpx.AsyncClient
        transport=httpx.MockTransport(lambda req:httpx.Response(404))
        async def probe():
            with patch.dict("os.environ", {"DISCORD_BOT_TOKEN": ""}):
                with patch("app.guild_stock.httpx.AsyncClient",side_effect=lambda *a,**kw:original_client(transport=transport)):
                    async with httpx.AsyncClient() as client:
                        await verify_guild_with_discord(client,GUILD_ID)
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(probe())
        self.assertEqual(ctx.exception.status_code,404)

    def test_verified_guild_calls_discord_before_provider(self):
        calls=[]
        def handle(request):
            calls.append(str(request.url))
            if request.url.host=="discord.com":
                return httpx.Response(200,json={"id":GUILD_ID,"name":"Example Guild"})
            return httpx.Response(200,json=EXAMPLE)
        transport=httpx.MockTransport(handle)
        original_client=httpx.AsyncClient
        def client_factory(*args,**kwargs):
            return original_client(transport=transport,timeout=kwargs.get("timeout"))
        async def run():
            _cache.clear()
            with patch.dict("os.environ",{"DISCORD_BOT_TOKEN":"test-token"}):
                with patch("app.guild_stock.httpx.AsyncClient",side_effect=client_factory):
                    return await get_guild_stock(GUILD_ID)
        data=asyncio.run(run())
        self.assertEqual(calls[0],f"https://discord.com/api/v10/guilds/{GUILD_ID}")
        self.assertEqual(len(calls),2)
        self.assertEqual(data["guild_name"],"Example Guild")
        self.assertEqual(data["total"],10672)

    def test_inaccessible_guild_never_reaches_provider(self):
        calls=[]
        def handle(request):
            calls.append(str(request.url))
            return httpx.Response(404,json={"message":"Unknown Guild"})
        transport=httpx.MockTransport(handle)
        original_client=httpx.AsyncClient
        def factory(*args,**kwargs):
            return original_client(transport=transport)
        async def run():
            _cache.clear()
            with patch.dict("os.environ",{"DISCORD_BOT_TOKEN":"test-token"}):
                with patch("app.guild_stock.httpx.AsyncClient",side_effect=factory):
                    await get_guild_stock(GUILD_ID)
        with self.assertRaises(HTTPException):
            asyncio.run(run())
        self.assertEqual(len(calls),2)
        self.assertTrue(all("discord.com" in url for url in calls))

    def test_invalid_route_never_contacts_provider(self):
        with patch("app.guild_stock.httpx.AsyncClient") as mocked:
            with TestClient(app) as client:
                response=client.get("/v2/stock/members/check/guild?guild_id=invalid")
            self.assertEqual(response.status_code,422)
            mocked.assert_not_called()

    def test_success_route_and_cors(self):
        from unittest.mock import AsyncMock
        with patch("app.main.get_guild_stock",new=AsyncMock(return_value={**EXAMPLE,"guild_name":"Example Guild"})):
            with TestClient(app) as client:
                response=client.get("/v2/stock/members/check/guild?guild_id="+GUILD_ID)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()["guild_name"],"Example Guild")
        self.assertEqual(response.headers.get("access-control-allow-origin"),"*")

if __name__=="__main__":
    unittest.main()
