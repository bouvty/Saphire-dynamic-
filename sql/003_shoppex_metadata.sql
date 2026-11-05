-- Preserve searchable Shoppex fields and the entire sanitized delivery payload.
ALTER TABLE public.delivery_orders
  ADD COLUMN IF NOT EXISTS shoppex_delivery_id text,
  ADD COLUMN IF NOT EXISTS shoppex_product_id text,
  ADD COLUMN IF NOT EXISTS shoppex_invoice_id text,
  ADD COLUMN IF NOT EXISTS shoppex_customer_email text,
  ADD COLUMN IF NOT EXISTS shoppex_event text;
CREATE INDEX IF NOT EXISTS delivery_orders_delivery_id_idx ON public.delivery_orders(shoppex_delivery_id);
CREATE INDEX IF NOT EXISTS delivery_orders_invoice_idx ON public.delivery_orders(shoppex_invoice_id);
COMMENT ON COLUMN public.delivery_orders.request_payload IS
  'Sanitized Shoppex order JSON captured at delivery time; server-side only';
