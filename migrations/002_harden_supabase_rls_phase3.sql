-- ==============================================================================
-- Migration: 002_harden_supabase_rls_phase3.sql
-- Description: Revoke all permissive anonymous and authenticated access to
--              leads, scrape_history, and users tables.
--              Enforce least-privilege server-side service_role policies.
-- Author: LeadMap Pro Security Remediation (Phase 3: Supabase Security)
-- ==============================================================================

-- 1. Ensure Row Level Security (RLS) is enabled on all tables
ALTER TABLE IF EXISTS public.leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.scrape_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.users ENABLE ROW LEVEL SECURITY;

-- 2. Drop all legacy permissive policies on public.leads
DROP POLICY IF EXISTS "Allow anon read and insert on leads" ON public.leads;
DROP POLICY IF EXISTS "Allow anon read and write on leads" ON public.leads;
DROP POLICY IF EXISTS "Allow anon read on leads" ON public.leads;
DROP POLICY IF EXISTS "Allow anon insert on leads" ON public.leads;
DROP POLICY IF EXISTS "Allow authenticated read and insert on leads" ON public.leads;
DROP POLICY IF EXISTS "Allow service_role full control on leads" ON public.leads;
DROP POLICY IF EXISTS "Service role full access on leads" ON public.leads;

-- 3. Drop all legacy permissive policies on public.scrape_history
DROP POLICY IF EXISTS "Allow anon read and write on scrape_history" ON public.scrape_history;
DROP POLICY IF EXISTS "Allow anon read on scrape_history" ON public.scrape_history;
DROP POLICY IF EXISTS "Allow anon write on scrape_history" ON public.scrape_history;
DROP POLICY IF EXISTS "Allow authenticated read and write on scrape_history" ON public.scrape_history;
DROP POLICY IF EXISTS "Service role full access on scrape_history" ON public.scrape_history;

-- 4. Drop all legacy policies on public.users
DROP POLICY IF EXISTS "Allow anon read and write on users" ON public.users;
DROP POLICY IF EXISTS "Allow anon read on users" ON public.users;
DROP POLICY IF EXISTS "Allow anon write on users" ON public.users;
DROP POLICY IF EXISTS "Allow authenticated read and write on users" ON public.users;
DROP POLICY IF EXISTS "Strict service role only on users" ON public.users;
DROP POLICY IF EXISTS "Service role full access on users" ON public.users;

-- 5. Revoke direct table permissions from untrusted public roles (anon and authenticated)
-- This blocks direct PostgREST table access from client browsers or public keys.
REVOKE ALL ON TABLE public.leads FROM anon, authenticated;
REVOKE ALL ON TABLE public.scrape_history FROM anon, authenticated;
REVOKE ALL ON TABLE public.users FROM anon, authenticated;

-- 6. Grant administrative permissions strictly to backend service_role
GRANT ALL ON TABLE public.leads TO service_role;
GRANT ALL ON TABLE public.scrape_history TO service_role;
GRANT ALL ON TABLE public.users TO service_role;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO service_role;

-- 7. Define least-privilege policies restricted strictly to service_role
-- Only the backend server possessing the private service_role key can read, insert,
-- update, or delete data in these tables.

CREATE POLICY "Service role full access on leads"
    ON public.leads
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

CREATE POLICY "Service role full access on scrape_history"
    ON public.scrape_history
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

CREATE POLICY "Service role full access on users"
    ON public.users
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- ==============================================================================
-- End of Migration 002
-- ==============================================================================
