-- Saphire V3 reseller order record metadata
-- Applied to Supabase on 2026-11-06; retained here for reproducible setup.
ALTER TABLE public.resellers_orders
 ADD COLUMN IF NOT EXISTS shoppex_data jsonb,
 ADD COLUMN IF NOT EXISTS shoppex_product_id text,
 ADD COLUMN IF NOT EXISTS shoppex_customer_email text,
 ADD COLUMN IF NOT EXISTS shoppex_event text;
CREATE INDEX IF NOT EXISTS reseller_orders_invoice_lookup_idx
 ON public.resellers_orders(reseller_user_id, shoppex_invoice_id);
COMMENT ON COLUMN public.resellers_orders.shoppex_data IS
 'Redacted Shoppex request JSON; private backend/database access only';
COMMENT ON COLUMN public.resellers_orders.shoppex_customer_email IS
 'Private customer information; do not expose to other resellers';
ALTER TABLE public.resellers_orders ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.resellers_orders FROM PUBLIC, anon, authenticated;
