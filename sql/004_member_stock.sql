-- One private row storing the last validated member stock snapshot.
CREATE TABLE IF NOT EXISTS public.member_stock (
 id integer PRIMARY KEY DEFAULT 1 CHECK (id = 1),
 offline_members bigint CHECK (offline_members IS NULL OR offline_members >= 0),
 online_members bigint CHECK (online_members IS NULL OR online_members >= 0),
 provider_success boolean NOT NULL DEFAULT false,
 source_url text NOT NULL DEFAULT 'https://176.100.37.219/api/v2/stock',
 last_synced_at timestamptz,
 last_attempt_at timestamptz,
 last_error text,
 updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.member_stock
 ADD COLUMN IF NOT EXISTS source_url text NOT NULL DEFAULT 'https://176.100.37.219/api/v2/stock',
 ADD COLUMN IF NOT EXISTS last_attempt_at timestamptz,
 ADD COLUMN IF NOT EXISTS last_error text,
 ADD COLUMN IF NOT EXISTS provider_success boolean NOT NULL DEFAULT false;
INSERT INTO public.member_stock(id) VALUES(1) ON CONFLICT(id) DO NOTHING;
ALTER TABLE public.member_stock ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.member_stock FROM PUBLIC, anon, authenticated;
