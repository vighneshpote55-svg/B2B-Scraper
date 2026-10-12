-- ==============================================================================
-- Migration 003: Additive Telegram Scrape Jobs & Lead Attribution
-- ==============================================================================
-- Purely additive schema updates for Telegram bot integration and job tracking.
-- Preserves all existing columns, constraints, and data in public.leads,
-- public.scrape_history, and public.users.
-- ==============================================================================

-- 1. Additive fields on public.leads
ALTER TABLE public.leads 
    ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'web',
    ADD COLUMN IF NOT EXISTS place_id TEXT,
    ADD COLUMN IF NOT EXISTS cid TEXT,
    ADD COLUMN IF NOT EXISTS analysis JSONB DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS telegram_message_id TEXT;

-- Indexes for efficient lookups on additive columns
CREATE INDEX IF NOT EXISTS idx_leads_place_id ON public.leads (place_id);
CREATE INDEX IF NOT EXISTS idx_leads_source ON public.leads (source);

-- 2. Create public.scrape_jobs table for detailed multi-client job tracking
CREATE TABLE IF NOT EXISTS public.scrape_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scraper_job_id TEXT,
    telegram_user_id TEXT,
    telegram_chat_id TEXT,
    telegram_update_id TEXT,
    query TEXT NOT NULL,
    location TEXT,
    requested_limit INTEGER DEFAULT 5,
    status TEXT NOT NULL DEFAULT 'pending' 
        CHECK (status IN ('pending', 'running', 'ok', 'failed', 'cancelled')),
    stage TEXT DEFAULT 'Queued',
    progress INTEGER DEFAULT 0 CHECK (progress >= 0 AND progress <= 100),
    lead_counts JSONB DEFAULT '{"total": 0, "hot": 0, "warm": 0, "cold": 0, "with_email": 0, "with_phone": 0}'::jsonb,
    total_leads INTEGER DEFAULT 0,
    error_summary TEXT,
    source TEXT DEFAULT 'telegram',
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Idempotency index for Telegram update processing
CREATE UNIQUE INDEX IF NOT EXISTS idx_scrape_jobs_telegram_update
    ON public.scrape_jobs (telegram_update_id)
    WHERE telegram_update_id IS NOT NULL;

-- Performance & lookup indexes
CREATE INDEX IF NOT EXISTS idx_scrape_jobs_scraper_id ON public.scrape_jobs (scraper_job_id);
CREATE INDEX IF NOT EXISTS idx_scrape_jobs_telegram_user ON public.scrape_jobs (telegram_user_id);
CREATE INDEX IF NOT EXISTS idx_scrape_jobs_telegram_chat ON public.scrape_jobs (telegram_chat_id);
CREATE INDEX IF NOT EXISTS idx_scrape_jobs_status_created ON public.scrape_jobs (status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_scrape_jobs_created_at ON public.scrape_jobs (created_at DESC);

-- Trigger for updated_at
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

-- 3. Row Level Security & Least-Privilege Policies
ALTER TABLE public.scrape_jobs ENABLE ROW LEVEL SECURITY;

-- Revoke untrusted public access, grant full access exclusively to backend service_role
REVOKE ALL ON TABLE public.scrape_jobs FROM anon, authenticated;
GRANT ALL ON TABLE public.scrape_jobs TO service_role;

DROP POLICY IF EXISTS "Service role full access on scrape_jobs" ON public.scrape_jobs;
CREATE POLICY "Service role full access on scrape_jobs"
    ON public.scrape_jobs
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);
