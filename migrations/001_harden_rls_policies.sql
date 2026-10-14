-- ==============================================================================
-- Migration: 001_harden_rls_policies.sql
-- Description: Revoke anonymous & untrusted public access to public.users,
--              public.leads, and public.scrape_history tables.
--              Enforces strict server-side service_role policies to prevent
--              unauthorized client data access or password hash leakage.
-- Author: LeadMap Pro Security Remediation (Phase 1B)
-- ==============================================================================

-- 1. Ensure RLS is active on all core tables
ALTER TABLE IF EXISTS public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.scrape_history ENABLE ROW LEVEL SECURITY;

-- 2. Drop legacy wide-open and permissive policies on public.users
DROP POLICY IF EXISTS "Allow anon read and write on users" ON public.users;
DROP POLICY IF EXISTS "Allow anon read on users" ON public.users;
DROP POLICY IF EXISTS "Allow anon write on users" ON public.users;
DROP POLICY IF EXISTS "Allow authenticated read and write on users" ON public.users;
DROP POLICY IF EXISTS "Strict service role only on users" ON public.users;
DROP POLICY IF EXISTS "Service role full access on users" ON public.users;

-- 3. Drop legacy wide-open and permissive policies on public.leads
DROP POLICY IF EXISTS "Allow anon read and insert on leads" ON public.leads;
DROP POLICY IF EXISTS "Allow anon read and write on leads" ON public.leads;
DROP POLICY IF EXISTS "Allow anon read on leads" ON public.leads;
DROP POLICY IF EXISTS "Allow anon insert on leads" ON public.leads;
DROP POLICY IF EXISTS "Allow authenticated read and insert on leads" ON public.leads;
DROP POLICY IF EXISTS "Allow authenticated read and write on leads" ON public.leads;
DROP POLICY IF EXISTS "Allow service_role full control on leads" ON public.leads;
DROP POLICY IF EXISTS "Service role full access on leads" ON public.leads;
DROP POLICY IF EXISTS "Strict service role only on leads" ON public.leads;

-- 4. Drop legacy wide-open and permissive policies on public.scrape_history
DROP POLICY IF EXISTS "Allow anon read and write on scrape_history" ON public.scrape_history;
DROP POLICY IF EXISTS "Allow anon read on scrape_history" ON public.scrape_history;
DROP POLICY IF EXISTS "Allow anon write on scrape_history" ON public.scrape_history;
DROP POLICY IF EXISTS "Allow authenticated read and write on scrape_history" ON public.scrape_history;
DROP POLICY IF EXISTS "Service role full access on scrape_history" ON public.scrape_history;
DROP POLICY IF EXISTS "Strict service role only on scrape_history" ON public.scrape_history;

-- 5. Revoke direct table permissions from untrusted public roles (anon and authenticated)
-- Direct client access via PostgREST is blocked; all traffic flows through backend server
REVOKE ALL ON TABLE public.users FROM anon;
REVOKE ALL ON TABLE public.users FROM authenticated;
REVOKE ALL ON TABLE public.leads FROM anon;
REVOKE ALL ON TABLE public.leads FROM authenticated;
REVOKE ALL ON TABLE public.scrape_history FROM anon;
REVOKE ALL ON TABLE public.scrape_history FROM authenticated;

-- 6. Grant full administrative privileges strictly to backend service_role
GRANT ALL ON TABLE public.users TO service_role;
GRANT ALL ON TABLE public.leads TO service_role;
GRANT ALL ON TABLE public.scrape_history TO service_role;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO service_role;

-- 7. Define least-privilege policies restricted strictly to backend service_role
CREATE POLICY "Strict service role only on users"
    ON public.users
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

CREATE POLICY "Strict service role only on leads"
    ON public.leads
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

CREATE POLICY "Strict service role only on scrape_history"
    ON public.scrape_history
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- Done. Run this migration in Supabase SQL Editor if applying to a database.

