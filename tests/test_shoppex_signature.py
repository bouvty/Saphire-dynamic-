import hashlib
import hmac
import time
import unittest
from unittest.mock import patch
from starlette.requests import Request
from fastapi import HTTPException
from app.main import authenticate

class ShoppexAuthenticationTests(unittest.TestCase):
    def test_idempotency_header_signature(self):
        body=b'{"order_id":"example","quantity":1}'
        stamp=str(int(time.time()))
        delivery="idempotency-123"
        secret="example-secret"
        signature=hmac.new(secret.encode(),delivery.encode()+b"."+stamp.encode()+b"."+body,hashlib.sha256).hexdigest()
        headers=[
            (b"x-shoppex-signature-v2",f"v1,t={stamp},h={signature}".encode()),
            (b"x-shoppex-timestamp",stamp.encode()),
            (b"x-shoppex-idempotency-key",delivery.encode())
        ]
        request=Request({"type":"http","method":"POST","headers":headers})
        with patch.dict("os.environ",{"SHOPPEX_OFFLINE_SIGNING_SECRET":secret}):
            authenticate(request,body,"offline")
        self.assertEqual(request.state.shoppex_delivery_id,delivery)

    def test_invalid_signature_rejected(self):
        body=b'{"order_id":"example","quantity":1}'
        stamp=str(int(time.time()))
        headers=[
            (b"x-shoppex-signature-v2",f"v1,t={stamp},h={'0'*64}".encode()),
            (b"x-shoppex-timestamp",stamp.encode()),
            (b"x-shoppex-idempotency-key",b"idempotency-123")
        ]
        request=Request({"type":"http","method":"POST","headers":headers})
        with patch.dict("os.environ",{"SHOPPEX_OFFLINE_SIGNING_SECRET":"example-secret"}):
            with self.assertRaises(HTTPException):
                authenticate(request,body,"offline")
