# LeadMap Pro — Phase 4 Production Readiness and End-to-End Validation Plan

**Document Version:** 1.0.0  
**Status:** PROPOSED (Awaiting User Review & Approval)  
**Author:** Senior Software Architect, Application Security Engineer & QA Lead  
**Target Repository:** `/home/vighnesh/Documents/B2B`  
**Baseline Git Commit:** `a83d5be80abcb2b9b167319b2b48accb789c0237`  
**Date:** October 14, 2026  

---

## 1. Executive Summary & Baseline Audit

### 1.1 Baseline Verification
Before defining Phase 4, the current working tree and verification records were audited:
* **Branch & Commit State:** Working tree clean on branch `feature/telegram-integration-phases-1-3` at commit `a83d5be80abcb2b9b167319b2b48accb789c0237`.
* **Automated Regression Suite:** 97 tests across 6 dedicated test suites, all verified passing in isolated test environments:
  - `tests/test_phase3_quality_and_ux.py` (8/8 passing)
  - `tests/test_phase2_reliability.py` (14/14 passing)
  - `tests/test_phase1b_security.py` (12/12 passing)
  - `tests/test_ssrf_and_config_phase5.py` (27/27 passing)
  - `tests/test_security_phase1.py` (26/26 passing)
  - `tests/test_security_phase2.py` (10/10 passing)
* **Predecessor Reports:**
  - `docs/PHASE1B_FINAL_VERIFICATION.md`: Verified socket-pinned SSRF defenses, session concurrency locking (`SESSION_LOCK`), and CSV formula sanitization.
  - `docs/PHASE2_RELIABILITY_REPORT.md` & `docs/PHASE2_INDEPENDENT_VERIFICATION.md`: Verified bounded job admission (`MAX_CONCURRENT_JOBS = 2`), atomic JSON persistence, and startup crash recovery.
  - `docs/PHASE3_INDEPENDENT_VERIFICATION.md`: Verified user-scoped historical deduplication, 90-day stale lead refresh window, 1-click retry controls, leveled logging with secret redaction (`SecretRedactionFilter`), and `/api/diagnostics`.

### 1.2 Implemented Capabilities vs. Production Gaps
While Phases 1–3 established solid internal security, reliability, and lead quality, critical operational and end-to-end integration gaps remain between the local prototype and a resilient production deployment:

| Functional Area | Current Baseline (Phases 1–3) | Identified Production Gap (Phase 4 Scope) |
| :--- | :--- | :--- |
| **User Journey** | Individual endpoint unit/integration tests with mocked inputs. | No continuous end-to-end integration test validating a complete user journey (signup -> search -> scrape -> qualification -> deduplication -> export -> logout). |
| **Browser UX** | Static UI tests and client-side unit sanitization. | Polling uses fixed 2.5s intervals (`setInterval`) without backoff; no automated testing of UI state persistence across view switches or network drops. |
| **Transport Security** | `dashboard.py` serves plaintext HTTP on `127.0.0.1:3000`. | Public exposure requires upstream TLS termination (Caddy/Nginx) with HSTS, CSP, and secure cookie handling. |
| **Multi-Worker Safety** | In-process threading locks (`JOB_ADMISSION_LOCK`). | Concurrency limit will fail if deployed across multiple WSGI worker processes without POSIX file locks (`fcntl.flock`). |
| **Server Shutdown** | Startup crash recovery recovers crashed jobs. | Server lacks OS signal handling (`SIGTERM`/`SIGINT`) to gracefully terminate child scrapers and flush buffers before shutdown. |
| **Database Verification** | Migration SQL restricts access to `service_role`. | Live Supabase catalog permissions remain unverified against actual cloud deployment. |
| **Operational Runbooks** | Ad-hoc run scripts (`run_dashboard.sh`, `run_bot.sh`). | Lacks production systemd service templates, automated data backup/restore scripts, and single-command test harnesses. |

---

## 2. Workstreams & Implementation Plan

Phase 4 is structured into six focused workstreams designed to achieve production grade operational readiness without introducing unnecessary external infrastructure (such as Redis, Celery, or n8n).

```
┌────────────────────────────────────────────────────────┐
│ Workstream 1: End-to-End User Journey Automation       │
│ Full lifecycle integration test in an isolated harness  │
└───────────────────────────┬────────────────────────────┘
                            ▼
┌────────────────────────────────────────────────────────┐
│ Workstream 2: Browser-Level UX & Workflow Validation   │
│ Exponential backoff polling, retry controls & state    │
└───────────────────────────┬────────────────────────────┘
                            ▼
┌────────────────────────────────────────────────────────┐
│ Workstream 3: Security Readiness & Reverse Proxy Setup │
│ Upstream TLS (Caddy/Nginx), Supabase verification      │
└───────────────────────────┬────────────────────────────┘
                            ▼
┌────────────────────────────────────────────────────────┐
│ Workstream 4: Operational Resilience & Disaster Recovery│
│ SIGTERM shutdown, POSIX file lock, backup/restore      │
└───────────────────────────┬────────────────────────────┘
                            ▼
┌────────────────────────────────────────────────────────┐
│ Workstream 5: Observability & Operational Metrics      │
│ JSON logging, memory/disk metrics, safe diagnostics    │
└───────────────────────────┬────────────────────────────┘
                            ▼
┌────────────────────────────────────────────────────────┐
│ Workstream 6: Release Engineering & Deployment Runbook │
│ Systemd service, config validator, run_all_tests.sh    │
└────────────────────────────────────────────────────────┘
```

---

### Workstream 1: End-to-End User Journey Tests
* **Objective:** Validate the complete customer workflow programmatically in an isolated, deterministic environment without connecting to live external services.
* **Scope & Proposed Work:**
  1. Create a dedicated test suite (`tests/test_phase4_e2e_journey.py`) simulating an entire client lifecycle:
     - **Step 1: Auth:** Register new account (`POST /api/auth/signup`) and acquire session token.
     - **Step 2: Dispatch:** Submit query (`POST /api/scrape/start`) with mocked backend scraper subprocess.
     - **Step 3: Progress:** Poll `/api/job/<id>/status` across intermediate milestones (10% -> 15% -> 85% -> 96% -> 100%).
     - **Step 4: Enrichment & Qualification:** Fetch lead results (`GET /api/job/<id>/leads`), verifying computed scores, email/phone formatting, and qualification tags.
     - **Step 5: Multi-Tenant Scoping:** Confirm a second authenticated user cannot view or cancel this job (`HTTP 403 Forbidden`).
     - **Step 6: Deduplication:** Dispatch a second search containing overlapping places; verify existing records are preserved and marked as skipped.
     - **Step 7: Lead Management:** Execute in-memory bulk removal and verify curated CSV export (`GET /api/export?format=csv`).
     - **Step 8: CSV Security:** Validate exported CSV complies with RFC 4180 and prepends `'` to formula triggers (`=`, `+`, `-`, `@`, `\t`, `\r`).
     - **Step 9: Logout:** Terminate session (`POST /api/auth/logout`) and confirm subsequent requests fail with `HTTP 401 Unauthorized`.
* **Target Files:**
  - `tests/test_phase4_e2e_journey.py` [NEW]
* **Acceptance Criteria:**
  - Automated test runs synchronously in under 5.0 seconds.
  - Zero external network requests (fully mocked scraper / loopback).
  - Validates full journey end-to-end in a single test invocation.

---

### Workstream 2: Browser-Level UX & Workflow Validation
* **Objective:** Validate frontend interface resilience, error recovery, and data presentation consistency.
* **Scope & Proposed Work:**
  1. **Adaptive Polling with Exponential Backoff:**
     - Replace the fixed 2.5s `setInterval` in `web/app.js` with an adaptive `setTimeout` loop.
     - On successful response: poll every 2.5s while job is active.
     - On transient network or 5xx error: back off exponentially (2.5s -> 5s -> 10s) up to a max backoff of 30s.
     - Reset backoff interval immediately upon recovery or manual refresh.
  2. **Retry Controls Fidelity:**
     - Verify `#btn-retry-scrape` retains original search terms, query category, location, and max results limit.
     - Ensure retry button is visible on both job failure (`status == 'failed'`) and rate limit / capacity exhaustion (`HTTP 429`).
  3. **Table and Map View Consistency:**
     - Verify filter state (rating slider, category chips, search text) and lead row selections remain synchronized when switching between `#view-table` and `#view-map`.
     - Ensure Leaflet map markers reflect exactly the active, non-removed lead set.
  4. **Client-Side Bulk Removal:**
     - Ensure `#btn-bulk-remove` updates row count, total counter, and export payload without causing DOM exceptions or requesting backend deletions.
* **Target Files:**
  - `web/app.js` [MODIFY]
  - `tests/test_phase4_browser_ux.py` [NEW]
* **Acceptance Criteria:**
  - Polling gracefully handles simulated temporary network interruptions.
  - No JavaScript console errors during view toggles or bulk actions.
  - UI maintains responsiveness under 500+ rendered lead rows.

---

### Workstream 3: Security Readiness & Reverse Proxy Architecture
* **Objective:** Prepare the application for secure public network deployment and verify multi-tenant isolation boundaries.
* **Scope & Proposed Work:**
  1. **Upstream Reverse Proxy Templates:**
     - Provide production Caddy (`deploy/caddy/Caddyfile`) and Nginx (`deploy/nginx/leadmap.conf`) configurations.
     - Enforce automatic HTTPS/TLS 1.3 termination, HTTP/2 or HTTP/3 support.
     - Set secure HTTP headers:
       ```http
       Strict-Transport-Security: max-age=63072000; includeSubDomains; preload
       X-Content-Type-Options: nosniff
       X-Frame-Options: DENY
       Referrer-Policy: strict-origin-when-cross-origin
       Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline' https://unpkg.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://unpkg.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: https://*.tile.openstreetmap.org; connect-src 'self';
       ```
     - Proxy requests securely to backend bound to `127.0.0.1:3000`.
  2. **Session & Transport Protection:**
     - Support `X-Forwarded-Proto` and `X-Forwarded-For` awareness in `dashboard.py`.
     - Support setting session tokens in `HttpOnly; Secure; SameSite=Strict` cookies in addition to Bearer tokens.
  3. **Multi-User Tenant Isolation Audit:**
     - Verify all endpoints (`/api/job/<id>/status`, `/api/job/<id>/leads`, `/api/job/<id>/cancel`, `/api/export`) reject unauthorized access if `session['user_id'] != job['user_id']`.
  4. **Supabase Production Verification Script:**
     - Create a non-destructive verification tool (`scripts/verify_supabase_production.py`) that checks:
       * Valid connection to `SUPABASE_URL` using `SUPABASE_SERVICE_ROLE_KEY`.
       * Correct schema structure on `leads` table.
       * Direct public / anon key access is rejected (`401`/`403`).
       * Multi-tenant RLS policies prevent cross-tenant reading.
* **Target Files:**
  - `deploy/caddy/Caddyfile` [NEW]
  - `deploy/nginx/leadmap.conf` [NEW]
  - `scripts/verify_supabase_production.py` [NEW]
  - `dashboard.py` [MODIFY]
  - `tests/test_phase4_security_hardening.py` [NEW]
* **Acceptance Criteria:**
  - A+ SSL Labs configuration profile.
  - Zero cross-tenant data leakage under concurrent user requests.
  - Live Supabase verification script executes cleanly with zero secret leakage.

---

### Workstream 4: Operational Resilience & Disaster Recovery
* **Objective:** Guarantee clean process lifecycle handling, graceful shutdowns, and reproducible data backup/recovery.
* **Scope & Proposed Work:**
  1. **Graceful Server Shutdown:**
     - Add `signal.signal(signal.SIGTERM, _shutdown_handler)` and `signal.signal(signal.SIGINT, _shutdown_handler)` in `dashboard.py`.
     - When a signal is caught:
       * Set an atomic shutdown event preventing new job admissions.
       * Terminate all active child scraper processes using the verified `_terminate_job_processes(sig=signal.SIGTERM)`.
       * Flush pending memory buffers and atomic JSON files to disk.
       * Close the HTTP server cleanly without leaving orphan scraper processes.
  2. **Multi-Worker Concurrency File Locking:**
     - Replace Python's in-process `threading.Lock()` for job admissions with POSIX file locking (`fcntl.flock`) on a dedicated lock file (`data/.job_admission.lock`).
     - Ensures `MAX_CONCURRENT_JOBS` is strictly enforced even when running under multi-worker WSGI servers (Gunicorn/Uvicorn).
  3. **Data Backup and Restore Utility:**
     - Create `scripts/backup_data.py`:
       * Takes a consistent, read-locked snapshot of `data/` (jobs, deduplication history, lead JSONs).
       * Compresses archive to `tar.gz` with date-stamped filename.
       * Generates a SHA-256 checksum manifest (`.sha256`).
     - Create `scripts/restore_data.py`:
       * Verifies archive SHA-256 checksum prior to extraction.
       * Validates archive contents (protects against path traversal / zip-slip).
       * Restores data directory atomically.
  4. **Disk Capacity Safeguards:**
     - Check available storage in `data/` before launching a new job.
     - Reject new jobs with `HTTP 507 Insufficient Storage` if free disk space is below safety threshold (`MIN_FREE_DISK_MB = 250`).
* **Target Files:**
  - `dashboard.py` [MODIFY]
  - `scripts/backup_data.py` [NEW]
  - `scripts/restore_data.py` [NEW]
  - `tests/test_phase4_reliability_resilience.py` [NEW]
* **Acceptance Criteria:**
  - `SIGTERM` cleanly terminates scraper subprocesses within 5 seconds without orphans.
  - POSIX file locking prevents concurrency races across multiple worker processes.
  - Backup and restore utility passes round-trip integrity test with checksum validation.

---

### Workstream 5: Observability & Operational Metrics
* **Objective:** Provide actionable operational visibility, health diagnostics, and structured logs while preventing secret or PII exposure.
* **Scope & Proposed Work:**
  1. **Operational Metrics Endpoint:**
     - Enhance `/api/diagnostics` to include safe runtime telemetry:
       * System uptime and process start timestamp.
       * Memory usage (Resident Set Size via `/proc/self/statm` or standard library).
       * Disk usage: total size of `data/`, available disk space on filesystem.
       * Job queue counters: total active, completed, failed, and recovered jobs.
       * Docker scraper connectivity check (timeout bounded to 1.0s).
  2. **Structured JSON Logging:**
     - Implement optional JSON log formatting (`LOG_FORMAT=json` env var) for production log aggregators (Datadog, Loki, CloudWatch).
     - Ensure `SecretRedactionFilter` wraps the JSON formatter to prevent Bearer tokens, passwords, and API keys from leaking into stdout.
  3. **Actionable Error Responses:**
     - Ensure all HTTP 4xx/5xx responses return structured JSON:
       ```json
       {"success": false, "error": "Descriptive message", "code": "ERROR_CODE", "timestamp": "ISO-8601"}
       ```
* **Target Files:**
  - `dashboard.py` [MODIFY]
  - `tests/test_phase4_observability.py` [NEW]
* **Acceptance Criteria:**
  - `/api/diagnostics` returns in < 30ms with zero exposed secrets.
  - Structured logs pass JSON validation schema.
  - Regex secret redaction prevents API keys or passwords from appearing in log streams.

---

### Workstream 6: Release Engineering & Deployment Runbook
* **Objective:** Ensure deterministic configuration, reproducible verification commands, and documented release/rollback procedures.
* **Scope & Proposed Work:**
  1. **Startup Configuration Validator:**
     - Implement `validate_environment()` in `dashboard.py` executed at boot:
       * Validate `PORT` is a valid integer between 1024 and 65535.
       * Validate `MAX_CONCURRENT_JOBS >= 1`.
       * If Supabase is enabled (`SUPABASE_URL` present), verify `SUPABASE_SERVICE_ROLE_KEY` is non-empty and well-formed.
       * Verify `data/` directory is writable.
       * Print clear, actionable error messages and exit immediately on misconfiguration.
  2. **Single-Command Test Runner:**
     - Create `scripts/run_all_tests.sh`:
       * Sets isolated test environment variables (`LEADMAP_ENV=test`, `DATA_DIR=/tmp/test_...`).
       * Runs all test suites sequentially with verbose output.
       * Returns non-zero exit code on any failure.
       * Cleans up temporary test files upon exit.
  3. **Systemd Production Service Template:**
     - Create `deploy/systemd/leadmap.service`:
       * Unprivileged service running as dedicated user (`User=leadmap`).
       * System sandboxing (`ProtectSystem=strict`, `ProtectHome=true`, `NoNewPrivileges=true`).
       * Process supervision with automatic restart (`Restart=on-failure`, `RestartSec=5s`).
       * Proper shutdown timeout (`TimeoutStopSec=15s`).
  4. **Deployment & Rollback Documentation:**
     - Create `docs/PHASE4_DEPLOYMENT_AND_ROLLBACK.md` detailing:
       * Host system prerequisites (Docker, Python 3.10+, systemd, Caddy/Nginx).
       * Step-by-step production installation instructions.
       * Configuration reference matrix.
       * Rollback procedure (service stop, binary reversion, data snapshot restore).
* **Target Files:**
  - `deploy/systemd/leadmap.service` [NEW]
  - `deploy/docker-compose.prod.yml` [NEW]
  - `scripts/run_all_tests.sh` [NEW]
  - `docs/PHASE4_DEPLOYMENT_AND_ROLLBACK.md` [NEW]
* **Acceptance Criteria:**
  - Invalid configuration triggers clear error message without stack trace.
  - `run_all_tests.sh` runs all 100+ tests cleanly and exits with code 0.
  - Systemd service template passes `systemd-analyze verify`.

---

## 3. Implementation Sequence & Dependency Map

To maintain a green test suite and avoid regression, Phase 4 will be implemented in four sub-phases:

```
┌────────────────────────────────────────────────────────┐
│ Sub-Phase 4A: End-to-End User Journey & UX Resilience   │
│ 1. Implement tests/test_phase4_e2e_journey.py          │
│ 2. Upgrade web/app.js polling to exponential backoff    │
│ 3. Add tests/test_phase4_browser_ux.py                 │
└───────────────────────────┬────────────────────────────┘
                            ▼
┌────────────────────────────────────────────────────────┐
│ Sub-Phase 4B: Security Hardening & Transport Readiness  │
│ 1. Create deploy/caddy/Caddyfile & Nginx config        │
│ 2. Add scripts/verify_supabase_production.py           │
│ 3. Implement tests/test_phase4_security_hardening.py   │
└───────────────────────────┬────────────────────────────┘
                            ▼
┌────────────────────────────────────────────────────────┐
│ Sub-Phase 4C: Reliability & Operational Resilience     │
│ 1. Add SIGTERM / SIGINT graceful shutdown in dashboard  │
│ 2. Implement POSIX file-locking admission (fcntl)       │
│ 3. Create scripts/backup_data.py & restore_data.py     │
│ 4. Add tests/test_phase4_reliability_resilience.py     │
└───────────────────────────┬────────────────────────────┘
                            ▼
┌────────────────────────────────────────────────────────┐
│ Sub-Phase 4D: Observability, Release & Gating          │
│ 1. Add startup config validation & JSON logging        │
│ 2. Create deploy/systemd/leadmap.service unit          │
│ 3. Create scripts/run_all_tests.sh test harness        │
│ 4. Author docs/PHASE4_DEPLOYMENT_AND_ROLLBACK.md       │
│ 5. Perform final independent verification gate         │
└────────────────────────────────────────────────────────┘
```

---

## 4. Specific Target Files & Affected Components

| Component | Target File | Action | Purpose / Changes |
| :--- | :--- | :--- | :--- |
| **Backend** | `dashboard.py` | MODIFY | Add SIGTERM handler, POSIX file locking, environment validator, and JSON log formatting. |
| **Frontend** | `web/app.js` | MODIFY | Upgrade fixed 2.5s polling to adaptive exponential backoff; reinforce retry controls. |
| **Test Suite** | `tests/test_phase4_e2e_journey.py` | NEW | Full user journey automation (signup -> scrape -> qual -> dedup -> export -> logout). |
| **Test Suite** | `tests/test_phase4_browser_ux.py` | NEW | Client polling backoff, retry button, filter/selection synchronization tests. |
| **Test Suite** | `tests/test_phase4_security_hardening.py` | NEW | Multi-tenant scoping, header validation, token vs cookie verification. |
| **Test Suite** | `tests/test_phase4_reliability_resilience.py` | NEW | Graceful shutdown, process signal cleanup, POSIX file locking concurrency tests. |
| **Test Suite** | `tests/test_phase4_observability.py` | NEW | `/api/diagnostics` metrics, memory/disk checks, JSON logging secret redaction. |
| **Scripts** | `scripts/run_all_tests.sh` | NEW | Single-command deterministic test harness for all test suites. |
| **Scripts** | `scripts/backup_data.py` | NEW | Consistent data snapshot utility with SHA-256 manifest. |
| **Scripts** | `scripts/restore_data.py` | NEW | Validated, path-traversal-safe data restoration utility. |
| **Scripts** | `scripts/verify_supabase_production.py` | NEW | Standalone live Supabase credential and RLS policy verification tool. |
| **Deployment** | `deploy/caddy/Caddyfile` | NEW | Production TLS reverse proxy configuration template. |
| **Deployment** | `deploy/nginx/leadmap.conf` | NEW | Alternative Nginx TLS reverse proxy configuration template. |
| **Deployment** | `deploy/systemd/leadmap.service` | NEW | Sandboxed systemd service definition for process supervision. |
| **Deployment** | `deploy/docker-compose.prod.yml` | NEW | Hardened production Docker Compose configuration. |
| **Documentation**| `docs/PHASE4_DEPLOYMENT_AND_ROLLBACK.md` | NEW | Production deployment runbook, operations guide, and rollback runbook. |

---

## 5. Security & Data Protection Considerations

1. **Zero Secret Exposure:**
   - Diagnostic endpoints and logs must NEVER print raw tokens, API keys, or service role secrets.
   - All diagnostic endpoints strip headers before serializing requests.
   - The verified `SecretRedactionFilter` will wrap any new log formatters.
2. **Multi-Tenant Boundary Enforcement:**
   - Every file read, write, or process lookup must verify the caller's `user_id`.
   - Lead records saved to `data/leads_*.json` are strictly associated with the owner user ID.
3. **SSRF Defense Continuity:**
   - No modifications will be made to `PinnedHTTPSConnection` or the IP blacklist.
   - Scraper communication remains pinned to `127.0.0.1:8085` via internal loopback.
4. **CSV Injection Defense:**
   - All exported CSV fields continue through `sanitize_csv_cell()` to prevent spreadsheet formula execution.
5. **Backup Archive Traversal Safety:**
   - `scripts/restore_data.py` must inspect every archive entry and reject filenames containing `..` or absolute paths (preventing Zip Slip / directory traversal vulnerabilities).

---

## 6. Measurable Release Acceptance Criteria

Phase 4 release acceptance requires passing the following quantitative criteria:

| Category | Criterion | Target Threshold | Verification Method |
| :--- | :--- | :--- | :--- |
| **Automated Tests** | Full test suite passing | 100% (97 existing + ~20 Phase 4 tests) | `scripts/run_all_tests.sh` |
| **E2E Journey** | Full lifecycle execution time | < 5.0 seconds | `tests/test_phase4_e2e_journey.py` |
| **Process Cleanup** | Child process termination on `SIGTERM` | < 5.0 seconds, 0 orphan processes | `tests/test_phase4_reliability_resilience.py` |
| **Multi-Worker Safety**| Max concurrent jobs strictly enforced | 0 excess jobs admitted | Concurrency stress test via `fcntl` lock |
| **Diagnostics** | `/api/diagnostics` response latency | < 50ms | HTTP benchmark test |
| **Secret Redaction** | Leaked secrets in logs / diagnostics | 0 leaked secrets | Grep verification across test output |
| **Backup Integrity** | Backup and restore roundtrip | 100% bitwise matching SHA-256 | Test backup/restore script roundtrip |
| **Reverse Proxy** | Security headers verification | HSTS, CSP, X-Frame-Options present | Automated config syntax audit |

---

## 7. Deployment Prerequisites & Rollback Strategy

### 7.1 Deployment Prerequisites
Before initiating production deployment:
1. **OS Environment:** Linux host (Ubuntu 22.04 LTS or Debian 12 recommended) with Python 3.10+, Docker, and Docker Compose.
2. **Network Ports:** Port 80 and 443 open to internet (handled by Caddy/Nginx); Port 3000 bound strictly to `127.0.0.1` (never exposed directly).
3. **Domain & DNS:** A/AAAA records pointed to host public IP for automated TLS issuance.
4. **Dedicated System User:** Dedicated unprivileged system user `leadmap` with ownership of `/opt/leadmap` (or deployment path).
5. **Environment Configuration:** Valid `.env` populated with secure `SESSION_SECRET` (minimum 32 bytes high-entropy random string) and optional Supabase credentials.

### 7.2 Rollback Strategy
If any production anomaly or verification failure occurs during or after deployment:
1. **Immediate Service Isolation:**
   ```bash
   sudo systemctl stop leadmap
   ```
2. **Binary & Configuration Rollback:**
   ```bash
   git checkout <PREVIOUS_STABLE_COMMIT_OR_TAG>
   ```
3. **Data State Restoration (if storage was corrupted):**
   ```bash
   python3 scripts/restore_data.py --archive /var/backups/leadmap/leadmap_data_<TIMESTAMP>.tar.gz
   ```
4. **Service Restart & Health Verification:**
   ```bash
   sudo systemctl start leadmap
   curl -f http://127.0.0.1:3000/api/health
   ```
5. **Downtime Target:** Rollback process completed in under 2 minutes with zero persistent data loss.

---

## 8. Risks, Assumptions, and Out-of-Scope Work

### 8.1 Risks & Mitigation
* **Risk: Multi-Worker Race Conditions.**
  - *Mitigation:* Implement POSIX file locking (`fcntl.flock`) on `data/.job_admission.lock` so concurrency limits hold regardless of WSGI worker count.
* **Risk: Orphan Scraper Subprocesses on Abrupt Server Crash.**
  - *Mitigation:* Combined startup crash recovery (`recover_interrupted_jobs()`) plus runtime `SIGTERM`/`SIGINT` traps.
* **Risk: Large Lead Dataset Memory Pressure.**
  - *Mitigation:* Bulk remove operations are kept in-memory for curated export without mutating persistent JSON files; client table uses DOM pagination.

### 8.2 Assumptions
* Scraper container remains `gosom/google-maps-scraper:v1.18.1` reachable on `127.0.0.1:8085`.
* Production deployment will utilize an upstream reverse proxy (Caddy or Nginx) for TLS termination.
* Live Supabase database usage remains optional; application operates completely autonomously with local JSON persistence when Supabase credentials are not supplied.

### 8.3 Out-of-Scope Work
* **No n8n or Redis:** No external message brokers, Celery, or external workflow orchestrators will be introduced.
* **No Database Migration Rewrites:** The verified Phase 1B/2 Supabase schema will not be refactored.
* **No Complete Frontend Redesign:** The existing vanilla JS and Leaflet map UI is preserved; work is restricted to resilient polling and state synchronization.

---

## 9. Final Review & Readiness Recommendation

### 9.1 Verification of Constraints
* **Contradiction Check:** All proposed work aligns with the verified Phase 1B/2/3 baseline. Single-binary simplicity is maintained.
* **Scope Creep Check:** No unneeded libraries or heavy dependencies are introduced; Python standard library is leveraged for locks, signals, and backups.
* **Verifiable Claims:** Claims regarding live Supabase permissions, browser polling, and HTTPS are explicitly recognized as unverified until actual evidence is generated.

### 9.2 Recommendation
**Status:** The Phase 4 Production Readiness and End-to-End Validation Plan is complete, rigorous, and architecturally sound.

**Recommendation:** Await user review and approval before proceeding to implementation. Upon approval, begin execution starting with **Sub-Phase 4A (End-to-End User Journey Tests & UX Resilience)**.
