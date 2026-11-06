import hashlib
import hmac
import json
import time
import unittest
from starlette.requests import Request
from app.reseller_v3 import shoppex_signature_details,identify_reseller,SUCCESS_PREFIX,NO_CREDITS


def signed_request(body, secret, delivery_id, timestamp=None):
    raw=json.dumps(body,separators=(",",":")).encode()
    stamp=str(timestamp if timestamp is not None else int(time.time()))
    digest=hmac.new(
        secret.encode(),delivery_id.encode()+b"."+stamp.encode()+b"."+raw,
        hashlib.sha256
    ).hexdigest()
    headers=[
        (b"content-type",b"application/json"),
        (b"x-shoppex-timestamp",stamp.encode()),
        (b"x-shoppex-signature-v2",f"v1,t={stamp},h={digest}".encode()),
        (b"x-shoppex-delivery-id",delivery_id.encode()),
    ]
    return Request({"type":"http","method":"POST","headers":headers}),raw


class ShoppexResellerV3Tests(unittest.TestCase):
    def test_matches_only_credential_owner(self):
        req,raw=signed_request({"order_id":"invoice-1","quantity":5},"a"*40,"delivery-1")
        details=shoppex_signature_details(req,raw)
        self.assertEqual(identify_reseller(details,raw,[
            {"user_id":"r1","signing_secret":"b"*40},
            {"user_id":"r2","signing_secret":"a"*40},
        ]),("r2","delivery-1"))

    def test_wrong_secret_is_not_authenticated(self):
        req,raw=signed_request({"order_id":"invoice-1","quantity":1},"wrong"*9,"delivery-2")
        self.assertIsNone(identify_reseller(shoppex_signature_details(req,raw),raw,[
            {"user_id":"r1","signing_secret":"right"*9}
        ]))

    def test_tamper_rejected(self):
        req,raw=signed_request({"order_id":"invoice-1","quantity":1},"a"*40,"delivery-3")
        changed=raw.replace(b'"quantity":1',b'"quantity":100')
        self.assertIsNone(identify_reseller(shoppex_signature_details(req,changed),changed,[
            {"user_id":"r1","signing_secret":"a"*40}
        ]))

    def test_expired_timestamp_rejected(self):
        req,raw=signed_request({"order_id":"invoice-1","quantity":5},"a"*40,"delivery-4",int(time.time())-3600)
        self.assertIsNone(shoppex_signature_details(req,raw))

    def test_ambiguous_secrets_fail_closed(self):
        req,raw=signed_request({"order_id":"invoice-1","quantity":2},"a"*40,"delivery-5")
        self.assertIsNone(identify_reseller(shoppex_signature_details(req,raw),raw,[
            {"user_id":"r1","signing_secret":"a"*40},
            {"user_id":"r2","signing_secret":"a"*40}
        ]))

    def test_shoppex_plaintext(self):
        self.assertEqual(SUCCESS_PREFIX+"3H76-68PW-Z66N-6HGT",
            "Thank you for your purchase ❤️\\n"
            "please use our redeem panel below to redeem your key, follow the steps provided.\\n"
            "https://memberix.live/redeem     KEY: 3H76-68PW-Z66N-6HGT"
        )
        self.assertEqual(NO_CREDITS,
            "Sorry but this reseller does not have the amount of credits to make this order, "
            "please contact support on this store or join discord.gg/saphire")
