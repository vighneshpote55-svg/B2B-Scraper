# 🛡️ LeadMap Pro — Supabase Security & Deployment Guide (Phase 3)

> **Document Version:** 1.0.0  
> **Target Audience:** System Administrators, DevOps Engineers, and Security Auditors  
> **Scope:** Row Level Security (RLS), Privilege Hardening, Key Management, and Deployment Verification  

---

## 1. Overview & Threat Model

In earlier iterations of the repository, [`supabase_schema.sql`](file:///home/vighnesh/Documents/B2B/supabase_schema.sql) contained broad Row Level Security (RLS) policies granting `FOR ALL` access to `anon`, `authenticated`, and `service_role` on `public.leads` and `public.scrape_history`:

```sql
-- INSECURE LEGACY POLICY (DO NOT USE)
CREATE POLICY "Allow anon read and insert on leads"
    ON public.leads FOR ALL
    TO anon, authenticated, service_role
    USING (true) WITH CHECK (true);
```

### The Threat
Because Supabase public publishable keys (`anon`) are distributed in web applications or discovered via network requests, any unauthenticated attacker with knowledge of your Supabase URL and public anon key could send PostgREST HTTP requests:
- **Data Exfiltration**: `GET /rest/v1/leads` or `GET /rest/v1/scrape_history` dumps all proprietary business intelligence and search targets.
- **Data Tampering & Destruction**: `DELETE /rest/v1/leads` or `DELETE /rest/v1/scrape_history` wipes out leads and historical scrape runs.

---

## 2. Hardened Architecture (Phase 3)

LeadMap Pro operates under a **backend-mediated service architecture**:
1. Client browsers communicate **only** with the authenticated local dashboard (`dashboard.py` at `127.0.0.1:3000`).
2. Browser clients **never** make direct PostgREST calls to Supabase.
3. All cloud database queries, lead upserts, and historical synchronization occur server-side in Python (`scripts/supabase_client.py`).
4. Therefore, the cloud database is locked down with **least-privilege policies restricted strictly to `service_role`**:
   - `anon` has **zero** table privileges and zero RLS policies.
   - `authenticated` has **zero** table privileges and zero RLS policies.
   - `service_role` retains administrative CRUD privileges for backend syncing.

---

## 3. Applying the Hardening Migration to Already-Deployed Databases

> [!IMPORTANT]
> Modifying SQL files in Git **does not automatically update an existing, live database**.
> If you have already deployed LeadMap Pro with Supabase, you must execute the migration manually.

### Step-by-Step Migration Instructions:
1. Open the [Supabase Cloud Dashboard](https://supabase.com/dashboard) and select your project.
2. In the left navigation bar, click on **SQL Editor**.
3. Click **New Query**.
4. Open [`migrations/002_harden_supabase_rls_phase3.sql`](file:///home/vighnesh/Documents/B2B/migrations/002_harden_supabase_rls_phase3.sql), copy the entire SQL script, and paste it into the editor.
5. Click **Run** (or press `Cmd+Enter` / `Ctrl+Enter`).
6. Confirm that the query completes with `Success. No rows returned`.

---

## 4. Verifying Deployed Security Policies & Grants

After applying the migration, verify your database posture by running [`docs/SUPABASE_SECURITY_VERIFICATION.sql`](file:///home/vighnesh/Documents/B2B/docs/SUPABASE_SECURITY_VERIFICATION.sql) in the Supabase SQL Editor.

### Key Verification Checks:

#### 1. Confirm RLS is Active on All Public Tables
```sql
SELECT tablename, rowsecurity FROM pg_tables
WHERE schemaname = 'public' AND tablename IN ('leads', 'scrape_history', 'users');
```
*Expected Result:* `rowsecurity = true` for all three tables.

#### 2. Confirm No Policies Target `anon` or `authenticated`
```sql
SELECT count(*) AS unhardened_policy_count FROM pg_policies
WHERE schemaname = 'public'
  AND tablename IN ('leads', 'scrape_history', 'users')
  AND ('anon' = ANY(roles) OR 'authenticated' = ANY(roles) OR 'public' = ANY(roles));
```
*Expected Result:* `unhardened_policy_count = 0`.

#### 3. Confirm Direct Grants are Revoked from `anon` and `authenticated`
```sql
SELECT grantee, table_name, privilege_type FROM information_schema.role_table_grants
WHERE table_schema = 'public'
  AND table_name IN ('leads', 'scrape_history', 'users')
  AND grantee IN ('anon', 'authenticated');
```
*Expected Result:* Empty set (0 rows).

---

## 5. Server-Side Key Management & Configuration

1. In Supabase Dashboard, navigate to **Project Settings** ➜ **API**.
2. Locate the **Project API keys** section.
3. Copy the **`service_role` secret key** (revealed by clicking *Reveal*).
4. Update your local `.env` file:
   ```bash
   SUPABASE_URL=https://xxxxxxxx.supabase.co
   SUPABASE_KEY=eyJh... (your private service_role key)
   SUPABASE_TABLE=leads
   SUPABASE_AUTO_SYNC=true
   ```
5. **Never commit `.env` to source control.** Verify `.gitignore` contains `.env`.
6. **Never distribute the `service_role` key to browser clients.** `dashboard.py` exposes only high-level connection indicators and never returns secret keys over the API.
