"""Validated 15-minute provider stock cache, stored privately in Supabase."""
import asyncio
import logging
import os

import httpx
from fastapi.responses import JSONResponse

log = logging.getLogger("saphire.stock")
SOURCE = os.getenv("MEMBER_STOCK_SOURCE_URL", "https://176.100.37.219/api/v2/stock")
SYNC_SECONDS = 900
# Dedicated advisory lock prevents duplicate fetches across service replicas.
LOCK_ID = 214660731


def validate_stock(payload):
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ValueError("Provider did not report success")
    result = []
    for name in ("offline_members", "online_members"):
        value = payload.get(name)
        if type(value) is not int or value < 0:
            raise ValueError("Invalid provider stock count: " + name)
        result.append(value)
    return tuple(result)


async def sync_once(pool):
    """Fetch source only if due; preserve last valid stock when source fails."""
    if pool is None:
        return False
    try:
        async with pool.acquire() as conn:
            locked = await conn.fetchval("SELECT pg_try_advisory_lock($1)", LOCK_ID)
            if not locked:
                return False
            try:
                # Claim the next refresh atomically, even across replicas.
                claimed = await conn.fetchval("""
                    UPDATE public.member_stock
                       SET last_attempt_at = now()
                     WHERE id = 1
                       AND (last_attempt_at IS NULL
                            OR last_attempt_at <= now() - interval '15 minutes')
                    RETURNING id
                """)
                if not claimed:
                    return False
                try:
                    async with httpx.AsyncClient(
                        timeout=httpx.Timeout(12), follow_redirects=False
                    ) as client:
                        response = await client.get(SOURCE, headers={"Accept": "application/json"})
                        response.raise_for_status()
                        if len(response.content) > 32768:
                            raise ValueError("Provider stock response too large")
                        offline, online = validate_stock(response.json())
                    await conn.execute("""
                        UPDATE public.member_stock SET
                            offline_members=$1, online_members=$2,
                            success=true, source_url=$3,
                            last_synced_at=now(), last_error=NULL,
                            updated_at=now()
                        WHERE id=1
                    """, offline, online, SOURCE)
                    log.info("Stock synchronized: offline=%s online=%s", offline, online)
                    return True
                except Exception as exc:
                    message = type(exc).__name__ + ": " + str(exc)
                    log.warning("Provider stock sync failed: %s", message[:180])
                    await conn.execute("""
                        UPDATE public.member_stock SET last_error=$1, updated_at=now()
                        WHERE id=1
                    """, message[:350])
                    return False
            finally:
                await conn.execute("SELECT pg_advisory_unlock($1)", LOCK_ID)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("Stock sync failed to access database")
        return False


async def sync_loop(pool):
    """Immediate startup attempt then one check per 15 minutes."""
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
                FROM public.member_stock WHERE id=1
            """)
        if not row or not row["success"] or row["offline_members"] is None or row["online_members"] is None:
            return JSONResponse({"success": False, "detail": "Stock not synchronized yet"}, status_code=503)
        return JSONResponse({
            "offline_members": row["offline_members"],
            "online_members": row["online_members"],
            "success": True,
        }, headers={"Cache-Control": "public, max-age=60"})
    except Exception:
        log.exception("Could not read stock cache")
        return JSONResponse({"success": False, "detail": "Stock database unavailable"}, status_code=503)
