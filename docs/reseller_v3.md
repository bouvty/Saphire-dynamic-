# Saphire Reseller V3 — Private Shoppex Dynamic Delivery

## Routes (POST only)

- `https://api.saphire.rip/v3/resellers/order/members/online`
- `https://api.saphire.rip/v3/resellers/order/members/offline`

Both accept the same Shoppex dynamic-product POST JSON/signature format as the V2 delivery endpoints, but require one of the corresponding reseller-specific Shoppex signing secrets stored in `public.resellers_accounts`. **Never call these directly with an API key or a user ID**; the backend determines the reseller exclusively by matching Shoppex's signed raw request. Never expose secrets on websites, bot messages or public API responses.

## Account management

The private `public.resellers_accounts` table stores:
`user_id` (typically their Discord snowflake as text), `created_at`,
`shoppex_signing_secret_online`, `shoppex_signing_secret_offline`,
`credit_online`, `credit_offline`, `active` and `updated_at`.

A reseller must be provisioned **by an authorized administrator**. No reseller accounts are created automatically by arbitrary signed requests. Secrets are server-side database credentials and should be entered only through the administrator's secure Supabase environment; **do not send them through ChatGPT or commit them to source control**.

Example schema for an authorized provisioning transaction (replace the placeholders securely; do not paste live credentials in issues or logs):

```sql
BEGIN;
INSERT INTO public.resellers_accounts
(user_id,shoppex_signing_secret_online,shoppex_signing_secret_offline,credit_online,credit_offline)
VALUES ('YOUR_RESELLER_DISCORD_ID','YOUR_ONLINE_SHOPPEX_SIGNING_SECRET','YOUR_OFFLINE_SHOPPEX_SIGNING_SECRET',0,0);
COMMIT;
```

Each online order deducts exactly its requested member quantity from `credit_online`; offline orders deduct from `credit_offline`. **1 member = 1 credit**. The account row is locked `FOR UPDATE` to prevent two simultaneous deliveries from overspending. The order record and debit ledger entry are committed in the same transaction. Changing balances is an administrator-only database action; reconcile balance changes against your customer payments.

## Delivery response

On success (HTTP 200, `text/plain`):

```text
Thank you for your purchase ❤️
please use our redeem panel below to redeem your key, follow the steps provided.
https://memberix.live/redeem     KEY: <actual provider key>
```

Insufficient credits (HTTP 409, `text/plain`):
```text
Sorry but this reseller does not have the amount of credits to make this order, please contact support on this store or join discord.gg/saphire
```

Every other error responds with `Error` as `text/plain` with an appropriate non-2xx HTTP status. Do not return HTTP 200 for an unfulfilled order; Shoppex might otherwise treat it as a successful digital delivery.

## Authentication / safety

- Verify Shoppex `X-Shoppex-Signature-V2` HMAC-SHA256 with the correct reseller/online-or-offline signing secret.
- Validate `X-Shoppex-Timestamp` (five-minute window), signed delivery ID, unaltered raw payload, length limit and JSON fields.
- There are no endpoints that mint keys based solely on a user ID, posted credit quantity or a client-supplied key.
- Per-reseller/order/line/type unique constraint prevents charging credits twice or generating multiple keys on ordinary duplicate deliveries.
- Existing successful orders replay the same key without another provider request.
- Definite provider refusal refunds reserved credits atomically and logs the refund.
- Provider timeouts, HTTP 429/5xx or success without a key are marked `unknown` and **retain reserved credits**. Do not automatically retry/reissue a key: inspect the provider by `provider_order_id` and manually reconcile.
- Private RLS is enabled and PUBLIC/anon/authenticated database grants have been revoked on all three tables. The database connection must use a trusted backend role with access; never put backend database credentials in a client.
- No reseller credentials, provider API keys, customer emails or generated redemption keys are included in error logs.

## Setup

1. Create a Shoppex dynamic product for online members and one for offline members.
2. Set the matching online/offline callback endpoint above.
3. Securely obtain the actual signing secret for each Shoppex webhook.
4. Have an authorized Saphire administrator insert a private reseller account row with the two secrets.
5. Allocate separate online and offline credits only after confirming the reseller's payment.
6. Send one real Shoppex test purchase for each type to confirm signature verification, a single credit deduction and a single provider key.
7. Check `public.resellers_orders` and `public.resellers_credit_ledger` for the expected order/reserve record; retry the same signed order and confirm there is no second debit.
8. Verify an order larger than the balance returns HTTP 409 without invoking the provider.

If the reseller has no account or their signed secret isn't in the database yet, the V3 endpoint rejects the request. Existing V2 Shoppex routes are unchanged.
