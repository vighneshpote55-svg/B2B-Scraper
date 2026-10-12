-- ==============================================================================
-- LeadMap Pro — Supabase Security Verification Queries
-- ==============================================================================
-- Run these queries in your Supabase Dashboard:
-- SQL Editor -> New Query -> Paste and click "Run"
--
-- These queries verify that:
-- 1. Row Level Security (RLS) is ENABLED on leads, scrape_history, and users.
-- 2. No permissive policies exist for 'anon' or 'authenticated' roles.
-- 3. Direct table permissions are REVOKED from 'anon' and 'authenticated'.
-- 4. 'service_role' is the ONLY role granted table and policy access.
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- CHECK 1: Verify Row Level Security is active on public tables
-- Expected: rowsecurity = true for leads, scrape_history, and users.
-- ------------------------------------------------------------------------------
SELECT
    tablename,
    rowsecurity AS rls_enabled
FROM pg_tables
WHERE schemaname = 'public'
  AND tablename IN ('leads', 'scrape_history', 'users')
ORDER BY tablename;

-- ------------------------------------------------------------------------------
-- CHECK 2: Inspect active RLS policies
-- Expected: Only policies targeting 'service_role' should exist.
-- Zero policies should target 'anon', 'authenticated', or 'public'.
-- ------------------------------------------------------------------------------
SELECT
    schemaname,
    tablename,
    policyname,
    permissive,
    roles,
    cmd,
    qual,
    with_check
FROM pg_policies
WHERE schemaname = 'public'
  AND tablename IN ('leads', 'scrape_history', 'users')
ORDER BY tablename, policyname;

-- ------------------------------------------------------------------------------
-- CHECK 3: Assert ZERO permissive policies exist for anon or authenticated
-- Expected: count = 0. If > 0, legacy permissive policies are still active!
-- ------------------------------------------------------------------------------
SELECT
    count(*) AS unhardened_policy_count
FROM pg_policies
WHERE schemaname = 'public'
  AND tablename IN ('leads', 'scrape_history', 'users')
  AND (
      'anon' = ANY(roles)
      OR 'authenticated' = ANY(roles)
      OR 'public' = ANY(roles)
  );

-- ------------------------------------------------------------------------------
-- CHECK 4: Inspect table grants in PostgreSQL catalogs
-- Expected: 'anon' and 'authenticated' must have ZERO privileges on these tables.
-- 'service_role' must have ALL privileges.
-- ------------------------------------------------------------------------------
SELECT
    grantee,
    table_name,
    privilege_type
FROM information_schema.role_table_grants
WHERE table_schema = 'public'
  AND table_name IN ('leads', 'scrape_history', 'users')
  AND grantee IN ('anon', 'authenticated', 'service_role')
ORDER BY table_name, grantee, privilege_type;

-- ------------------------------------------------------------------------------
-- CHECK 5: Quick boolean status summary
-- All checks should return TRUE for a fully secured deployment.
-- ------------------------------------------------------------------------------
SELECT
    -- Check 1: RLS enabled on all 3 tables
    (SELECT count(*) = 3 FROM pg_tables WHERE schemaname = 'public' AND tablename IN ('leads', 'scrape_history', 'users') AND rowsecurity = true) AS all_tables_rls_enabled,
    -- Check 2: Zero policies for anon / authenticated
    (SELECT count(*) = 0 FROM pg_policies WHERE schemaname = 'public' AND tablename IN ('leads', 'scrape_history', 'users') AND ('anon' = ANY(roles) OR 'authenticated' = ANY(roles))) AS zero_untrusted_policies,
    -- Check 3: Zero table privileges for anon / authenticated
    (SELECT count(*) = 0 FROM information_schema.role_table_grants WHERE table_schema = 'public' AND table_name IN ('leads', 'scrape_history', 'users') AND grantee IN ('anon', 'authenticated')) AS zero_untrusted_grants;
