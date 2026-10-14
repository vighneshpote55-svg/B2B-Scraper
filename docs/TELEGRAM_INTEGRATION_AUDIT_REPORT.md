# LeadMap Pro — Telegram Lead Collection & WhatsApp Portal Integration
## Phase 0: Architecture, Compatibility & Security Audit Report

**Audit Date:** October 14, 2026  
**Auditor:** Senior Software Architect, Security Engineer & QA Lead  
**Repository:** `/home/vighnesh/Documents/B2B`  
**Git Branch:** `feature/telegram-integration-phases-1-3`  
**Git Commit:** `23df11e` (HEAD)  
**Status:** Audit Complete — Awaiting Approval Before Implementation  

---

## 1. Executive Summary & Current Implementation Findings

An exhaustive audit of the LeadMap Pro repository was performed to evaluate the requirements for extending natural-language Telegram lead collection, database persistence, and integration with the administrator's existing WhatsApp sending portal.

### Current Baseline State
* **Core Web & API Stack:** Pure Python standard library HTTP daemon (`dashboard.py`), serving authenticated session-based endpoints, managing job admissions, and executing multi-step lead processing.
* **Scraper Engine:** Dockerized Go/Puppeteer scraper listening on `http://127.0.0.1:8085` managed via `scripts/scraper_engine.py`. Geocoding is powered by OpenStreetMap Nominatim.
* **Lead Cleansing & Deduplication:** `scripts/cleanser.py` handles regex-based phone parsing (international E.164 and local), email syntax and junk filtering, domain extraction, multi-attribute deduplication, and 0–100 lead scoring (HOT 🔥, WARM ⚡, COLD ❄️).
* **Database & Cloud Sync:** Supabase PostgreSQL PostgREST client (`scripts/supabase_client.py`). Tables `public.leads`, `public.scrape_history`, and `public.scrape_jobs` are protected by strict Row Level Security (RLS) granting access exclusively to `service_role`. Idempotent upserts use deterministic UUIDv5 primary keys (`phone|domain` or `title|address`).
* **Existing Telegram Bot:** `scripts/telegram_bot.py` (916 lines) provides a zero-dependency Telegram long-polling bot supporting strict numeric sender ID allowlisting (`TELEGRAM_ALLOWED_USER_IDS`), private-chat enforcement, update deduplication (`telegram_processed_updates.json`), and slash commands (`/start`, `/help`, `/scrape`, `/status`, `/recent`, `/cancel`, `/leads`, `/export`). 54 unit tests pass in `tests/test_telegram_bot_phase3.py`.
* **Testing & Release Baseline:** 120/120 automated tests pass across 11 unified suites (`./scripts/run_all_tests.sh`).

---

## 2. Existing-to-Proposed Architecture

### Current Architecture
```mermaid
graph TD
    AdminBrowser["Web Browser (Admin)"] -->|Cookie Session| Dashboard["dashboard.py (Port 3000)"]
    TelegramUser["Authorized Telegram User"] -->|Slash Command /scrape| TelegramBot["scripts/telegram_bot.py"]
    
    TelegramBot -->|initiate_scrape_job()| ScraperEngine["scripts/scraper_engine.py"]
    Dashboard -->|POST /api/scrape/start| ScraperEngine
    
    ScraperEngine -->|HTTP POST /api/v1/jobs| GoScraper["Go/Puppeteer Scraper (Port 8085)"]
    GoScraper -->|Scrapes| GMap["Google Maps Public Listings"]
    
    ScraperEngine -->|Clean & Deduplicate| Cleanser["scripts/cleanser.py"]
    ScraperEngine -->|JSON Save| LocalDisk["data/leads_{job_id}.json"]
    ScraperEngine -->|Idempotent Upsert (service_role)| Supabase["Supabase PostgreSQL (leads, scrape_jobs)"]
```

### Proposed Architecture (Natural Language & WhatsApp Portal Bridge)
```mermaid
graph TD
    subgraph Telegram_Interface [Telegram Interface]
        AdminTelegram["Authorized Admin (@username / Numeric ID)"] -->|Natural Language: '10 IT companies in Pune'| TelegramBot["scripts/telegram_bot.py"]
        TelegramBot --> NLPParser["NLP Intent & Entity Parser (Zero-dep)"]
        NLPParser -->|Missing Parameters| ClarificationFlow["Interactive Telegram Clarification"]
        ClarificationFlow -->|Ask follow-up| AdminTelegram
    end

    subgraph Core_Backend [LeadMap Pro Backend]
        NLPParser -->|Validated Payload| BackendBridge["Job Submission Bridge"]
        BackendBridge -->|Check Admission Lock| AdmissionLock["InterProcessAdmissionLock"]
        AdmissionLock --> ScraperEngine["scripts/scraper_engine.py"]
        ScraperEngine --> GoScraper["Go/Puppeteer Container (8085)"]
        GoScraper --> Cleanser["scripts/cleanser.py"]
        Cleanser --> LocalDisk["data/leads_{job_id}.json"]
        Cleanser --> SupabaseSync["scripts/supabase_client.py"]
    end

    subgraph Persistence_Layer [Data & Job Tracking]
        SupabaseSync -->|Idempotent Upsert (on_conflict=id)| DBLeads[("public.leads (with whatsapp_ready flag)")]
        SupabaseSync -->|Job Progress & Real Counts| DBJobs[("public.scrape_jobs")]
        LocalDisk --> DashboardUI["Existing Dashboard (/api/job/{id}/leads)"]
    end

    subgraph WhatsApp_Portal_Bridge [Existing WhatsApp Sending Portal]
        DBLeads -.->|Option A: Direct Supabase / PostgreSQL Read| WAPortal["Existing WhatsApp Portal"]
        DashboardUI -.->|Option B: Authenticated REST API Export| WAPortal
        LocalDisk -.->|Option C: WhatsApp-Formatted CSV Export| WAPortal
    end

    TelegramBot -->|Verified Metrics & Confirmation| AdminTelegram
```

---

## 3. Telegram Implementation Gaps

| Requirement | Current Status in `telegram_bot.py` | Gap / Defect Identified | Remediation Plan |
| :--- | :--- | :--- | :--- |
| **Natural Language Queries** | Only slash commands (`/scrape`, `/status`) are accepted (`telegram_bot.py:516-538`). | Messages like `"10 IT companies in Pune"` or `"Find 50 dentists in Mumbai"` fail with `Unknown command: '10'`. | Implement zero-dependency regex and rule-based entity extractor (`parse_natural_language_query`) extracting count, category, city, and modifiers. |
| **Conversational Clarification** | Commands are strictly one-shot. Missing fields return static error text. | If an admin sends `"find software companies"` (no city) or `"30 in Bangalore"` (no keyword), the bot aborts instead of asking a natural follow-up. | Add multi-turn state management in `telegram_state.json` tracking `awaiting_clarification` per chat with a 5-minute expiry timeout. |
| **Dashboard Visibility of Telegram Jobs** | Telegram jobs write to `data/leads_{job_id}.json` without a web `user_id` attribute. | In `dashboard.py:1588-1591`, `/api/job/{job_id}/leads` checks `job_owner == user.get("id")`. If `user_id` is missing or unassociated, web users cannot inspect Telegram-created jobs unless logged in as admin or ownership is mapped. | Attribute Telegram jobs to a designated Administrator account ID or shared admin role, ensuring they seamlessly render in the web dashboard. |
| **Real Counts Reporting** | Current bot reports total and synced count, but does not explicitly contrast new inserts vs duplicates skipped. | The user workflow mandates reporting: *actual results found, unique records, new database inserts, duplicates skipped, errors, job ID, and persistence status*. | Upgrade completion callback to unpack `metrics["raw_extracted"]`, `metrics["total"]` (unique), `metrics["newly_inserted"]`, `metrics["skipped_previous"]`, and Supabase confirmation status. |
| **Job Admission & Lock Safety** | Scraper engine submits directly to Go container without checking `InterProcessAdmissionLock`. | If web dashboard and Telegram bot submit simultaneously, total concurrent jobs can breach `MAX_CONCURRENT_JOBS`. | Wrap Telegram job initiation under `InterProcessAdmissionLock`, returning friendly queue/retry notices when capacity is reached. |

---

## 4. Database Compatibility and Migration Plan

### Assessment of Existing Database
* `public.leads` already stores `phone`, `clean_phone`, `category`, `lead_tier`, `lead_score`, `domain`, and `source` (`migrations/003`).
* `public.scrape_jobs` already exists with `telegram_user_id`, `telegram_chat_id`, `telegram_update_id`, `status`, and `lead_counts`.

### Additive Migration Plan (Migration 004)
To ensure compatibility with WhatsApp outreach workflows without breaking existing Phase 1–4 tables:
```sql
-- Migration 004: Additive WhatsApp Integration Fields
ALTER TABLE public.leads 
    ADD COLUMN IF NOT EXISTS whatsapp_status TEXT DEFAULT 'pending' 
        CHECK (whatsapp_status IN ('pending', 'eligible', 'invalid_phone', 'sent', 'failed', 'opt_out')),
    ADD COLUMN IF NOT EXISTS whatsapp_phone_e164 TEXT,
    ADD COLUMN IF NOT EXISTS whatsapp_last_exported_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS consent_source TEXT DEFAULT 'public_directory';

CREATE INDEX IF NOT EXISTS idx_leads_whatsapp_status ON public.leads (whatsapp_status)
    WHERE clean_phone IS NOT NULL AND clean_phone != '';

CREATE INDEX IF NOT EXISTS idx_leads_wa_phone ON public.leads (whatsapp_phone_e164);
```
* **Backwards Compatibility:** 100% additive. All existing queries in `dashboard.py`, `supabase_client.py`, and `scraper_engine.py` continue functioning without alteration.
* **RLS Preservation:** Retains strict `service_role` security.

---

## 5. Verified WhatsApp Portal Integration Approach

### Status of the WhatsApp Portal Codebase
Per Phase 0 inspection rules:
* The WhatsApp sending portal codebase is **external to this repository** and not located within `/home/vighnesh/Documents/B2B`.
* Rather than guessing its internals, LeadMap Pro will provide a **three-tier standardized integration bridge**:

| Integration Method | How LeadMap Pro Supports It | Requirements for Operator |
| :--- | :--- | :--- |
| **Tier 1: Direct Database (Recommended if Portal uses PostgreSQL/Supabase)** | LeadMap Pro marks all cleansed leads having valid phone numbers with `whatsapp_status = 'eligible'` and formats phone numbers to standard E.164. The WhatsApp portal can query `public.leads WHERE whatsapp_status = 'eligible' AND whatsapp_last_exported_at IS NULL`. | Provide table name / read credentials used by the portal. |
| **Tier 2: Authenticated REST API Export** | Add `/api/whatsapp/export` and `/api/whatsapp/leads` to `dashboard.py`. Returns JSON/CSV containing only verified phone numbers, business name, category, rating, personalized outreach pitch, and unique lead UUID. | WhatsApp portal calls this API using a secret API bearer token (`WHATSAPP_PORTAL_API_KEY`). |
| **Tier 3: Formatted File Import (CSV / Excel)** | The Telegram bot and Web Dashboard both offer 1-click downloads of a dedicated **WhatsApp Portal CSV** formatted specifically with `phone, name, custom_pitch, place_id, city, category`. | Operator uploads the exported CSV directly into the existing portal UI. |

> [!IMPORTANT]
> **Information Required from Operator Regarding WhatsApp Portal:**
> 1. Does the portal read directly from a PostgreSQL/Supabase database, or does it consume CSV file uploads or REST webhooks?
> 2. What phone number format does the portal expect (e.g. `+919876543210` E.164, `9876543210` 10-digit, or with spaces)? *(LeadMap Pro cleanses both local digits and E.164 formats).*
> 3. Does the portal require custom template variable columns (e.g. `{{business_name}}`, `{{category}}`, `{{rating}}`)?

---

## 6. Proposed File-by-File Changes

| Component / File | Nature | Scope of Changes |
| :--- | :---: | :--- |
| `scripts/telegram_bot.py` | **MODIFY** | 1. Add `parse_natural_language_query(text)` matching patterns (`10 IT companies in Pune`, `Find 50 dentists in Mumbai`).<br>2. Add interactive multi-turn clarification state machine in `_load_state()` / `_save_state()`.<br>3. Bind jobs to `InterProcessAdmissionLock` to prevent concurrent job overrun.<br>4. Enhance completion message to report exact breakdown: extracted, unique, newly inserted, duplicates skipped, job ID, and persistence status.<br>5. Add `/whatsapp` export command delivering WhatsApp-ready CSVs directly in chat. |
| `scripts/scraper_engine.py` | **MODIFY** | 1. Compute and return exact `newly_inserted` count from Supabase response.<br>2. Generate E.164 normalized phone numbers for WhatsApp readiness (`clean_phone_e164`).<br>3. Populate `whatsapp_status` ('eligible' if phone present, 'invalid_phone' otherwise). |
| `dashboard.py` | **MODIFY** | 1. Expose Telegram jobs in recent job listings for authenticated administrators.<br>2. Add optional authenticated endpoint `GET /api/whatsapp/export?job_id=...` protected by API key or session. |
| `scripts/cleanser.py` | **MODIFY** | Add `format_e164_phone(phone, country_hint='IN')` ensuring clean international format for WhatsApp outreach. |
| `migrations/004_whatsapp_integration_additive.sql` | **NEW** | Additive columns `whatsapp_status`, `whatsapp_phone_e164`, `whatsapp_last_exported_at`, and partial indexes. |
| `tests/test_telegram_nlp_and_whatsapp.py` | **NEW** | Comprehensive test suite covering NLP entity extraction, conversational follow-ups, real counts reporting, WhatsApp CSV formatting, and admission locking. |

---

## 7. Dependency and Configuration Changes

### Dependencies
* **Zero new pip dependencies:** Natural language entity parsing, regex heuristics, and conversational state will be built entirely using the Python standard library (`re`, `json`, `urllib`). No spaCy, NLTK, or external LLM API dependencies required.
* **No n8n:** Fully adheres to the strict requirement prohibiting n8n.

### Environment Configuration Updates (`.env.example`)
```bash
# Telegram Bot Configuration
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_ALLOWED_USER_IDS=123456789,987654321

# Default Country Code for Phone Normalization (Default: 91 for India)
DEFAULT_PHONE_COUNTRY_CODE=91

# WhatsApp Portal Integration (Optional REST key)
WHATSAPP_PORTAL_API_KEY=generate_secure_random_key_here
```

---

## 8. Security, Privacy & Operational Risks

| Risk Area | Severity | Mitigations Implemented |
| :--- | :---: | :--- |
| **Unauthorized Telegram Access** | **High** | Numeric sender ID whitelist (`is_user_authorized`) and private-chat-only enforcement. Rejects groups, supergroups, and unauthorized users immediately. |
| **Spam / Automated WhatsApp Violations** | **High** | **LeadMap Pro will NEVER trigger automated WhatsApp messaging upon scrape completion.** Contacts are merely flagged as eligible for manual review or explicit portal ingestion. Preserves anti-spam laws (TRAI / TCPA / GDPR). |
| **Prompt Injection / NLP Manipulation** | **Medium** | Input string length caps (keyword <= 200 chars, city <= 100 chars), strict regex extraction, and bounds checking on target count (1 <= count <= 50). |
| **Concurrency & Resource Exhaustion** | **Medium** | Telegram job dispatch honors `InterProcessAdmissionLock` and `MAX_CONCURRENT_JOBS` (default: 2-4), preventing Docker Puppeteer OOM crashes. |
| **Data Integrity & Count Hallucinations** | **Low** | The bot never fabricates counts; all reported metrics (`newly_inserted`, `skipped_previous`, `synced`) are derived directly from the deduplication engine and Supabase API response receipts. |

---

## 9. Test Matrix & Acceptance Criteria

| Test Suite / Area | Test Cases | Acceptance Criteria |
| :--- | :--- | :--- |
| **NLP Entity Extraction** | • `"10 IT companies in Pune"`<br>• `"Find 50 dentists in Mumbai"`<br>• `"20 digital marketing agencies in Bangalore"`<br>• `"cafes in Panjim 15"`<br>• Quoted strings and edge cases | Correctly extracts target count, category, and city. Bounds count between 1 and 50. Defaults to 5 if omitted. |
| **Conversational Clarification** | • `"find dentists"` (missing city)<br>• `"50 in Pune"` (missing category)<br>• Invalid follow-up reply<br>• State timeout after 5 minutes | Bot asks concise follow-up question. Second user message completes the query and initiates scrape. |
| **Deduplication & Real Counts** | Scrape batch containing 5 leads: 3 brand new, 2 duplicates previously saved in database | Bot completion message reports: `Extracted: 5, Unique: 3, New DB Inserts: 3, Duplicates Skipped: 2`. |
| **WhatsApp Readiness** | Phone cleansing across formats: `+91 98765 43210`, `020-25531234`, `9876543210` | Valid mobile numbers tagged `whatsapp_status = 'eligible'` and normalized to E.164 (`+919876543210`). Landlines flagged appropriately. |
| **Regression Safety** | Full `./scripts/run_all_tests.sh` execution | All 120 baseline tests pass with 0 failures, 0 errors. |

---

## 10. Phased Implementation & Rollback Plan

### Phased Execution (Upon Approval)
* **Phase 1: NLP Parser & Interactive Clarification**
  * Implement `parse_natural_language_query` and conversation state machine in `scripts/telegram_bot.py`.
  * Add unit tests verifying all query formats.
* **Phase 2: Scraper Engine Metrics & WhatsApp Cleansing Bridge**
  * Update `scripts/cleanser.py` with E.164 normalization.
  * Update `scripts/scraper_engine.py` to record exact database insertion deltas and WhatsApp eligibility flags.
  * Create `migrations/004_whatsapp_integration_additive.sql`.
* **Phase 3: Telegram Bot Workflow & Completion Reporting**
  * Connect natural language submission to `InterProcessAdmissionLock` and backend worker.
  * Wire detailed receipt reporting (results found, unique, new inserts, skipped duplicates, job ID, Supabase status).
  * Add `/whatsapp` export format in bot.
* **Phase 4: Dashboard Alignment & Full Regression Validation**
  * Ensure Telegram-initiated jobs render in web dashboard.
  * Run unified test runner (all 120 Phase 1–4 tests + new Telegram NLP suite).

### Rollback Plan
* All code changes are modular and backwards-compatible.
* If any regression occurs, the natural language handler falls back to explicit slash commands without affecting the core web dashboard or scraper container.
* Database changes in Migration 004 are purely additive (nullable columns with defaults) and can be rolled back with simple `ALTER TABLE public.leads DROP COLUMN IF EXISTS whatsapp_status;`.
