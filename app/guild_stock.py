"""Public guild-specific stock proxy; validates snowflakes before upstream access."""
import asyncio
import logging
import os
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


async def verify_guild_with_discord(client: httpx.AsyncClient, guild_id: str) -> dict:
    """Verify a guild using the official Discord bot API, not just its snowflake.

    An unknown guild and a guild the bot is not in cannot always be
    distinguished. Never label both cases 'invalid'.
    """
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        raise HTTPException(
            status_code=503,
            detail="Discord guild verification is not configured on the API",
        )
    try:
        response = await client.get(
            f"https://discord.com/api/v10/guilds/{guild_id}",
            headers={"Authorization": "Bot " + token, "Accept": "application/json"},
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Discord verification unavailable") from exc
    if response.status_code == 401:
        log.error("Discord guild verification credentials rejected")
        raise HTTPException(status_code=503, detail="Discord guild verification unavailable")
    if response.status_code == 429:
        raise HTTPException(status_code=503, detail="Discord rate limit reached; retry later")
    if response.status_code in (403, 404):
        # A bot cannot query private guilds it hasn't joined. A public,
        # discoverable guild can still be confirmed through the preview API.
        try:
            preview = await client.get(
                f"https://discord.com/api/v10/guilds/{guild_id}/preview",
                headers={"Authorization": "Bot " + token, "Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="Discord verification unavailable") from exc
        if preview.status_code != 200:
            if preview.status_code == 429:
                raise HTTPException(status_code=503, detail="Discord rate limit reached; retry later")
            # Do not mistake inaccessible or undiscoverable servers for
            # nonexistent ones. Do not request provider stock if unverified.
            raise HTTPException(
                status_code=404,
                detail="Discord could not verify this server. Add the verification bot to the server or use a discoverable server.",
            )
        response = preview
    if response.status_code != 200:
        log.warning("Discord guild check failed with HTTP %s", response.status_code)
        raise HTTPException(status_code=502, detail="Discord verification unavailable")
    try:
        body = response.json()
        if not isinstance(body, dict) or str(body.get("id")) != guild_id:
            raise ValueError("Guild ID mismatch")
        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Missing guild name")
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=502, detail="Invalid Discord verification response") from exc
    return {"id": guild_id, "name": name[:100]}


async def get_guild_stock(guild_id: str) -> dict:
    guild_id = validate_guild_id(guild_id)
    async with _cache_lock:
        cached = _cache.get(guild_id)
        if cached and time.monotonic() - cached[0] < CACHE_TTL_SECONDS:
            return cached[1]
        async with httpx.AsyncClient(timeout=httpx.Timeout(12), follow_redirects=False) as client:
            # Discord is checked FIRST. Never contact the stock provider
            # for malformed, unverified, or inaccessible guild IDs.
            discord_guild = await verify_guild_with_discord(client, guild_id)
            try:
                response = await client.get(
                    GUILD_STOCK_URL,
                    params={"guild_id": guild_id},
                    headers={"Accept": "application/json"},
                )
                if response.status_code in (400, 404, 422):
                    raise HTTPException(
                        status_code=404, detail="No provider stock found for this guild"
                    )
                response.raise_for_status()
                if len(response.content) > MAX_RESPONSE_BYTES:
                    raise ValueError("Provider response too large")
                payload = validate_guild_payload(response.json(), guild_id)
            except HTTPException:
                raise
            except (httpx.HTTPError, ValueError) as error:
                log.warning("Guild stock provider unavailable: %s", type(error).__name__)
                raise HTTPException(
                    status_code=502, detail="Guild stock provider unavailable"
                ) from error
        if len(_cache) >= 256:
            _cache.clear()
        # Keep the original JSON keys and attach Discord-verified guild name.
        payload["guild_name"] = discord_guild["name"]
        _cache[guild_id] = (time.monotonic(), payload)
        return payload
