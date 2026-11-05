"""Public guild-specific stock proxy; validates snowflakes before upstream access."""
import asyncio
import logging
import re
import time

import httpx
from fastapi import HTTPException

log = logging.getLogger("saphire.guild_stock")
GUILD_STOCK_URL = "https://176.100.37.219/api/v2/stock/guild"
SNOWFLAKE_RE = re.compile(r"[0-9]{17,20}\Z")
DISCORD_EPOCH_MS = 1420070400000
MAX_RESPONSE_BYTES = 32768
# Only one HTTP call per guild per 20 seconds to reduce provider load.
CACHE_TTL_SECONDS = 20
_cache = {}
_cache_lock = asyncio.Lock()


def validate_guild_id(value: str) -> str:
    """Check Discord snowflake syntax and plausible encoded creation timestamp.

    This does not confirm the server exists; the provider performs that check.
    """
    if not isinstance(value, str) or not SNOWFLAKE_RE.fullmatch(value):
        raise HTTPException(status_code=422, detail="Invalid Discord guild ID: expected 17-20 digits")
    snowflake = int(value)
    timestamp_ms = (snowflake >> 22) + DISCORD_EPOCH_MS
    now_ms = int(time.time() * 1000)
    if snowflake <= 0 or timestamp_ms > now_ms + 86400000:
        raise HTTPException(status_code=422, detail="Invalid Discord guild ID")
    return value


def validate_guild_payload(payload: object, guild_id: str) -> dict:
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ValueError("Provider did not confirm guild stock")
    if str(payload.get("guild_id")) != guild_id:
        raise ValueError("Provider guild ID did not match request")
    counts = {}
    for key in ("offline_members", "online_members", "total"):
        value = payload.get(key)
        if type(value) is not int or value < 0:
            raise ValueError("Invalid provider field: " + key)
        counts[key] = value
    if counts["total"] != counts["offline_members"] + counts["online_members"]:
        raise ValueError("Provider total does not match stock counts")
    return {
        "guild_id": guild_id,
        "offline_members": counts["offline_members"],
        "online_members": counts["online_members"],
        "success": True,
        "total": counts["total"],
    }


async def get_guild_stock(guild_id: str) -> dict:
    guild_id = validate_guild_id(guild_id)
    async with _cache_lock:
        cached = _cache.get(guild_id)
        if cached and time.monotonic() - cached[0] < CACHE_TTL_SECONDS:
            return cached[1]
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(12), follow_redirects=False) as client:
                response = await client.get(GUILD_STOCK_URL,
                    params={"guild_id": guild_id}, headers={"Accept": "application/json"})
                if response.status_code in (400, 404, 422):
                    raise HTTPException(status_code=404, detail="Guild not found or unavailable at provider")
                response.raise_for_status()
                if len(response.content) > MAX_RESPONSE_BYTES:
                    raise ValueError("Provider response too large")
                payload = validate_guild_payload(response.json(), guild_id)
        except HTTPException:
            raise
        except (httpx.HTTPError, ValueError) as error:
            log.warning("Guild stock provider unavailable: %s", type(error).__name__)
            raise HTTPException(status_code=502, detail="Guild stock provider unavailable") from error
        if len(_cache) >= 256:
            _cache.clear()
        _cache[guild_id] = (time.monotonic(), payload)
        return payload
