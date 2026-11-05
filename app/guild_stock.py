"""Public guild-specific stock proxy; validates snowflakes before upstream access."""
import asyncio
import logging
import os
import re
import time
from urllib.parse import urlsplit

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


def parse_invite_code(value: str) -> str | None:
    """Recognise only official Discord invite hosts, never arbitrary URLs."""
    if not isinstance(value, str) or len(value) > 250:
        raise HTTPException(status_code=422, detail="Invalid guild ID or Discord invite")
    value = value.strip()
    if not value:
        raise HTTPException(status_code=422, detail="Invalid guild ID or Discord invite")
    candidate = value if "://" in value else "https://" + value
    try:
        url = urlsplit(candidate)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid Discord invite")
    host = (url.hostname or "").lower()
    if url.scheme != "https" or url.port is not None or url.username is not None or url.password is not None:
        raise HTTPException(status_code=422, detail="Invalid Discord invite")
    parts = [part for part in url.path.split("/") if part]
    if host in ("discord.gg", "www.discord.gg"):
        code = parts[0] if len(parts) == 1 else ""
    elif host in ("discord.com", "www.discord.com", "discordapp.com", "www.discordapp.com"):
        code = parts[1] if len(parts) == 2 and parts[0] == "invite" else ""
    else:
        raise HTTPException(status_code=422, detail="Invalid guild ID or Discord invite")
    if not re.fullmatch(r"[A-Za-z0-9_-]{2,64}", code):
        raise HTTPException(status_code=422, detail="Invalid Discord invite")
    return code


async def resolve_invite(client: httpx.AsyncClient, code: str) -> dict:
    """Discord GET /invites/:code verifies invitation and yields its guild ID."""
    try:
        resp = await client.get(
            f"https://discord.com/api/v10/invites/{code}",
            headers={"Accept": "application/json"},
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Discord invite verification unavailable") from exc
    if resp.status_code in (400, 404):
        raise HTTPException(status_code=422, detail="Invalid or expired Discord server invite")
    if resp.status_code == 429:
        raise HTTPException(status_code=503, detail="Discord is rate limiting checks; try again shortly")
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Discord invite verification unavailable")
    try:
        body = resp.json()
        guild = body.get("guild")
        guild_id = validate_guild_id(str(guild.get("id")))
        name = guild.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Missing guild name")
    except (ValueError, AttributeError, TypeError, HTTPException) as exc:
        raise HTTPException(status_code=422, detail="Invalid Discord server invite") from exc
    return {"id": guild_id, "name": name[:100]}


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
    # Public/discoverable servers can be checked without a bot token.
    # For private guild IDs, verification requires a bot in the guild or a
    # valid invite, which is separately checked against Discord.
    response = None
    if token:
        try:
            response = await client.get(
                f"https://discord.com/api/v10/guilds/{guild_id}",
                headers={"Authorization": "Bot " + token, "Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="Discord verification unavailable") from exc
    else:
        response = httpx.Response(404)
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
                headers={"Accept": "application/json"},
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
                detail="Discord could not verify this guild ID. Use a valid Discord invite or a server that the verification bot can access.",
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
    """Validate input, verify guild through Discord, then mirror provider stock."""
    raw = guild_id.strip() if isinstance(guild_id, str) else ""
    invite = None
    if SNOWFLAKE_RE.fullmatch(raw):
        guild_id = validate_guild_id(raw)
    else:
        invite = parse_invite_code(raw)
    async with _cache_lock:
        # A verified invite always resolves through Discord before cache lookup.
        async with httpx.AsyncClient(timeout=httpx.Timeout(12), follow_redirects=False) as client:
            if invite:
                discord_guild = await resolve_invite(client, invite)
                guild_id = discord_guild["id"]
            else:
                guild_id = validate_guild_id(guild_id)
                discord_guild = None

            cached = _cache.get(guild_id)
            if cached and time.monotonic() - cached[0] < CACHE_TTL_SECONDS:
                return cached[1]
            if discord_guild is None:
                discord_guild = await verify_guild_with_discord(client, guild_id)
            try:
                response = await client.get(
                    GUILD_STOCK_URL,
                    params={"guild_id": guild_id},
                    headers={"Accept": "application/json"},
                )
                if response.status_code in (400, 404, 422):
                    raise HTTPException(status_code=404, detail="No provider stock found for this guild")
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
        payload["guild_name"] = discord_guild["name"]
        _cache[guild_id] = (time.monotonic(), payload)
        return payload
