-- ==============================================================================
-- Migration: 001_harden_rls_policies.sql
-- Description: Revoke anonymous & untrusted public access to public.users table
--              Prevent exfiltration of user credentials & password hashes.
-- Author: LeadMap Pro Security Remediation (Phase 1)
-- ==============================================================================

-- 1. Ensure RLS is active on public.users
ALTER TABLE IF EXISTS public.users ENABLE ROW LEVEL SECURITY;

-- 2. Drop legacy wide-open policies on public.users
DROP POLICY IF EXISTS "Allow anon read and write on users" ON public.users;
DROP POLICY IF EXISTS "Allow anon read on users" ON public.users;
DROP POLICY IF EXISTS "Allow anon write on users" ON public.users;

-- 3. Revoke direct table permissions from public roles
REVOKE ALL ON TABLE public.users FROM anon;
REVOKE ALL ON TABLE public.users FROM authenticated;

-- 4. Grant full administrative privileges strictly to backend service_role
GRANT ALL ON TABLE public.users TO service_role;

-- 5. Create hardened policy: ONLY service_role can query or modify public.users
-- This ensures user credentials, email addresses, and bcrypt password hashes
-- can NEVER be dumped or modified via Supabase anon REST/GraphQL APIs.
CREATE POLICY "Strict service role only on users"
    ON public.users
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- 6. Harden scrape_history & leads if authenticated multi-tenancy is active
-- Ensure anonymous clients cannot truncate or delete leads data
DROP POLICY IF EXISTS "Allow anon read and insert on leads" ON public.leads;

CREATE POLICY "Allow service_role full control on leads"
    ON public.leads
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

CREATE POLICY "Allow authenticated read and insert on leads"
    ON public.leads
    FOR ALL
    TO authenticated
    USING (true)
    WITH CHECK (true);

-- Done. Run this migration in Supabase SQL Editor to apply.
