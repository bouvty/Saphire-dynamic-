"""Short-lived Discord OAuth staff registration for the Saphire self-hosted bot."""
import hashlib
import hmac
import html
import json
import logging
import os
import re
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter()
log = logging.getLogger("saphire.staff.oauth")
COOKIE = "saphire_staff_oauth_state"
DISCORD_API = "https://discord.com/api/v10"
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{35,100}$")


def state_hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def oauth_config():
    client_id = (os.getenv("STAFF_DISCORD_CLIENT_ID") or os.getenv("DISCORD_CLIENT_ID") or "").strip()
    secret = (os.getenv("STAFF_DISCORD_CLIENT_SECRET") or os.getenv("DISCORD_CLIENT_SECRET") or "").strip()
    callback = os.getenv("STAFF_OAUTH_REDIRECT_URI", "https://api.saphire.rip/v3/staff/auth/callback").strip()
    if not (client_id.isdecimal() and secret and callback.startswith("https://") and
            callback.endswith("/v3/staff/auth/callback")):
        return None
    return client_id, secret, callback


_PAGE_TEMPLATE = (Path(__file__).with_name("staff_auth_page.html")).read_text(encoding="utf-8")
_ICON_CHECK = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="m5 12 4.5 4.5L19 7"/></svg>'
)
_ICON_ALERT = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M12 9v4m0 4h.01"/><path d="M10.3 3.9 2.2 18.1A2 2 0 0 0 3.9 21h16.2a2 2 0 0 0 1.7-2.9L13.7 3.9a2 2 0 0 0-3.4 0Z"/></svg>'
)


def page(title, message, status=200):
    title = html.escape(title, quote=True)
    message = html.escape(message, quote=True)
    success = status == 200 and title == "Authentication complete"
    body = (_PAGE_TEMPLATE
        .replace("__TITLE__", title)
        .replace("__MESSAGE__", message)
        .replace("__ICON_CLASS__", "success" if success else "warning")
        .replace("__ICON_SVG__", _ICON_CHECK if success else _ICON_ALERT))
    return HTMLResponse(body, status_code=status, headers={
        "Cache-Control": "no-store",
        "X-Robots-Tag": "noindex, nofollow, noarchive",
    })


@router.get("/v3/staff/auth/start", include_in_schema=False)
async def staff_oauth_start(request: Request, ticket: str = ""):
    from app import main
    config = oauth_config()
    if main.DB is None:
        return page("Login unavailable", "The staff registration database is temporarily unavailable. Try again from your Discord staff panel.", 503)
    if config is None:
        return page("Login unavailable", "Discord OAuth setup is incomplete. A Saphire administrator needs to configure the Discord OAuth client secret on the API server.", 503)
    if TOKEN_RE.fullmatch(ticket) is None:
        return page("Invalid login link", "Start again from your Discord staff panel.", 400)
    async with main.DB.acquire() as conn:
        pending = await conn.fetchval(
            "SELECT userid FROM public.staff_auth_requests "
            "WHERE token_hash=$1 AND expires_at>now() AND consumed_at IS NULL", state_hash(ticket))
    if not pending:
        return page("Expired login link", "Start again from your Discord staff panel.", 403)
    client_id, _, callback = config
    url = "https://discord.com/oauth2/authorize?" + urlencode({
        "response_type":"code", "client_id":client_id, "scope":"identify guilds.members.read",
        "redirect_uri":callback, "state":ticket, "prompt":"consent"
    })
    response = RedirectResponse(url, status_code=303)
    response.set_cookie(COOKIE, ticket, max_age=600, secure=True, httponly=True,
                        samesite="lax", path="/v3/staff/auth")
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/v3/staff/auth/callback", include_in_schema=False)
async def staff_oauth_callback(request: Request, code: str = "", state: str = "", iss: str = ""):
    from app import main
    config = oauth_config()
    if main.DB is None or config is None:
        return page("Login unavailable", "Discord OAuth client credentials are not configured.", 503)
    if iss and iss != "https://discord.com":
        return page("Verification failed", "Invalid Discord OAuth issuer.", 403)
    cookie = request.cookies.get(COOKIE, "")
    if not (TOKEN_RE.fullmatch(state) and hmac.compare_digest(state, cookie)
            and code and len(code) < 2048):
        return page("Invalid login session", "Open a new staff login from Discord.", 403)
    async with main.DB.acquire() as conn:
        pending = await conn.fetchrow(
            "SELECT userid,guild_id,eligible_role_ids,owner_override FROM public.staff_auth_requests "
            "WHERE token_hash=$1 AND expires_at>now() AND consumed_at IS NULL", state_hash(state))
    if pending is None:
        return page("Login expired", "Start a fresh login from Discord.", 403)
    cid, client_secret, redirect = config
    try:
        async with httpx.AsyncClient(timeout=12,follow_redirects=False) as client:
            token_response = await client.post(DISCORD_API+"/oauth2/token",data={
                "client_id":cid,"client_secret":client_secret,"grant_type":"authorization_code",
                "code":code,"redirect_uri":redirect
            })
            if token_response.status_code != 200:
                return page("Discord verification failed","Please retry your authorization.",403)
            body = token_response.json()
            access_token = body.get("access_token") if isinstance(body,dict) else None
            if not isinstance(access_token,str) or not access_token:
                return page("Discord verification failed","Unable to verify OAuth access.",403)
            headers={"Authorization":"Bearer "+access_token}
            identity = await client.get(DISCORD_API+"/users/@me",headers=headers)
            if identity.status_code != 200:
                return page("Discord verification failed","Unable to check Discord identity.",403)
            user = identity.json()
            if not isinstance(user,dict) or str(user.get("id","")) != pending["userid"]:
                log.warning("staff oauth identity mismatch")
                return page("Wrong Discord account","Sign in with the same account that opened the staff panel.",403)
            membership = await client.get(
                DISCORD_API+"/users/@me/guilds/"+pending["guild_id"]+"/member",headers=headers)
            if membership.status_code != 200:
                return page("Not authorised","You must be a member of the staff server.",403)
            member = membership.json()
            if not isinstance(member,dict):
                return page("Not authorised","Your membership could not be verified.",403)
            permitted = pending["eligible_role_ids"]
            if isinstance(permitted,str):
                permitted=json.loads(permitted)
            permitted = {str(role) for role in permitted} if isinstance(permitted,list) else set()
            current = {str(role) for role in member.get("roles",[]) or []}
            if not pending["owner_override"] and not (current & permitted):
                return page("Not authorised","You do not have a permitted staff role.",403)
    except (httpx.HTTPError,ValueError,KeyError,TypeError) as exc:
        log.warning("staff oauth validation failure (%s)",type(exc).__name__)
        return page("Discord unavailable","Please try again from Discord.",503)
    name = str(user.get("username") or "")[:100]
    display = str(user.get("global_name") or name)[:100]
    try:
        async with main.DB.acquire() as conn:
            async with conn.transaction():
                claimed = await conn.fetchval(
                    "UPDATE public.staff_auth_requests SET consumed_at=now() "
                    "WHERE token_hash=$1 AND consumed_at IS NULL AND expires_at>now() RETURNING userid",
                    state_hash(state))
                if claimed is None:
                    return page("Login already used","Request a new login in Discord.",403)
                await conn.execute(
                    "INSERT INTO public.staff_user_profile "
                    "(userid,registered,registered_at,last_authenticated_at,guild_id,username,display_name) "
                    "VALUES($1,true,now(),now(),$2,$3,$4) "
                    "ON CONFLICT(userid) DO UPDATE SET registered=true, "
                    "registered_at=COALESCE(staff_user_profile.registered_at,now()), "
                    "last_authenticated_at=now(),guild_id=EXCLUDED.guild_id, "
                    "username=EXCLUDED.username,display_name=EXCLUDED.display_name",
                    claimed,pending["guild_id"],name,display)
                await conn.execute(
                    "INSERT INTO public.staff_panel_audit_log "
                    "(actor_userid,guild_id,action,result,metadata) "
                    "VALUES($1,$2,'oauth_registered','success',$3::jsonb)",
                    claimed,pending["guild_id"],json.dumps({"method":"discord_oauth2"}))
    except Exception:
        log.exception("staff oauth registration could not be saved")
        return page("Registration failed","Please try again or contact staff.",503)
    done=page("Authentication complete","Staff verification succeeded. Return to Discord and press Check Login.")
    done.delete_cookie(COOKIE,path="/v3/staff/auth")
    return done
