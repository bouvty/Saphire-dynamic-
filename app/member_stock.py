"""Synchronize provider Discord-member stock to Supabase and serve a public read-only cache."""
import asyncio
import logging
import os
from datetime import datetime, timezone

import httpx
from fastapi.responses import JSONResponse

log = logging.getLogger("saphire.stock")
DEFAULT_SOURCE = "https://176.100.37.219/api/v2/stock"
SYNC_SECONDS = max(60, int(os.getenv("MEMBER_STOCK_REFRESH_SECONDS", "600")))
SOURCE = os.getenv("MEMBER_STOCK_SOURCE_URL", DEFAULT_SOURCE)


def validate_stock(payload):
    """Accept only provider-confirmed, nonnegative integer stock counts."""
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ValueError("Provider did not report success")
    values = []
    for name in ("offline_members", "online_members"):
        value = payload.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("Invalid provider stock field: " + name)
        values.append(value)
    return tuple(values)


async def sync_once(pool):
    """A failed fetch preserves the previous successful counts."""
    if pool is None:
        return False
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12), follow_redirects=False) as client:
            response = await client.get(SOURCE, headers={"Accept": "application/json"})
            response.raise_for_status()
            if len(response.content) > 32768:
                raise ValueError("Provider stock payload too large")
            offline, online = validate_stock(response.json())
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO public.member_stock
                  (id, offline_members, online_members, success, source_url,
                   last_synced_at, last_attempt_at, last_error, updated_at)
                VALUES (1, $1, $2, true, $3, now(), now(), NULL, now())
                ON CONFLICT (id) DO UPDATE SET
                  offline_members = EXCLUDED.offline_members,
                  online_members = EXCLUDED.online_members,
                  success = true,
                  source_url = EXCLUDED.source_url,
                  last_synced_at = now(),
                  last_attempt_at = now(),
                  last_error = NULL,
                  updated_at = now()
            """, offline, online, SOURCE)
        log.info("Stock synchronized: offline=%d online=%d", offline, online)
        return True
    except Exception as error:
        log.warning("Stock sync failed: %s: %s", type(error).__name__, str(error)[:160])
        try:
            async with pool.acquire() as conn:
                await conn.execute("""
                    UPDATE public.member_stock SET last_attempt_at=now(),
                        last_error=$1, updated_at=now() WHERE id=1
                """, (type(error).__name__ + ": " + str(error))[:350])
        except Exception:
            log.exception("Unable to record stock sync failure")
        return False


async def sync_loop(pool):
    while True:
        await sync_once(pool)
        await asyncio.sleep(SYNC_SECONDS)


async def public_stock(pool):
    if pool is None:
        return JSONResponse({"success": False, "detail": "Stock database unavailable"}, status_code=503)
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT offline_members, online_members, success
                FROM public.member_stock WHERE id = 1
            """)
    except Exception:
        log.exception("Unable to read member stock")
        return JSONResponse({"success": False, "detail": "Stock database unavailable"}, status_code=503)
    if not row or not row["success"] or row["offline_members"] is None or row["online_members"] is None:
        return JSONResponse({"success": False, "detail": "Stock not synchronized yet"}, status_code=503)
    return JSONResponse({
        "offline_members": row["offline_members"],
        "online_members": row["online_members"],
        "success": True,
    })
