-- ==============================================================================
-- Migration 004: Additive WhatsApp Integration Fields & Eligibility Indexing
-- ==============================================================================
-- Purely additive schema updates for WhatsApp sending portal integration.
-- Preserves all existing columns, constraints, and data in public.leads,
-- public.scrape_history, and public.scrape_jobs.
-- ==============================================================================

-- 1. Additive columns on public.leads for WhatsApp outreach eligibility
ALTER TABLE public.leads 
    ADD COLUMN IF NOT EXISTS whatsapp_status TEXT DEFAULT 'pending' 
        CHECK (whatsapp_status IN ('pending', 'eligible', 'invalid_phone', 'sent', 'failed', 'opt_out')),
    ADD COLUMN IF NOT EXISTS whatsapp_phone_e164 TEXT,
    ADD COLUMN IF NOT EXISTS whatsapp_last_exported_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS consent_source TEXT DEFAULT 'public_directory';

-- 2. Performance indexes for efficient WhatsApp portal queries
CREATE INDEX IF NOT EXISTS idx_leads_whatsapp_status ON public.leads (whatsapp_status)
    WHERE clean_phone IS NOT NULL AND clean_phone != '';

CREATE INDEX IF NOT EXISTS idx_leads_whatsapp_e164 ON public.leads (whatsapp_phone_e164)
    WHERE whatsapp_phone_e164 IS NOT NULL AND whatsapp_phone_e164 != '';

CREATE INDEX IF NOT EXISTS idx_leads_wa_unexported ON public.leads (whatsapp_status, whatsapp_last_exported_at)
    WHERE whatsapp_status = 'eligible';

-- 3. Row Level Security policy confirmation
-- Retains strict service_role access policy without granting public/anon access
ALTER TABLE public.leads ENABLE ROW LEVEL SECURITY;
GRANT ALL ON TABLE public.leads TO service_role;
