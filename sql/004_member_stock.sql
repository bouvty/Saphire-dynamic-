-- Private, singleton cache of provider-supplied online/offline member inventory.
CREATE TABLE IF NOT EXISTS public.member_stock (
 id smallint PRIMARY KEY DEFAULT 1 CHECK(id=1),
 offline_members integer CHECK(offline_members >= 0),
 online_members integer CHECK(online_members >= 0),
 success boolean NOT NULL DEFAULT false,
 source_url text NOT NULL DEFAULT 'https://176.100.37.219/api/v2/stock',
 last_synced_at timestamptz,
 last_attempt_at timestamptz,
 last_error text,
 updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO public.member_stock(id,success) VALUES(1,false) ON CONFLICT(id) DO NOTHING;
ALTER TABLE public.member_stock ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.member_stock FROM PUBLIC,anon,authenticated;
