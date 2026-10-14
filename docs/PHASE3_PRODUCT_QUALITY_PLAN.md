# LeadMap Pro — Phase 3: Product Quality and Usability Plan

## 1. Context and Baseline Posture

Phase 1B security remediation and Phase 2 reliability hardening have both achieved verified status:
* **Phase 1B:** Closed all CSV formula injection avenues, locked down SSRF with socket pinning and strict IP classification, implemented re-entrant session concurrency locking, and restricted Supabase tables exclusively to `service_role` (75/75 automated tests passing).
* **Phase 2:** Introduced thread-safe bounded concurrency (`MAX_CONCURRENT_JOBS = 2`, HTTP 429), atomic durable state records (`_atomic_json_write`), idempotent crash recovery on server startup (`recover_interrupted_jobs`), safe subprocess termination with graceful `SIGTERM` and bounded `SIGKILL` escalation, and retention pruning while strictly safeguarding active jobs (89/89 automated tests passing).

This document establishes the architecture, requirements, and execution plan for **Phase 3: Product Quality and Usability**. The primary goal is to improve lead normalization, deduplication accuracy, error clarity, and frontend management efficiency without regressing the verified security or reliability foundation.

---

## 2. Working Tree & Environment State

* **Active Branch:** `feature/telegram-integration-phases-1-3`
* **Base Commit:** `95e65e76e10702cc04ac66c6402822b072b2edcf`
* **Local Working Tree:** Clean baseline plus Phase 2 reliability additions in `dashboard.py` and test harnesses.
* **Constraints Honored:**
  * Zero code or migration changes during this planning gate.
  * No external automation platforms (n8n) introduced.
  * No production credentials or live user data utilized.
  * Zero secret leakage in logs, plans, or documentation.

---

## 3. Workstream Deep Dives

### Workstream 1: Lead Quality Engine Hardening
*Normalization, Deduplication, Data Validation & Stale-Record Detection*

#### A. Current Implementation & Repository Evidence
* Lead cleansing and scoring are implemented in [scripts/cleanser.py](file:///home/vighnesh/Documents/B2B/scripts/cleanser.py).
* `clean_emails()` filters out common asset/spam emails and sorts domain-matching addresses first.
* `clean_phone()` applies regex-based country code rules for NANP, India, UK, Australia, Germany, France, UAE, and Singapore.
* `score_lead()` calculates a 0–100 score based on email (+35), phone (+25), website (+20), rating (+10/+5), reviews (+5), and socials (+5).
* Historical deduplication in `load_historical_leads()` (line 778) reads all `leads_*.json` files across the entire `data/` directory without inspecting `user_id`.

#### B. Deficiencies & Gaps Identified
1. **Cross-User History Leakage:** Because `load_historical_leads()` is global, User A's past scrapes inadvertently suppress leads for User B if they search the same city/niche.
2. **Missing Staleness Window:** Leads scraped months or years ago are permanently suppressed. If a business updates its phone, website, or email, the scraper permanently skips it instead of refreshing stale information.
3. **Punctuation & Unit Variations:** Address normalization handles basic roadway abbreviations (St, Ave, Blvd) but struggles with non-standard suite, unit, or floor combinations.
4. **Placeholder Values:** Scraper results occasionally contain placeholder titles ("Unknown", "N/A") or missing geographic coordinates that dilute lead quality scores.

#### C. Target Files
* `scripts/cleanser.py`
* `dashboard.py` (pass `user_id` to history loader)
* `tests/test_phase3_lead_quality.py` [NEW]

#### D. Requirements & Acceptance Criteria
* **User-Scoped History:** `load_historical_leads(data_dir, user_id=None, exclude_job_id="")` must filter files so only records owned by `user_id` are loaded for historical deduplication.
* **Configurable Freshness Threshold:** Introduce `LEAD_STALENESS_DAYS` (default: 90 days, configurable via environment variable). Leads with `completed_at` older than 90 days are flagged as stale and allowed to be refreshed by new scrapes.
* **Validation Filter:** Exclude dummy records lacking legitimate business titles or contact vectors.
* **International Phone Normalization:** Standardize international phone formatting with fallback preserving user-visible digits.

#### E. Risks & Dependencies
* Preserving backward compatibility with historical job files lacking `completed_at` timestamps (fallback to file modification time `mtime`).

#### F. Safe Testing Approach
* Isolated unit tests in `tests/test_phase3_lead_quality.py` using temporary directories and synthesized multi-user job files.

---

### Workstream 2: Scraping UX & Operational Resilience
*Progress Transparency, Understandable Errors, Safe Retries & Clear State Transitions*

#### A. Current Implementation & Repository Evidence
* [web/app.js](file:///home/vighnesh/Documents/B2B/web/app.js) manages `startScrape()` (line 730), `startPolling()` (line 788), and `liveProgressBox` (line 93).
* When `POST /api/scrape/start` returns HTTP 429, `app.js` triggers a generic error toast: `Error: Maximum concurrent jobs limit reached...`.
* When a job fails, the progress box displays "Job Failed" but provides no recovery or retry mechanism.
* Polling errors during network glitches log to console without retry or backoff.

#### B. Deficiencies & Gaps Identified
1. **No Concurrency Countdown:** The user is told the limit is reached but receives no indication of when a slot will open or an automated retry mechanism.
2. **Missing 1-Click Retry:** Users who experience a timeout, temporary throttling, or server reboot must manually re-enter their search keyword, city, scroll depth, and toggle settings.
3. **Ambiguous Failure Messages:** A job interrupted by server restart shows as "Failed" without clarifying that the failure was caused by a reboot and is safe to re-run.

#### C. Target Files
* `web/app.js`
* `web/index.html`
* `dashboard.py`
* `tests/test_phase3_scraping_ux.py` [NEW]

#### D. Requirements & Acceptance Criteria
* **Concurrency Countdown Banner:** When receiving HTTP 429 with `Retry-After: 30`, display a visual countdown timer ("Server at capacity. Automatic retry in 28s...") with a "Cancel Wait" option.
* **One-Click Safe Retry:** Add a "Retry Extraction" button in the Live Progress Box and Recent Jobs overview card that re-submits the exact saved `params` of the failed/cancelled job under a new job ID.
* **Granular Progress Milestones:** Update progress stage labels to reflect the pipeline:
  * 0–10%: "Admitting & Resolving Location"
  * 10–70%: "Extracting Google Maps Listings"
  * 70–85%: "Parsing & Deduplicating Leads"
  * 85–95%: "Enriching Contact & Social Profiles"
  * 100%: "Completed & Ready"
* **Resilient Polling:** Implement exponential backoff (2.5s -> 5s -> 10s) on intermittent HTTP polling failures before marking the connection as disconnected.

#### E. Risks & Dependencies
* Retries must strictly execute under `JOB_ADMISSION_LOCK` to ensure they never bypass `MAX_CONCURRENT_JOBS`.

#### F. Safe Testing Approach
* Mocked frontend interaction tests and HTTP client tests asserting retry behavior, parameter preservation, and 429 retry backoff.

---

### Workstream 3: Lead Management UX, Search & Bulk Operations
*Search Responsiveness, Multi-Field Filtering, Bulk Actions & Export Consistency*

#### A. Current Implementation & Repository Evidence
* [web/app.js](file:///home/vighnesh/Documents/B2B/web/app.js) lines 2000–2150 implement client-side filtering, tier select, multi-field search, and sorting.
* Bulk operations exist for Supabase sync (`btnBulkSyncSupabase`) and CSV download (`btnBulkExportCsv`).
* Standalone client-side CSV export is handled by `downloadSelectedLeadsCsv()` (line 1802).
* Backend CSV export is handled by `GET /api/export?format=csv` in `dashboard.py`.

#### B. Deficiencies & Gaps Identified
1. **Search Input Thrashing:** Every keystroke in the search field triggers an immediate DOM rebuild and re-slice of the entire leads array without debouncing.
2. **Export Schema Drift:** Client-side CSV export exports 13 fields (`lead_tier`, `lead_score`, `title`, `phone`, `emails`, `website`, `category`, `address`, `review_rating`, `review_count`, `instagram`, `facebook`, `linkedin`), while the backend `/api/export?format=csv` exports an older schema without social handles.
3. **Lack of Bulk Delete / Archive:** Users cannot prune unwanted or unqualified leads directly in the web table before saving or syncing.
4. **State Loss on View Toggle:** Switching between Table View and Leaflet Map View can reset the active lead selection set.

#### C. Target Files
* `web/app.js`
* `web/index.html`
* `dashboard.py` (align `/api/export` fields with client-side schema)

#### D. Requirements & Acceptance Criteria
* **Debounced Search:** Implement a 200ms debounce on the search input to maintain 60 FPS UI performance on datasets with 1,000+ leads.
* **Bulk Management Actions:** Add "Select All", "Deselect All", and "Remove Selected" (in-memory prune) actions to the bulk actions toolbar.
* **Synchronized Export Schema:** Ensure `/api/export?format=csv` and client-side `downloadSelectedLeadsCsv()` output identical column headers, column order, and quote/formula escaping.
* **View State Persistence:** Preserve selected lead checkboxes, search query, and filter tab when toggling between Table and Leaflet Map views.

#### E. Risks & Dependencies
* Must preserve all Phase 1/1B formula injection defenses (prefixing `=`, `+`, `-`, `@`, `\t`, `\r`, `|`, `%` with `'`).

#### F. Safe Testing Approach
* Headless browser/DOM tests or JavaScript unit tests asserting debounced query execution and CSV header parity.

---

### Workstream 4: Observability, Structured Logging & Diagnostics
*Structured Logs, Diagnostic Endpoints & Secret Redaction*

#### A. Current Implementation & Repository Evidence
* [dashboard.py](file:///home/vighnesh/Documents/B2B/dashboard.py) uses unstructured `print()` statements for notifications, Supabase sync results, and error handling.
* `/api/status` returns high-level scraper health (`scraper_up`, `scraper_base`, `dashboard_port`, `active_jobs_count`).
* Sensitive keys are stripped from durable records via regex, but raw stdout/stderr has no automated redaction layer.

#### B. Deficiencies & Gaps Identified
1. **No Log Levels or Timestamps:** Without standard levels (`INFO`, `WARNING`, `ERROR`), log monitoring tools cannot filter noise from actionable failures.
2. **No Correlation IDs:** Subprocess executions and background worker threads lack a unified correlation ID linking HTTP requests to specific jobs.
3. **Missing System Health Metrics:** No endpoint reports memory usage, queue capacity, disk space in `data/`, or server uptime.

#### C. Target Files
* `dashboard.py`
* `tests/test_phase3_observability.py` [NEW]

#### D. Requirements & Acceptance Criteria
* **Standard Python Logging:** Replace ad-hoc `print()` statements with standard `logging.getLogger("leadmap")`.
* **Automated Secret Redaction Filter:** Attach a custom `logging.Filter` that masks:
  * Bearer tokens (`Bearer [a-f0-9...redacted]`)
  * Passwords / password hashes
  * Supabase `service_role` and `anon` keys
  * Email outbox tokens
* **Protected Diagnostics Endpoint (`GET /api/diagnostics`):** Restricted to authenticated users, returning:
  * Server uptime and memory footprint (`rss_bytes`).
  * Job concurrency status: active vs `MAX_CONCURRENT_JOBS`.
  * Total jobs stored on disk and disk space consumed by `DATA_DIR`.
  * External service status (Docker scraper latency, Supabase connection latency).
* **Job Performance Metrics:** Store pipeline timing milestones (`scrape_duration_sec`, `cleanse_duration_sec`, `enrich_duration_sec`) in job metadata.

#### E. Risks & Dependencies
* Secret redaction filter must be lightweight to avoid adding latency to high-throughput log operations.

#### F. Safe Testing Approach
* Unit tests in `tests/test_phase3_observability.py` passing synthetic logs with secret keys through the logging handler and verifying zero unmasked secrets appear in output.

---

### Workstream 5: End-to-End User Journey & Regression Protection
*Critical User Journey Automation & Regression Baselines*

#### A. Current Implementation & Repository Evidence
* 89 automated tests currently pass across 5 separate unit and integration suites:
  * `test_phase2_reliability.py` (14 tests)
  * `test_phase1b_security.py` (12 tests)
  * `test_ssrf_and_config_phase5.py` (27 tests)
  * `test_security_phase1.py` (26 tests)
  * `test_security_phase2.py` (10 tests)
* No automated test currently simulates the complete end-to-end user lifecycle through all pipeline stages.

#### B. Target Files
* `tests/test_phase3_e2e_journey.py` [NEW]

#### C. Requirements & Acceptance Criteria
* **Complete User Journey Test:** Construct an offline, isolated test simulating:
  1. User signup and login token issuance.
  2. Submitting `POST /api/scrape/start` with mock scraper responses.
  3. Polling `GET /api/job/<id>/status` until completion.
  4. Fetching `GET /api/job/<id>/leads` and verifying lead normalization, scoring, and tiering.
  5. Attempting cross-user access with a secondary user token and verifying `HTTP 403 Forbidden`.
  6. Exporting leads via `GET /api/export?format=csv` and verifying RFC 4180 quotes, formula escaping, and row counts.
  7. Verifying `POST /api/scrape/stop` terminates active processes and sets `"status": "cancelled"`.
* **Regression Protection:** All existing 89 tests must remain 100% green.

#### D. Safe Testing Approach
* Runs completely in-memory and against temporary directories without external Docker, network, or database calls.

---

## 4. Prioritized Implementation Sequence

We recommend executing Phase 3 in 4 focused, independently testable phases:

```
┌─────────────────────────────────────────────────────────────┐
│ Phase 3A: Lead Quality & Observability Core                 │
│ - User-scoped historical deduplication                      │
│ - Configurable stale-record freshness window (90 days)      │
│ - Leveled Python logging with automated secret redaction    │
│ - Protected /api/diagnostics endpoint                       │
└──────────────────────────────┬──────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ Phase 3B: Scraping UX & Error Resilience                    │
│ - HTTP 429 concurrency retry countdown banner               │
│ - One-click "Retry Extraction" with preserved params        │
│ - Resilient polling with exponential backoff                │
│ - Clear failure reason badges (reboot vs timeout vs rate)   │
└──────────────────────────────┬──────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ Phase 3C: Lead Management UX & Export Alignment             │
│ - Debounced search input (200ms)                            │
│ - Bulk selection toolbar & bulk remove actions              │
│ - Synchronized CSV export schema (client & backend parity)  │
│ - View state persistence between Table & Map modes          │
└──────────────────────────────┬──────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ Phase 3D: End-to-End Testing & Verification Gate            │
│ - Automated end-to-end user journey test suite              │
│ - Full 89-test regression suite re-verification             │
│ - docs/PHASE3_PRODUCT_QUALITY_REPORT.md deliverable         │
└─────────────────────────────────────────────────────────────┘
```

---

## 5. Security & Production-Readiness Prerequisites Remaining

Before declaring the entire LeadMap Pro application production-ready for untrusted multi-tenant public networks:
1. **TLS / HTTPS Reverse Proxy:** The application currently binds to loopback (`127.0.0.1:3000`) over plaintext HTTP. An upstream reverse proxy (Caddy, NGINX) with automated TLS termination and secure cookie handling is required.
2. **Distributed Process Coordination:** For scaling across multiple WSGI worker processes (e.g. Gunicorn), `JOB_ADMISSION_LOCK` and process termination should utilize POSIX file locks or a shared Redis broker.
3. **Database Migration Verification:** Supabase tables and RLS policies are strictly server-side; live database catalog inspection should confirm effective permissions upon cloud deployment.
