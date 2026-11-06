-- Supabase migration: V3 Shoppex reseller accounts and credit-backed orders.
-- Run through database migrations. No customer data is deleted.
CREATE TABLE IF NOT EXISTS public.resellers_accounts (
  user_id text PRIMARY KEY,
  created_at timestamptz NOT NULL DEFAULT now(),
  shoppex_signing_secret_online text NOT NULL UNIQUE CHECK(length(shoppex_signing_secret_online)>=32),
  shoppex_signing_secret_offline text NOT NULL UNIQUE CHECK(length(shoppex_signing_secret_offline)>=32),
  credit_online bigint NOT NULL DEFAULT 0 CHECK(credit_online>=0),
  credit_offline bigint NOT NULL DEFAULT 0 CHECK(credit_offline>=0),
  active boolean NOT NULL DEFAULT true,
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT reseller_different_secrets CHECK(shoppex_signing_secret_online <> shoppex_signing_secret_offline)
);
CREATE TABLE IF NOT EXISTS public.resellers_orders (
  id bigserial PRIMARY KEY,
  reseller_user_id text NOT NULL REFERENCES public.resellers_accounts(user_id) ON DELETE RESTRICT,
  shoppex_order_id text NOT NULL,
  line_item_id text NOT NULL,
  members_type text NOT NULL CHECK(members_type IN ('online','offline')),
  quantity int NOT NULL CHECK(quantity BETWEEN 1 AND 100000),
  shoppex_delivery_id text NOT NULL,
  provider_order_id text NOT NULL UNIQUE,
  provider_key text,
  status text NOT NULL CHECK(status IN ('processing','success','failed','unknown')),
  error_message text,
  provider_response jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(reseller_user_id,shoppex_order_id,line_item_id,members_type)
);
CREATE INDEX IF NOT EXISTS reseller_orders_reseller_created_idx
ON public.resellers_orders(reseller_user_id,created_at DESC);
CREATE TABLE IF NOT EXISTS public.resellers_credit_ledger (
  id bigserial PRIMARY KEY,
  reseller_user_id text NOT NULL REFERENCES public.resellers_accounts(user_id) ON DELETE RESTRICT,
  order_id bigint NOT NULL REFERENCES public.resellers_orders(id) ON DELETE RESTRICT,
  members_type text NOT NULL CHECK(members_type IN ('online','offline')),
  credit_delta bigint NOT NULL CHECK(credit_delta<>0),
  reason text NOT NULL CHECK(reason IN ('reserve','refund')),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(order_id,reason)
);
ALTER TABLE public.resellers_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.resellers_orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.resellers_credit_ledger ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.resellers_accounts,public.resellers_orders,public.resellers_credit_ledger
FROM PUBLIC,anon,authenticated;
REVOKE ALL ON SEQUENCE public.resellers_orders_id_seq,public.resellers_credit_ledger_id_seq
FROM PUBLIC,anon,authenticated;
