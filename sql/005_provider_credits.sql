-- Records the latest reliable credit balance returned by the order provider.
-- Run once on the Saphire Supabase database. Existing orders are preserved.
CREATE TABLE IF NOT EXISTS public.provider_credits (
    members_type text PRIMARY KEY CHECK (members_type IN ('online','offline')),
    credits_remaining bigint CHECK (credits_remaining IS NULL OR credits_remaining >= 0),
    last_order_id bigint REFERENCES public.delivery_orders(id) ON DELETE SET NULL,
    updated_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.provider_credits ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.provider_credits FROM PUBLIC, anon, authenticated;
INSERT INTO public.provider_credits (members_type)
VALUES ('online'), ('offline')
ON CONFLICT (members_type) DO NOTHING;
WITH history AS (
 SELECT DISTINCT ON (members_type)
     members_type,
     id AS last_order_id,
     (provider_response->>'credits_remaining')::bigint AS credits_remaining,
     updated_at
 FROM public.delivery_orders
 WHERE status='success'
     AND jsonb_typeof(provider_response->'credits_remaining')='number'
     AND provider_response->>'credits_remaining' ~ '^[0-9]{1,18}$'
 ORDER BY members_type, updated_at DESC, id DESC
)
UPDATE public.provider_credits pc
SET credits_remaining=h.credits_remaining,
    last_order_id=h.last_order_id,
    updated_at=h.updated_at
FROM history h
WHERE pc.members_type=h.members_type
    AND (pc.updated_at IS NULL OR pc.updated_at < h.updated_at);
CREATE INDEX IF NOT EXISTS provider_credits_updated_at_idx
ON public.provider_credits(updated_at DESC);
