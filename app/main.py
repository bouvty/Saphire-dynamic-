import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import time
from pathlib import Path
from contextlib import asynccontextmanager

import asyncpg
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse, PlainTextResponse
from app.member_stock import sync_loop, public_stock
from app.guild_stock import get_guild_stock

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("saphire.delivery")
DB = None
PROVIDER_URL = "https://delivery.discorium.cc/api/v2/reseller/custom/delivery/members"

@asynccontextmanager
async def lifespan(app):
    global DB
    url = os.getenv("DATABASE_URL")
    if url:
        try:
            DB = await asyncpg.create_pool(url, min_size=1, max_size=8, command_timeout=30)
        except Exception:
            log.exception("Database unavailable; API delivery remains disabled")
    else:
        log.warning("DATABASE_URL not configured; API delivery remains disabled")
    stock_task = asyncio.create_task(sync_loop(DB)) if DB is not None else None
    try:
        yield
    finally:
        if stock_task is not None:
            stock_task.cancel()
            try:
                await stock_task
            except asyncio.CancelledError:
                pass
        if DB is not None:
            await DB.close()

app = FastAPI(title="Saphire Shoppex Delivery", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    if (request.url.path.rstrip("/") == "/v2/stock/members" or request.url.path == "/v2/stock/members/check/guild") and request.method in ("GET", "HEAD"):
        # Public stock counts only; no credentials or customer data.
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Cache-Control"] = "public, max-age=60"
    else:
        response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    return response

ACCESS_HTML = (Path(__file__).parent / "access_declined.html").read_text(encoding="utf-8")
UNAUTHORIZED_HTML = (ACCESS_HTML
    .replace("Error 404 · Restricted Endpoint", "Error 401 · Restricted Endpoint")
    .replace("This API endpoint is unavailable or doesn't exist. Check the request URL and try again.",
             "You are not authorized to access this endpoint. Valid API credentials or a verified request signature are required.")
    .replace("REQUEST DENIED", "UNAUTHORIZED REQUEST")
)

def auth_error_response(request: Request, status_code: int, detail: str):
    # Human browser GET visits show the branded access page.
    # Shoppex POST requests (and all programmatic clients) keep JSON with the
    # correct HTTP status, so dynamic delivery integrations can parse failures.
    wants_html = (
        request.method in ("GET", "HEAD")
        and "text/html" in request.headers.get("accept", "").lower()
    )
    if wants_html:
        return HTMLResponse(UNAUTHORIZED_HTML, status_code=status_code)
    return JSONResponse({"detail": detail}, status_code=status_code)

@app.exception_handler(401)
async def unauthorized_handler(request: Request, exc: HTTPException):
    return auth_error_response(request, 401, str(exc.detail))

@app.exception_handler(403)
async def forbidden_handler(request: Request, exc: HTTPException):
    return auth_error_response(request, 403, str(exc.detail))


@app.get("/v2/stock/members/")
@app.get("/v2/stock/members", include_in_schema=False)
async def get_member_stock():
    return await public_stock(DB)


@app.get("/v2/stock/members/check/guild")
async def check_guild_stock(guild_id: str = ""):
    return await get_guild_stock(guild_id)


@app.get("/_liveness")
async def liveness():
    return {"status": "running"}

@app.get("/health")
async def health():
    if DB is None:
        return JSONResponse({"status": "degraded", "database": "unavailable"}, status_code=503)
    try:
        async with DB.acquire() as conn:
            await conn.fetchval("SELECT 1")
    except Exception:
        return JSONResponse({"status": "degraded", "database": "unavailable"}, status_code=503)
    return {"status": "ok"}

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
@app.get("/404", response_class=HTMLResponse, include_in_schema=False)
async def access_declined():
    return HTMLResponse(ACCESS_HTML, status_code=404)

@app.exception_handler(404)
async def not_found(request: Request, exc: HTTPException):
    if request.method in ("GET", "HEAD"):
        return HTMLResponse(ACCESS_HTML, status_code=404)
    return JSONResponse({"detail": "Not Found"}, status_code=404)

def authenticate(request: Request, raw: bytes, kind: str):
    env_name = "SHOPPEX_OFFLINE_SIGNING_SECRET" if kind == "offline" else "SHOPPEX_ONLINE_SIGNING_SECRET"
    secret = os.getenv(env_name, "")
    if not secret:
        raise HTTPException(503, "Signing secret not configured")
    signature = request.headers.get("x-shoppex-signature-v2", "").strip()
    timestamp = request.headers.get("x-shoppex-timestamp", "").strip()
    signature_parts = signature.split(",")
    fields = {}
    if signature_parts and signature_parts[0] == "v1":
        fields = dict(part.strip().split("=", 1) for part in signature_parts[1:] if "=" in part)
    if len(signature) > 256 or not re.fullmatch(r"[a-fA-F0-9]{64}", fields.get("h", "")):
        log.warning("Shoppex signature rejected: missing_or_invalid_v2_signature kind=%s signature_present=%s", kind, bool(signature))
        raise HTTPException(401, "Invalid Shoppex signature")
    try:
        numeric_timestamp = int(timestamp)
    except ValueError:
        numeric_timestamp = 0
    if numeric_timestamp <= 0 or str(numeric_timestamp) != fields.get("t") or abs(time.time() - numeric_timestamp) > 300:
        log.warning("Shoppex signature rejected: missing_or_stale_timestamp kind=%s timestamp_present=%s", kind, bool(timestamp))
        raise HTTPException(401, "Invalid Shoppex signature timestamp")

    # Shoppex's webhook signing scheme includes a delivery identifier.
    # Dynamic-product callbacks can supply that identifier under different
    # header names or in the JSON body. We validate candidates against the
    # HMAC, never trust an unverified identifier, and never accept unsigned calls.
    candidates = [
        request.headers.get("x-shoppex-delivery"),
        request.headers.get("x-shoppex-delivery-id"),
        request.headers.get("x-shoppex-webhook-id"),
        request.headers.get("webhook-id"),
    ]
    try:
        body = json.loads(raw)
        if isinstance(body, dict):
            for field in ("delivery_id", "deliveryId", "webhook_id", "event_id", "line_item_id"):
                v = body.get(field)
                if isinstance(v, (str, int)):
                    candidates.append(str(v))
            delivery = body.get("delivery")
            if isinstance(delivery, dict):
                for field in ("id", "uniqid"):
                    if isinstance(delivery.get(field), (str, int)):
                        candidates.append(str(delivery[field]))
    except (ValueError, UnicodeDecodeError):
        pass

    verified_delivery_id = None
    for candidate in dict.fromkeys(candidates):
        if not isinstance(candidate, str) or not 1 <= len(candidate) <= 128:
            continue
        message = candidate.encode() + b"." + timestamp.encode() + b"." + raw
        expected = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, fields["h"].lower()):
            verified_delivery_id = candidate
            break
    if verified_delivery_id is None:
        log.warning("Shoppex signature rejected: hmac_mismatch_or_missing_delivery_id kind=%s signed_id_candidates=%d headers=%s",
                    kind, len([c for c in candidates if c]),
                    ",".join(sorted(k for k in request.headers.keys() if k.startswith("x-shoppex-") or k == "webhook-id")))
        raise HTTPException(401, "Invalid Shoppex signature")
    request.state.shoppex_delivery_id = verified_delivery_id

def pick(body, *paths):
    for path in paths:
        value = body
        for part in path.split("."):
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(part)
        if value is not None and value != "":
            return value
    return None

def extract(body):
    order = pick(body, "order_id", "order.id", "order.uniqid", "order", "invoice.id", "invoice.uniqid", "invoice_id", "uniqid")
    line = pick(body, "line_item_id", "item.id", "order_item.id", "order_item_id", "delivery_id", "product_id", "product.id", "product")
    quantity = pick(body, "quantity", "item.quantity", "order_item.quantity", "data.quantity", "units")
    if isinstance(order, dict):
        order = order.get("id") or order.get("uniqid")
    if isinstance(line, dict):
        line = line.get("id") or line.get("uniqid")
    if not isinstance(order, (str, int)) or not str(order) or len(str(order)) > 128:
        raise HTTPException(422, "Missing or invalid order ID")
    if line is not None and len(str(line)) > 128:
        raise HTTPException(422, "Invalid line item")
    if isinstance(quantity, bool) or not isinstance(quantity, (int, str)) or not str(quantity).isdigit():
        raise HTTPException(422, "Missing or invalid quantity")
    quantity = int(quantity)
    if not 1 <= quantity <= 100000:
        raise HTTPException(422, "Quantity out of range")
    custom = pick(body, "custom_fields", "fields", "customFields", "metadata", "item.custom_fields", "data.custom_fields", "order.custom_fields", "order_details", "order.details")
    server = pick(body, "discord_server", "discord_server_link", "server", "server_link")
    if isinstance(custom, dict):
        for k, v in custom.items():
            if "discord" in str(k).lower() or "server" in str(k).lower():
                server = v.get("value") if isinstance(v, dict) else v
                break
    elif isinstance(custom, list):
        for item in custom:
            if isinstance(item, dict):
                label = str(item.get("name") or item.get("label") or item.get("key") or "")
                if "discord" in label.lower() or "server" in label.lower():
                    server = item.get("value", item.get("answer"))
                    break
    if isinstance(server, dict):
        server = server.get("value") or server.get("answer")
    server = str(server)[:500] if server is not None else None
    return str(order), str(line or "default"), quantity, server

def redact_private(value):
    """Store all non-secret Shoppex order data without any embedded credentials."""
    blocked = ("password", "secret", "token", "api_key", "authorization", "private_key", "signature", "bearer", "card_number", "cvv")
    if isinstance(value, dict):
        return {str(k): ("[REDACTED]" if any(x in str(k).lower() for x in blocked)
                         else redact_private(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_private(item) for item in value]
    return value


def format_delivery_message(provider_key: str) -> str:
    """Human-readable plain-text delivery, with no JSON or Markdown wrappers."""
    return (
        "Thank you for using saphire ❤️\n"
        "please use our redeem panel below to redeem your key, follow the steps provided.\n"
        f"https://memberix.live/redeem   KEY: {provider_key}"
    )


def provider_order_id(order, line, kind):
    return "dsc-" + hashlib.sha256((order + "|" + line + "|" + kind).encode()).hexdigest()[:12]

def valid_provider_credits(payload):
    """Only accept an explicitly supplied nonnegative integer credit balance."""
    if not isinstance(payload, dict):
        return None
    balance = payload.get("credits_remaining")
    if type(balance) is int and 0 <= balance <= 9223372036854775807:
        return balance
    return None


async def set_status(order, line, kind, status, error):
    async with DB.acquire() as conn:
        await conn.execute("""UPDATE public.delivery_orders SET status=$4,error_message=$5,updated_at=now()
                              WHERE shoppex_order_id=$1 AND line_item_id=$2 AND members_type=$3""",
                           order, line, kind, status, error)

async def fulfill(kind: str, request: Request):
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise HTTPException(415, "Content-Type must be application/json")
    if request.headers.get("content-length", "0").isdigit() and int(request.headers.get("content-length", "0")) > 65536:
        raise HTTPException(413, "Payload too large")
    raw = await request.body()
    if len(raw) > 65536:
        raise HTTPException(413, "Payload too large")
    authenticate(request, raw, kind)
    if DB is None:
        raise HTTPException(503, "Delivery service unavailable")
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "Invalid JSON")
    if not isinstance(body, dict):
        raise HTTPException(422, "Expected JSON object")
    order, line, quantity, server = extract(body)
    # Shoppex's delivery ID is the line-level fallback when multiple deliveries
    # share one invoice; never generate a second provider key on a retry.
    if line == "default":
        line = request.state.shoppex_delivery_id
    pid = provider_order_id(order, line, kind)
    shoppex_delivery_id = request.state.shoppex_delivery_id
    product = pick(body, "product_id", "product.id", "product.uniqid", "product", "item.product_id")
    invoice = pick(body, "invoice_id", "invoice.id", "invoice.uniqid", "invoice", "order.invoice_id")
    customer_email = pick(body, "customer.email", "email", "customer_email", "buyer.email")
    shoppex_event = request.headers.get("x-shoppex-event")
    async with DB.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("""INSERT INTO public.delivery_orders
                  (shoppex_order_id,line_item_id,members_type,quantity,discord_server,provider_order_id,status,request_payload,
                   shoppex_delivery_id,shoppex_product_id,shoppex_invoice_id,shoppex_customer_email,shoppex_event)
                  VALUES($1,$2,$3,$4,$5,$6,'processing',$7::jsonb,$8,$9,$10,$11,$12)
                  ON CONFLICT(shoppex_order_id,line_item_id,members_type) DO NOTHING RETURNING *""",
                  order, line, kind, quantity, server, pid, json.dumps(redact_private(body)),
                  shoppex_delivery_id,
                  str(product)[:128] if isinstance(product, (str, int)) else None,
                  str(invoice)[:128] if isinstance(invoice, (str, int)) else None,
                  str(customer_email)[:320] if isinstance(customer_email, str) else None,
                  str(shoppex_event)[:128] if shoppex_event else None)
            if row is None:
                row = await conn.fetchrow("""SELECT * FROM public.delivery_orders WHERE
                  shoppex_order_id=$1 AND line_item_id=$2 AND members_type=$3""", order, line, kind)
                if row["quantity"] != quantity:
                    raise HTTPException(409, "Order quantity mismatch on retry")
                if row["status"] == "success":
                    return PlainTextResponse(format_delivery_message(row["provider_key"]))
                if row["status"] == "failed":
                    raise HTTPException(502, "Provider rejected order; contact support")
                raise HTTPException(503, "Delivery pending verification; contact support")
    key = os.getenv("DISCORIUM_API_KEY", "")
    if not key:
        await set_status(order, line, kind, "unknown", "Provider key not configured")
        raise HTTPException(503, "Provider not configured")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20)) as client:
            response = await client.post(PROVIDER_URL,
                headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                json={"order_id": pid, "members_type": kind, "quantity": quantity})
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Invalid provider response")
    except (httpx.TimeoutException, httpx.TransportError, ValueError) as exc:
        await set_status(order, line, kind, "unknown", "Provider outcome uncertain")
        log.warning("Unknown outcome for %s (%s)", pid, type(exc).__name__)
        raise HTTPException(502, "Provider response uncertain; manual investigation required")
    if response.status_code == 429 or response.status_code >= 500:
        await set_status(order, line, kind, "unknown", "Provider server error")
        raise HTTPException(502, "Provider outcome uncertain; manual investigation required")
    if response.is_success and payload.get("success") is True and isinstance(payload.get("key"), str) and payload["key"].strip():
        credits = valid_provider_credits(payload)
        async with DB.acquire() as conn:
            # Store the provider key/response and the corresponding provider
            # credit balance together. Invalid or absent credit fields never
            # overwrite a last known valid balance.
            async with conn.transaction():
                saved_order_id = await conn.fetchval("""UPDATE public.delivery_orders
                    SET status='success',provider_key=$4,
                        provider_response=$5::jsonb,updated_at=now()
                    WHERE shoppex_order_id=$1 AND line_item_id=$2 AND members_type=$3
                    RETURNING id""",
                    order, line, kind, payload["key"], json.dumps(payload))
                if credits is not None and saved_order_id is not None:
                    await conn.execute("""INSERT INTO public.provider_credits
                        (members_type,credits_remaining,last_order_id,updated_at)
                        VALUES($1,$2,$3,now())
                        ON CONFLICT(members_type) DO UPDATE SET
                          credits_remaining=EXCLUDED.credits_remaining,
                          last_order_id=EXCLUDED.last_order_id,
                          updated_at=EXCLUDED.updated_at""",
                        kind, credits, saved_order_id)
                elif credits is None:
                    log.warning("Provider response omitted a valid credits_remaining for %s", pid)
        return PlainTextResponse(format_delivery_message(payload["key"]))
    error = str(payload.get("error", "Provider rejected order"))[:500]
    if response.is_success and payload.get("success") is True:
        await set_status(order, line, kind, "unknown", "Provider claimed success without a usable key")
        raise HTTPException(502, "Provider response incomplete; manual investigation required")
    await set_status(order, line, kind, "failed", error)
    raise HTTPException(502, "Provider could not fulfil order")

@app.post("/v2/resellers/order/members/offline")
async def offline(request: Request):
    return await fulfill("offline", request)

@app.post("/v2/resellers/order/members/online")
async def online(request: Request):
    return await fulfill("online", request)

# These endpoints are POST-only for Shoppex. An interactive browser GET to
# their URLs must show the branded unauthorized page, never call the provider.
@app.get("/v2/resellers/order/members/offline", include_in_schema=False)
@app.get("/v2/resellers/order/members/online", include_in_schema=False)
async def protected_endpoint_browser(request: Request):
    return auth_error_response(request, 401, "Valid signed Shoppex request required")
