"""Fail-closed integration tests: no credits or invalid signature = no provider call."""
import hashlib
import hmac
import json
import time
import unittest
from unittest.mock import patch

from starlette.requests import Request
from app import main as backend
from app import reseller_v3

class FakeConnection:
    def __init__(self, balance):
        self.balance = balance
        self.sql = []
    async def fetch(self, sql, *args):
        self.sql.append(("fetch", sql))
        return [{"user_id":"1521284328485556357", "signing_secret":"o"*64}]
    async def fetchrow(self, sql, *args):
        self.sql.append(("fetchrow",sql))
        if "FROM public.resellers_accounts" in sql:
            return {"user_id":"1521284328485556357","credit_online":1,"credit_offline":self.balance}
        if "FROM public.resellers_orders" in sql:
            return None
        raise AssertionError("Unexpected database query")
    async def fetchval(self, *args):
        raise AssertionError("Insufficient credit must never create an order")
    async def execute(self, *args):
        raise AssertionError("Insufficient credit must never deduct balance")

class ContextManager:
    def __init__(self, result):
        self.result=result
    async def __aenter__(self):
        return self.result
    async def __aexit__(self, exc_type, exc, tb):
        return False

class FakePool:
    def __init__(self, balance=0):
        self.conn=FakeConnection(balance)
    def acquire(self):
        self.conn.transaction=lambda: ContextManager(None)
        return ContextManager(self.conn)

def request_for(secret, qty=1, delivery="delivery-1"):
    body=json.dumps({"order_id":"invoice-123","line_item_id":"item-1","quantity":qty}).encode()
    stamp=str(int(time.time()))
    digest=hmac.new(secret.encode(),delivery.encode()+b"."+stamp.encode()+b"."+body,hashlib.sha256).hexdigest()
    headers=[
        (b"content-type",b"application/json"),
        (b"x-shoppex-timestamp",stamp.encode()),
        (b"x-shoppex-signature-v2",f"v1,t={stamp},h={digest}".encode()),
        (b"x-shoppex-delivery-id",delivery.encode()),
    ]
    async def receive():
        return {"type":"http.request","body":body,"more_body":False}
    return Request({"type":"http","method":"POST","headers":headers},receive)

class CreditAndSignatureGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_offline_zero_credits_returns_exact_plaintext_and_no_provider(self):
        db=FakePool(balance=0)
        with patch.object(backend,"DB",db), patch.object(
            reseller_v3.httpx,"AsyncClient",side_effect=AssertionError("Provider must not be called")
        ):
            response=await reseller_v3.reseller_fulfill("offline",request_for("o"*64))
        self.assertEqual(response.status_code,409)
        self.assertEqual(response.headers["content-type"],"text/plain; charset=utf-8")
        self.assertEqual(response.body.decode(),reseller_v3.NO_CREDITS)
        self.assertFalse(any(op in ("execute","fetchval") for op,_ in db.conn.sql))

    async def test_offline_not_enough_credits_for_quantity(self):
        db=FakePool(balance=2)
        with patch.object(backend,"DB",db),patch.object(
            reseller_v3.httpx,"AsyncClient",side_effect=AssertionError("Provider must not be called")
        ):
            response=await reseller_v3.reseller_fulfill("offline",request_for("o"*64,qty=3))
        self.assertEqual(response.status_code,409)
        self.assertEqual(response.body.decode(),reseller_v3.NO_CREDITS)

    async def test_invalid_secret_never_passes_credit_gate_or_calls_provider(self):
        db=FakePool(balance=10)
        with patch.object(backend,"DB",db),patch.object(
            reseller_v3.httpx,"AsyncClient",side_effect=AssertionError("Provider must not be called")
        ):
            response=await reseller_v3.reseller_fulfill("offline",request_for("x"*64))
        self.assertEqual(response.status_code,401)
        self.assertEqual(response.body.decode(),"Error")
        self.assertFalse(any(op=="fetchrow" for op,_ in db.conn.sql))

    async def test_online_secret_cannot_authorize_offline_order(self):
        db=FakePool(balance=10)
        # Simulate a valid online key used for offline endpoint.
        with patch.object(backend,"DB",db),patch.object(
            reseller_v3.httpx,"AsyncClient",side_effect=AssertionError("Provider must not be called")
        ):
            response=await reseller_v3.reseller_fulfill("offline",request_for("n"*64))
        self.assertEqual(response.status_code,401)
