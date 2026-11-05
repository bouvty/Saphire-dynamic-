CREATE TABLE IF NOT EXISTS public.delivery_orders (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  shoppex_order_id text NOT NULL,
  line_item_id text NOT NULL,
  members_type text NOT NULL CHECK(members_type IN ('online','offline')),
  quantity integer NOT NULL CHECK(quantity BETWEEN 1 AND 100000),
  discord_server text,
  provider_order_id text NOT NULL UNIQUE,
  provider_key text,
  status text NOT NULL CHECK(status IN ('processing','success','failed','unknown')),
  error_message text,
  request_payload jsonb,
  provider_response jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(shoppex_order_id,line_item_id,members_type)
);
CREATE INDEX IF NOT EXISTS delivery_orders_status_idx ON public.delivery_orders(status,created_at DESC);
ALTER TABLE public.delivery_orders ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.delivery_orders FROM anon, authenticated;
