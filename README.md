# Saphire Shoppex Dynamic Delivery
Two signed Shoppex dynamic product URLs:
- POST https://api.saphire.rip/v2/resellers/order/members/offline
- POST https://api.saphire.rip/v2/resellers/order/members/online

After a valid request, the API logs the order in Supabase, requests a Discorium key (one attempt only), stores the result, and responds with a human-readable `text/plain` message (HTTP 200) containing a thank-you line, redemption instructions, `https://memberix.live/redeem`, and `key - PROVIDER-KEY` (no JSON). The same text is returned on successful retries.. Repeated requests for completed orders return the existing key. Unknown outcomes need manual investigation to prevent duplicate provider charges.

## Configure Railway
Set `DATABASE_URL`, `SHOPPEX_OFFLINE_SIGNING_SECRET`, `SHOPPEX_ONLINE_SIGNING_SECRET`, and `DISCORIUM_API_KEY` in Railway Variables. Do not commit them.

Supabase schema is in `sql/001_initial.sql` (already applied to the connected database). The database connection should use a secure Supabase Postgres connection URL and `sslmode=require`.

Shoppex requires a signed request with `X-Shoppex-Signature-V2`, `X-Shoppex-Timestamp`, and `X-Shoppex-Delivery`. Signature is HMAC-SHA256 of `deliveryId.timestamp.rawBody`. The first secret supplied by the user was interpreted as offline; the second as online (both were labelled offline in the original message).

Shoppex dynamic payload example: `{"order":"INV-XXXX","product":"prod_2001","quantity":100}`. A custom Discord server field is recorded if present under a recognised key.

## Deployment
Connect GitHub repo `bouvty/Saphire-dynamic-` to Railway service `delivery-api`. Map domain `api.saphire.rip` to the service and configure the DNS records Railway supplies. Run a signed test with quantity 1 only after all secrets and database access are configured. Health route: `GET /health`.

## Safety
Never expose provider keys to clients except delivered per-order keys. SQL table has RLS enabled and no anon/authenticated grants. Provider timeouts are not automatically retried.
