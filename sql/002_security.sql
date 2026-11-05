-- Saphire delivery order hardening (already applied to the linked Supabase project).
ALTER TABLE public.delivery_orders
  ADD CONSTRAINT delivery_orders_order_length_chk CHECK (char_length(shoppex_order_id) BETWEEN 1 AND 128),
  ADD CONSTRAINT delivery_orders_item_length_chk CHECK (char_length(line_item_id) BETWEEN 1 AND 128),
  ADD CONSTRAINT delivery_orders_discord_length_chk CHECK (discord_server IS NULL OR char_length(discord_server) <= 500),
  ADD CONSTRAINT delivery_orders_provider_id_format_chk CHECK (provider_order_id ~ '^dsc-[a-z0-9]{6,12}$');

-- This is a server-only table: no browser/API roles may query it.
ALTER TABLE public.delivery_orders ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.delivery_orders FROM PUBLIC, anon, authenticated;
REVOKE ALL ON SEQUENCE public.delivery_orders_id_seq FROM PUBLIC, anon, authenticated;
-- No anon/authenticated RLS policy is intentional. The backend uses privileged
-- Postgres credentials stored only as a Railway secret.
