# Saphire Shoppex Dynamic Delivery
Two signed Shoppex dynamic product URLs:
- POST https://api.saphire.rip/v2/resellers/order/members/offline
- POST https://api.saphire.rip/v2/resellers/order/members/online

After a valid request, the API logs the order in Supabase, requests a Discorium key (one attempt only), stores the result, and responds with a human-readable `text/plain` message (HTTP 200) containing a thank-you line, redemption instructions, and `https://memberix.live/redeem   KEY: PROVIDER-KEY` on the third line (no JSON). The same text is returned on successful retries.. Repeated requests for completed orders return the existing key. Unknown outcomes need manual investigation to prevent duplicate provider charges.

## Configure Railway
Set `DATABASE_URL`, `SHOPPEX_OFFLINE_SIGNING_SECRET`, `SHOPPEX_ONLINE_SIGNING_SECRET`, and `DISCORIUM_API_KEY` in Railway Variables. Do not commit them.

Supabase schema is in `sql/001_initial.sql` (already applied to the connected database). The database connection should use a secure Supabase Postgres connection URL and `sslmode=require`.

Shoppex requires a signed request with `X-Shoppex-Signature-V2`, `X-Shoppex-Timestamp`, and `X-Shoppex-Delivery`. Signature is HMAC-SHA256 of `deliveryId.timestamp.rawBody`. The first secret supplied by the user was interpreted as offline; the second as online (both were labelled offline in the original message).

Shoppex dynamic payload example: `{"order":"INV-XXXX","product":"prod_2001","quantity":100}`. A custom Discord server field is recorded if present under a recognised key.

## Deployment
Connect GitHub repo `bouvty/Saphire-dynamic-` to Railway service `delivery-api`. Map domain `api.saphire.rip` to the service and configure the DNS records Railway supplies. Run a signed test with quantity 1 only after all secrets and database access are configured. Health route: `GET /health`.

## Safety
Never expose provider keys to clients except delivered per-order keys. SQL table has RLS enabled and no anon/authenticated grants. Provider timeouts are not automatically retried.


## Guild-specific stock checking (public API)
GET `https://api.saphire.rip/v2/stock/members/check/guild?guild_id=1521285253204021389`\n\nThe `guild_id` query value may also be an official invite URL, such as `https://discord.gg/example` (URL-encoded). The API first calls Discord's Get Invite endpoint, extracts the verified guild ID, and then calls the provider. Discoverable guild IDs can be verified without a bot token; private guilds require a valid invite or a bot in the guild. Unknown/inaccessible numeric IDs are not automatically declared nonexistent. The frontend preview generates the canonical route `/stock/check/guild?guild_id=<resolved_id>`; hosting this single-page preview requires a rewrite from `/stock/check/guild` to `/index.html`.

A Discord snowflake is checked for plausible format and timestamp before any network request. The backend then uses the official Discord API to verify the guild with a bot token, falling back to Discord's preview endpoint for discoverable guilds. Only when Discord verifies the guild does the API call the provider's `/api/v2/stock/guild` endpoint and return validated online/offline/total stock plus a Discord-confirmed `guild_name`.

**Setup required:** Add `DISCORD_BOT_TOKEN` as a private Railway variable on the `delivery-api` service. Use a fresh bot token. The bot must be in private guilds to verify them, or the server must be discoverable to use the preview API. The bot token is never passed to the website. If Discord cannot verify a server, the API does not contact the provider. A 404 may mean a guild is inaccessible rather than nonexistent. If the token is not configured, the endpoint responds HTTP 503 with a clear error. The main stock endpoint and the existing Shoppex delivery system are independent.
