-- ==============================================================================
-- LeadMap Pro — Supabase Schema for Google Maps Lead Intelligence
-- ==============================================================================
-- Run this script in your Supabase Dashboard:
-- SQL Editor -> New query -> Paste and click "Run"
-- ==============================================================================

-- 1. Create the 'leads' table
CREATE TABLE IF NOT EXISTS public.leads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    job_id TEXT,
    title TEXT NOT NULL,
    category TEXT,
    clean_phone TEXT,
    phone TEXT,
    emails TEXT,
    website TEXT,
    domain TEXT,
    address TEXT,
    review_rating NUMERIC(3, 2),
    review_count INTEGER DEFAULT 0,
    latitude TEXT,
    longitude TEXT,
    lead_score INTEGER DEFAULT 0,
    lead_tier TEXT DEFAULT 'COLD',
    score_reasons TEXT[] DEFAULT '{}',
    instagram TEXT,
    facebook TEXT,
    linkedin TEXT,
    raw_data JSONB
);

-- 2. Create unique index to allow deduplication / upsert by (clean_phone, domain) or (title, address)
CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_phone_domain
    ON public.leads (clean_phone, domain)
    WHERE clean_phone IS NOT NULL AND clean_phone != '' AND domain IS NOT NULL AND domain != '';

CREATE INDEX IF NOT EXISTS idx_leads_tier_score ON public.leads (lead_tier, lead_score DESC);
CREATE INDEX IF NOT EXISTS idx_leads_created_at ON public.leads (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_leads_domain ON public.leads (domain);

-- 3. Auto-update updated_at timestamp
CREATE OR REPLACE FUNCTION public.update_leads_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_leads_updated_at ON public.leads;
CREATE TRIGGER trigger_leads_updated_at
    BEFORE UPDATE ON public.leads
    FOR EACH ROW
    EXECUTE FUNCTION public.update_leads_updated_at();

-- 4. Enable Row Level Security (RLS) on leads
ALTER TABLE public.leads ENABLE ROW LEVEL SECURITY;

-- Allow anon and authenticated users full read & write access for local scraper integration
CREATE POLICY "Allow anon read and insert on leads"
    ON public.leads
    FOR ALL
    TO anon, authenticated, service_role
    USING (true)
    WITH CHECK (true);

-- ==============================================================================
-- 5. Create 'scrape_history' table for tracking past searches and scrape runs
-- ==============================================================================
CREATE TABLE IF NOT EXISTS public.scrape_history (
    id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT now(),
    started_at BIGINT,
    completed_at BIGINT,
    keyword TEXT,
    city TEXT,
    depth INTEGER DEFAULT 5,
    status TEXT DEFAULT 'ok',
    stage TEXT,
    leads_count INTEGER DEFAULT 0,
    hot_count INTEGER DEFAULT 0,
    warm_count INTEGER DEFAULT 0,
    cold_count INTEGER DEFAULT 0,
    email_count INTEGER DEFAULT 0,
    phone_count INTEGER DEFAULT 0,
    skipped_previous_count INTEGER DEFAULT 0,
    metrics JSONB DEFAULT '{}'::jsonb,
    params JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_history_created_at ON public.scrape_history (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_history_keyword ON public.scrape_history (keyword);

ALTER TABLE public.scrape_history ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow anon read and write on scrape_history"
    ON public.scrape_history
    FOR ALL
    TO anon, authenticated, service_role
    USING (true)
    WITH CHECK (true);

-- Done! Tables public.leads and public.scrape_history are ready for LeadMap Pro sync.
