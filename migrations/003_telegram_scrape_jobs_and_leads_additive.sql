-- ==============================================================================
-- Migration: 003_telegram_scrape_jobs_and_leads_additive.sql
-- Description: Additive Telegram scraping jobs table and lead schema enhancements.
--              - Preserves existing public.leads, public.scrape_history, public.users.
--              - Adds source metadata, place_id, cid, and analysis JSONB to public.leads.
--              - Creates public.scrape_jobs for Telegram job progress tracking.
--              - Enforces least-privilege Row Level Security (RLS) for service_role.
-- Author: LeadMap Pro Engineering (Phase 2: Additive Supabase Migration)
-- ==============================================================================

-- 1. Additive Enhancements to public.leads
-- Safely add source metadata, stable Google identifiers, and rule-based analysis columns.
ALTER TABLE IF EXISTS public.leads
    ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'web',
    ADD COLUMN IF NOT EXISTS place_id TEXT,
    ADD COLUMN IF NOT EXISTS cid TEXT,
    ADD COLUMN IF NOT EXISTS analysis JSONB DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS telegram_message_id TEXT;

CREATE INDEX IF NOT EXISTS idx_leads_place_id ON public.leads (place_id);
CREATE INDEX IF NOT EXISTS idx_leads_source ON public.leads (source);

-- 2. Create public.scrape_jobs Table
CREATE TABLE IF NOT EXISTS public.scrape_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    scraper_job_id TEXT,
    telegram_user_id TEXT,
    telegram_chat_id TEXT,
    telegram_update_id TEXT,
    query TEXT NOT NULL,
    location TEXT,
    requested_limit INTEGER DEFAULT 5,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'ok', 'failed', 'cancelled')),
    stage TEXT DEFAULT 'Queued',
    progress INTEGER DEFAULT 0 CHECK (progress >= 0 AND progress <= 100),
    lead_counts JSONB DEFAULT '{"total": 0, "hot": 0, "warm": 0, "cold": 0, "with_email": 0, "with_phone": 0}'::jsonb,
    total_leads INTEGER DEFAULT 0,
    error_summary TEXT,
    source TEXT DEFAULT 'telegram' CHECK (source IN ('telegram', 'web', 'cli', 'api')),
    metadata JSONB DEFAULT '{}'::jsonb
);

-- 3. Indexes & Idempotency Constraints
CREATE UNIQUE INDEX IF NOT EXISTS idx_scrape_jobs_telegram_update
    ON public.scrape_jobs (telegram_update_id)
    WHERE telegram_update_id IS NOT NULL AND telegram_update_id != '';

CREATE INDEX IF NOT EXISTS idx_scrape_jobs_scraper_id ON public.scrape_jobs (scraper_job_id);
CREATE INDEX IF NOT EXISTS idx_scrape_jobs_telegram_user ON public.scrape_jobs (telegram_user_id);
CREATE INDEX IF NOT EXISTS idx_scrape_jobs_telegram_chat ON public.scrape_jobs (telegram_chat_id);
CREATE INDEX IF NOT EXISTS idx_scrape_jobs_status_created ON public.scrape_jobs (status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_scrape_jobs_created_at ON public.scrape_jobs (created_at DESC);

-- 4. Auto-update updated_at Trigger for public.scrape_jobs
CREATE OR REPLACE FUNCTION public.update_scrape_jobs_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_scrape_jobs_updated_at ON public.scrape_jobs;
CREATE TRIGGER trigger_scrape_jobs_updated_at
    BEFORE UPDATE ON public.scrape_jobs
    FOR EACH ROW
    EXECUTE FUNCTION public.update_scrape_jobs_updated_at();

-- 5. Row Level Security (RLS) & Least-Privilege Policies for public.scrape_jobs
ALTER TABLE public.scrape_jobs ENABLE ROW LEVEL SECURITY;

-- Revoke all permissions from public/untrusted roles (anon, authenticated)
REVOKE ALL ON TABLE public.scrape_jobs FROM anon, authenticated;

-- Grant access strictly to backend service_role
GRANT ALL ON TABLE public.scrape_jobs TO service_role;

DROP POLICY IF EXISTS "Service role full access on scrape_jobs" ON public.scrape_jobs;
CREATE POLICY "Service role full access on scrape_jobs"
    ON public.scrape_jobs
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- Ensure service_role has sequence usage for any schema sequences
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO service_role;

-- ==============================================================================
-- End of Migration 003
-- ==============================================================================
