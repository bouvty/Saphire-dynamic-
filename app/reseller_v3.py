"""Shoppex-signed, credit-funded reseller API. No direct key-generation endpoint."""
import hashlib
import hmac
import json
import logging
import os
import re
import time

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

router = APIRouter()
log = logging.getLogger("saphire.resellers.v3")

NO_CREDITS = ("Sorry but this reseller does not have the amount of credits to make this order, "
              "please contact support on this store or join discord.gg/saphire")
SUCCESS_PREFIX = ("Thank you for your purchase ❤️\n"
                  "please use our redeem panel below to redeem your key, follow the steps provided.\n"
                  "https://memberix.live/redeem     KEY: ")


def shoppex_signature_details(request, raw):
    """Return validated timestamp/hash and possible signed delivery IDs."""
    signature = request.headers.get("x-shoppex-signature-v2", "").strip()
    timestamp = request.headers.get("x-shoppex-timestamp", "").strip()
    parts = signature.split(",")
    fields = {}
    if parts and parts[0] == "v1":
        fields = dict(p.strip().split("=", 1) for p in parts[1:] if "=" in p)
    if len(signature) > 256 or not re.fullmatch(r"[a-fA-F0-9]{64}", fields.get("h", "")):
        return None
    if not timestamp.isdecimal() or timestamp != fields.get("t"):
        return None
    if abs(time.time() - int(timestamp)) > 300:
        return None
    candidates = [
        request.headers.get("x-shoppex-delivery"),
        request.headers.get("x-shoppex-delivery-id"),
        request.headers.get("x-shoppex-webhook-id"),
        request.headers.get("x-shoppex-idempotency-key"),
        request.headers.get("webhook-id"),
    ]
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        body = None
    if isinstance(body, dict):
        for key in ("delivery_id", "deliveryId", "webhook_id", "event_id", "line_item_id"):
            value = body.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                candidates.append(str(value))
        delivery = body.get("delivery")
        if isinstance(delivery, dict):
            for key in ("id", "uniqid"):
                value = delivery.get(key)
                if isinstance(value, (str, int)) and not isinstance(value, bool):
                    candidates.append(str(value))
    candidates = [v for v in dict.fromkeys(candidates) if isinstance(v, str) and 1 <= len(v) <= 128]
    if not candidates:
        return None
    return timestamp, fields["h"].lower(), candidates


def identify_reseller(details, raw, accounts):
    """Find exactly one verified reseller. Never trust a supplied reseller user ID."""
    if details is None:
        return None
    timestamp, signature, candidates = details
    matches = []
    for account in accounts:
        secret = account["signing_secret"]
        matched_id = None
        if not isinstance(secret, str) or len(secret) < 32:
            continue
        for delivery_id in candidates:
            expected = hmac.new(
                secret.encode(), delivery_id.encode() + b"." + timestamp.encode() + b"." + raw,
                hashlib.sha256
            ).hexdigest()
            if hmac.compare_digest(expected, signature):
                matched_id = delivery_id
        if matched_id is not None:
            matches.append((account["user_id"], matched_id))
    return matches[0] if len(matches) == 1 else None


def text_error(status=500, body="Error"):
    return PlainTextResponse(body, status_code=status)


async def reseller_fulfill(kind: str, request: Request):
    from app import main as backend
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        return text_error(415)
    content_length = request.headers.get("content-length")
    if content_length is not None and content_length.isdigit() and int(content_length) > 65536:
        return text_error(413)
    raw = await request.body()
    if len(raw) > 65536:
        return text_error(413)
    if backend.DB is None:
        return text_error(503)

    details = shoppex_signature_details(request, raw)
    if details is None:
        log.warning("v3 invalid Shoppex signing metadata (%s)", kind)
        return text_error(401)

    secret_field = "shoppex_signing_secret_online" if kind == "online" else "shoppex_signing_secret_offline"
    # Only the private backend database connection can read signing secrets.
    try:
        async with backend.DB.acquire() as conn:
            accounts = await conn.fetch(
                "SELECT user_id, " + secret_field + " AS signing_secret "
                "FROM public.resellers_accounts WHERE active=true LIMIT 1001"
            )
    except Exception:
        log.exception("v3 credential query failed")
        return text_error(503)
    if len(accounts) > 1000:
        log.error("v3 credential search limit reached")
        return text_error(503)

    verified = identify_reseller(details, raw, accounts)
    if verified is None:
        log.warning("v3 signature rejected kind=%s", kind)
        return text_error(401)
    user_id, signed_delivery_id = verified

    try:
        body = json.loads(raw)
        if not isinstance(body, dict):
            return text_error(422)
        order, line, quantity, _server = backend.extract(body)
    except Exception as exc:
        # The signature was checked before any order processing.
        log.info("v3 invalid Shoppex payload kind=%s error=%s", kind, type(exc).__name__)
        return text_error(422)
    if line == "default":
        line = signed_delivery_id
    invoice_ref = backend.pick(body, "invoice.id", "invoice.uniqid", "invoiceId", "invoice_id")
    if isinstance(invoice_ref, bool) or not isinstance(invoice_ref, (str, int)):
        invoice_ref = None
    if invoice_ref is not None:
        invoice_ref = str(invoice_ref)[:128]
    provider_order_id = "r3-" + hashlib.sha256(
        (user_id + "|" + order + "|" + line + "|" + kind).encode()
    ).hexdigest()[:24]
    balance_column = "credit_online" if kind == "online" else "credit_offline"
    row_id = None
    try:
        async with backend.DB.acquire() as conn:
            async with conn.transaction():
                account = await conn.fetchrow(
                    "SELECT user_id, credit_online, credit_offline FROM public.resellers_accounts "
                    "WHERE user_id=$1 AND active=true FOR UPDATE", user_id
                )
                if account is None:
                    return text_error(401)
                previous = await conn.fetchrow(
                    "SELECT id, status, quantity, provider_key, shoppex_delivery_id FROM public.resellers_orders "
                    "WHERE reseller_user_id=$1 AND shoppex_order_id=$2 AND line_item_id=$3 AND members_type=$4",
                    user_id, order, line, kind
                )
                if previous:
                    if previous["quantity"] != quantity or previous["shoppex_delivery_id"] != signed_delivery_id:
                        return text_error(409)
                    if previous["status"] == "success" and previous["provider_key"]:
                        return PlainTextResponse(SUCCESS_PREFIX + previous["provider_key"])
                    # Never re-call provider for a processing/unknown/failed order.
                    return text_error(503)
                if account[balance_column] < quantity:
                    log.info("v3 insufficient reseller credit kind=%s user_id=%s amount=%s",kind,user_id,quantity)
                    return text_error(409, NO_CREDITS)
                row_id = await conn.fetchval(
                    "INSERT INTO public.resellers_orders "
                    "(reseller_user_id,shoppex_order_id,line_item_id,members_type,quantity,"
                    "shoppex_delivery_id,provider_order_id,shoppex_invoice_id,status) "
                    "VALUES($1,$2,$3,$4,$5,$6,$7,$8,'processing') RETURNING id",
                    user_id, order, line, kind, quantity, signed_delivery_id, provider_order_id, invoice_ref
                )
                # Under the account row lock, debit and order reservation are atomic.
                await conn.execute(
                    "UPDATE public.resellers_accounts SET " + balance_column +
                    "=" + balance_column + "-$2,updated_at=now() WHERE user_id=$1",
                    user_id, quantity
                )
                await conn.execute(
                    "INSERT INTO public.resellers_credit_ledger "
                    "(reseller_user_id,order_id,members_type,credit_delta,reason) "
                    "VALUES($1,$2,$3,$4,'reserve')",
                    user_id,row_id,kind,-quantity
                )
    except Exception:
        log.exception("v3 atomic reservation failed kind=%s",kind)
        return text_error(503)

    # Once reserved, do not release credits unless provider explicitly refuses
    # without fulfilment. Network timeouts and 5xx may conceal a successful key.
    async def finish(status, error=None, payload=None, refund=False):
        async with backend.DB.acquire() as conn:
            async with conn.transaction():
                updated = await conn.fetchval(
                    "UPDATE public.resellers_orders SET status=$2,error_message=$3,"
                    "provider_key=$4,provider_response=$5::jsonb,updated_at=now() "
                    "WHERE id=$1 AND status='processing' RETURNING id",
                    row_id,status,error,
                    payload.get("key") if payload and status=="success" else None,
                    json.dumps(payload) if payload else None,
                )
                if updated and refund:
                    await conn.execute(
                        "UPDATE public.resellers_accounts SET "+balance_column+
                        "="+balance_column+"+$2,updated_at=now() WHERE user_id=$1",
                        user_id,quantity
                    )
                    await conn.execute(
                        "INSERT INTO public.resellers_credit_ledger "
                        "(reseller_user_id,order_id,members_type,credit_delta,reason) "
                        "VALUES($1,$2,$3,$4,'refund')",user_id,row_id,kind,quantity
                    )
                if updated and status=="success":
                    credit_balance = backend.valid_provider_credits(payload)
                    if credit_balance is not None:
                        await conn.execute(
                            "INSERT INTO public.provider_credits "
                            "(members_type,credits_remaining,last_order_id,updated_at) "
                            "VALUES($1,$2,NULL,now()) ON CONFLICT(members_type) DO UPDATE SET "
                            "credits_remaining=EXCLUDED.credits_remaining,last_order_id=NULL,updated_at=now()",
                            kind,credit_balance
                        )
                return bool(updated)

    provider_key = os.getenv("DISCORIUM_API_KEY", "").strip()
    if not provider_key:
        log.error("v3 provider key missing for reserved order id=%s",row_id)
        try:
            await finish("failed", "Provider not configured",refund=True)
        except Exception:
            log.exception("v3 refund failed for row=%s",row_id)
        return text_error(503)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20)) as client:
            response = await client.post(
                backend.PROVIDER_URL,
                headers={"Authorization":"Bearer "+provider_key,"Content-Type":"application/json"},
                json={"order_id":provider_order_id,"members_type":kind,"quantity":quantity},
            )
        payload = response.json()
        if not isinstance(payload,dict):
            raise ValueError("Invalid provider JSON")
    except (httpx.TimeoutException,httpx.TransportError,ValueError):
        log.warning("v3 provider outcome unknown order=%s",row_id)
        try:
            await finish("unknown","Provider response uncertain")
        except Exception:
            log.exception("v3 could not mark unknown order=%s",row_id)
        return text_error(502)
    if response.status_code in (429,) or response.status_code>=500:
        log.warning("v3 provider server error; unknown outcome order=%s",row_id)
        try:
            await finish("unknown","Provider error, outcome uncertain")
        except Exception:
            log.exception("v3 could not mark unknown order=%s",row_id)
        return text_error(502)
    if (response.is_success and payload.get("success") is True
            and isinstance(payload.get("key"),str) and payload["key"].strip()):
        try:
            completed = await finish("success",payload=payload)
        except Exception:
            log.exception("v3 provider succeeded but settlement failed order=%s",row_id)
            return text_error(503)
        if completed:
            return PlainTextResponse(SUCCESS_PREFIX+payload["key"])
        return text_error(503)
    if response.is_success and payload.get("success") is True:
        try:
            await finish("unknown","Provider reported success without key")
        except Exception:
            log.exception("v3 could not mark incomplete order=%s",row_id)
        return text_error(502)
    # Explicit provider refusal: release only our reserved credits, never
    # a second time and never for a transport-level uncertain response.
    try:
        await finish("failed","Provider rejected order",refund=True)
    except Exception:
        log.exception("v3 refund settlement failed order=%s",row_id)
        return text_error(503)
    return text_error(502)


@router.post("/v3/resellers/order/members/offline", response_class=PlainTextResponse)
async def reseller_offline(request:Request):
    return await reseller_fulfill("offline",request)


@router.post("/v3/resellers/order/members/online", response_class=PlainTextResponse)
async def reseller_online(request:Request):
    return await reseller_fulfill("online",request)


@router.get("/v3/resellers/order/members/offline",include_in_schema=False)
@router.get("/v3/resellers/order/members/online",include_in_schema=False)
async def browser_denied(request:Request):
    return text_error(401)
