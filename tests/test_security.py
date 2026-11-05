import hashlib
import hmac
import json
import os
import time
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.main import app, authenticate, extract, provider_order_id


def signed_request(body, kind="offline", delivery="delivery-test-001", secret="test-secret"):
    timestamp = str(int(time.time()))
    digest = hmac.new(secret.encode(), (delivery+"."+timestamp+".").encode()+body, hashlib.sha256).hexdigest()
    headers = [
        (b"x-shoppex-delivery", delivery.encode()),
        (b"x-shoppex-timestamp", timestamp.encode()),
        (b"x-shoppex-signature-v2", ("v1,t="+timestamp+",h="+digest).encode()),
        (b"content-type", b"application/json"),
    ]
    return Request({"type":"http", "method":"POST", "path":"/v2/resellers/order/members/"+kind, "headers":headers})


class SecurityTests(unittest.TestCase):
    def test_signed_request_is_accepted(self):
        payload = b'{"order":"INV-TEST","product":"P1","quantity":1}'
        with patch.dict(os.environ, {"SHOPPEX_OFFLINE_SIGNING_SECRET":"test-secret"}):
            authenticate(signed_request(payload), payload, "offline")

    def test_body_tampering_is_rejected(self):
        payload = b'{"order":"INV-TEST","product":"P1","quantity":1}'
        with patch.dict(os.environ, {"SHOPPEX_OFFLINE_SIGNING_SECRET":"test-secret"}):
            with self.assertRaises(HTTPException) as exc:
                authenticate(signed_request(payload), payload.replace(b'"quantity":1',b'"quantity":2'), "offline")
            self.assertEqual(exc.exception.status_code,401)

    def test_cross_product_signature_is_rejected(self):
        payload = b'{"order":"INV-TEST","product":"P1","quantity":1}'
        with patch.dict(os.environ, {"SHOPPEX_ONLINE_SIGNING_SECRET":"different-secret"}):
            with self.assertRaises(HTTPException) as exc:
                authenticate(signed_request(payload), payload, "online")
            self.assertEqual(exc.exception.status_code,401)

    def test_stale_signature_is_rejected(self):
        body=b'{}'
        timestamp=str(int(time.time())-1000)
        delivery="delivery-test-001"
        digest=hmac.new(b"test-secret",(delivery+"."+timestamp+".").encode()+body,hashlib.sha256).hexdigest()
        request=Request({"type":"http","method":"POST","path":"/","headers":[
            (b"x-shoppex-delivery",delivery.encode()),(b"x-shoppex-timestamp",timestamp.encode()),
            (b"x-shoppex-signature-v2",("v1,t="+timestamp+",h="+digest).encode())]})
        with patch.dict(os.environ,{"SHOPPEX_OFFLINE_SIGNING_SECRET":"test-secret"}):
            with self.assertRaises(HTTPException) as exc:
                authenticate(request,body,"offline")
            self.assertEqual(exc.exception.status_code,401)

    def test_payload_custom_fields(self):
        body={"order":"INV-TEST","product":"P1","quantity":100,
              "custom_fields":[{"name":"Discord Server","value":"https://discord.gg/example"}]}
        self.assertEqual(extract(body),("INV-TEST","P1",100,"https://discord.gg/example"))

    def test_provider_order_id_stable(self):
        self.assertEqual(provider_order_id("A","B","online"),provider_order_id("A","B","online"))
        self.assertNotEqual(provider_order_id("A","B","online"),provider_order_id("A","B","offline"))

    def test_invalid_quantities(self):
        for quantity in (0,-1,100001,True,"1.5"):
            with self.assertRaises(HTTPException):
                extract({"order":"A","quantity":quantity})

    def test_404_design_and_headers(self):
        with TestClient(app) as c:
            response=c.get("/unlisted-page")
            self.assertEqual(response.status_code,404)
            self.assertIn("Access Declined",response.text)
            self.assertEqual(response.headers["X-Frame-Options"],"DENY")
            self.assertEqual(response.headers["Cache-Control"],"no-store")
            self.assertEqual(c.get("/_liveness").status_code,200)

    def test_api_denies_unsigned_order(self):
        with TestClient(app) as c:
            response=c.post("/v2/resellers/order/members/offline",json={"order":"T","quantity":1})
            self.assertIn(response.status_code,(401,503))


if __name__=="__main__":
    unittest.main()
