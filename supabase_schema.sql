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

-- 4. Enable Row Level Security (RLS)
ALTER TABLE public.leads ENABLE ROW LEVEL SECURITY;

-- Allow anon and authenticated users full read & write access for local scraper integration
-- (You can restrict this later based on your auth requirements)
CREATE POLICY "Allow anon read and insert on leads"
    ON public.leads
    FOR ALL
    TO anon, authenticated, service_role
    USING (true)
    WITH CHECK (true);

-- Done! Table public.leads is ready for LeadMap Pro sync.
