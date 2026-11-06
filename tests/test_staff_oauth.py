import hashlib
import os
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.staff_oauth import COOKIE,oauth_config,state_hash

class StaffOAuthTests(unittest.TestCase):
    def test_state_hash_is_sha256(self):
        state="x"*48
        self.assertEqual(state_hash(state),hashlib.sha256(state.encode()).hexdigest())
        self.assertEqual(len(state_hash(state)),64)

    def test_config_requires_secret(self):
        with patch.dict(os.environ,{"STAFF_DISCORD_CLIENT_ID":"1561864016391970816",
                   "STAFF_DISCORD_CLIENT_SECRET":"", "DISCORD_CLIENT_SECRET":""}):
            self.assertIsNone(oauth_config())

    def test_config_rejects_http_callback(self):
        with patch.dict(os.environ,{"STAFF_DISCORD_CLIENT_ID":"1561864016391970816",
                   "STAFF_DISCORD_CLIENT_SECRET":"placeholder-test-value",
                   "STAFF_OAUTH_REDIRECT_URI":"http://example.com/v3/staff/auth/callback"}):
            self.assertIsNone(oauth_config())

    def test_unconfigured_login_fails_closed(self):
        with patch.dict(os.environ,{"STAFF_DISCORD_CLIENT_ID":"",
            "STAFF_DISCORD_CLIENT_SECRET":"","DISCORD_CLIENT_ID":"",
            "DISCORD_CLIENT_SECRET":""}):
            client=TestClient(app)
            self.assertEqual(client.get("/v3/staff/auth/start?ticket=abc").status_code,503)
            self.assertEqual(client.get("/v3/staff/auth/callback?state=abc&code=abc").status_code,503)

    def test_api_route_is_not_a_key_mint_endpoint(self):
        with patch.dict(os.environ,{"STAFF_DISCORD_CLIENT_ID":"",
            "STAFF_DISCORD_CLIENT_SECRET":"","DISCORD_CLIENT_ID":"",
            "DISCORD_CLIENT_SECRET":""}):
            client=TestClient(app)
            self.assertEqual(client.post("/v3/staff/auth/callback",json={"userid":"1521284328485556357"}).status_code,405)
