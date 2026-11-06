-- Private staff registration, single-use OAuth state, and audit history.
CREATE TABLE IF NOT EXISTS public.staff_user_profile (
 userid text PRIMARY KEY CHECK (userid ~ '^[0-9]{17,20}$'),
 registered boolean NOT NULL DEFAULT false,
 registered_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(),
 last_authenticated_at timestamptz,
 guild_id text NOT NULL,
 username text,
 display_name text,
 CONSTRAINT staff_registered_date CHECK (NOT registered OR registered_at IS NOT NULL)
);
CREATE TABLE IF NOT EXISTS public.staff_auth_requests (
 token_hash text PRIMARY KEY CHECK (length(token_hash)=64),
 userid text NOT NULL CHECK (userid ~ '^[0-9]{17,20}$'),
 guild_id text NOT NULL CHECK (guild_id ~ '^[0-9]{17,20}$'),
 eligible_role_ids jsonb NOT NULL DEFAULT '[]'::jsonb CHECK(jsonb_typeof(eligible_role_ids)='array'),
 owner_override boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL DEFAULT now(),
 expires_at timestamptz NOT NULL,
 consumed_at timestamptz,
 CONSTRAINT staff_auth_lifetime CHECK(expires_at>created_at AND expires_at<=created_at+interval '15 minutes')
);
CREATE INDEX IF NOT EXISTS staff_auth_user_expiry_idx ON public.staff_auth_requests(userid,expires_at DESC);
CREATE TABLE IF NOT EXISTS public.staff_panel_audit_log (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 actor_userid text NOT NULL,
 guild_id text,
 action text NOT NULL,
 result text NOT NULL CHECK(result IN ('allowed','denied','success','failed','started')),
 metadata jsonb NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(metadata)='object'),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS staff_audit_actor_created_idx ON public.staff_panel_audit_log(actor_userid,created_at DESC);
ALTER TABLE public.staff_user_profile ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.staff_auth_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.staff_panel_audit_log ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.staff_user_profile,public.staff_auth_requests,public.staff_panel_audit_log FROM PUBLIC,anon,authenticated;
REVOKE ALL ON SEQUENCE public.staff_panel_audit_log_id_seq FROM PUBLIC,anon,authenticated;
