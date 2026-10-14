# LeadMap Pro — Phase 2: Reliability and Job Management Report

## 1. Executive Summary

Phase 2 implementation introduces bounded, reliable, restart-safe scraping job management to LeadMap Pro. Prior to Phase 2, job execution spawned unmetered concurrent background threads and child subprocesses, lacked persistent disk records during early job stages, had no crash recovery mechanism for jobs left running across server restarts, and risked leaking child processes or exceeding system capacity under load.

With Phase 2, job admission is strictly metered with thread-safe locking, job states are durably persisted to disk from admission through completion using atomic file replacement (`.tmp` + `os.replace`), server restarts automatically and idempotently mark orphaned jobs as failed, cancellation terminates job-specific process trees with graceful SIGTERM and bounded SIGKILL escalation, and completed jobs/orphaned temporary files are pruned according to configurable retention thresholds while strictly protecting active jobs.

All existing Phase 1 and Phase 1B security controls (session locking, authenticated endpoints, SSRF validation, CSV injection neutralization, and cross-user isolation) were preserved without regression.

---

## 2. Configuration Parameters

The following configurable parameters govern job scheduling, resource limits, and retention policies in [dashboard.py](file:///home/vighnesh/Documents/B2B/dashboard.py):

| Parameter | Default Value | Environment Variable | Description |
|---|---|---|---|
| `MAX_CONCURRENT_JOBS` | `2` | `LEADMAP_MAX_CONCURRENT_JOBS` | Maximum number of simultaneous active (`working` / `starting` / `stopping`) scraping jobs permitted. Excess start requests are rejected immediately with HTTP 429. |
| `JOB_TIMEOUT_SECONDS` | `600` (10 min) | `LEADMAP_JOB_TIMEOUT_SECONDS` | Bounded execution timeout applied to scraper subprocesses to prevent orphaned hangs. |
| `JOB_RETENTION_DAYS` | `30` | `LEADMAP_JOB_RETENTION_DAYS` | Age threshold after which completed or failed job records (`leads_<job_id>.json`) are pruned. Active jobs are never pruned regardless of age. |
| `MAX_COMPLETED_JOBS` | `100` | `LEADMAP_MAX_COMPLETED_JOBS` | Maximum number of terminal job records retained on disk. When exceeded, the oldest completed jobs are deleted first. |
| Temporary File Age Limit | `3600` (1 hr) | Built-in | Orphaned `.tmp_*` files left in the data directory by crashed writes are deleted if older than 1 hour. |

---

## 3. Job Lifecycle State Machine

The scraping job state machine transitions predictably through the following stages:

```
                  ┌────────────────┐
                  │ POST /scrape   │
                  │ (Admission)    │
                  └───────┬────────┘
                          │ (JOB_ADMISSION_LOCK check)
             ┌────────────┴────────────┐
             ▼                         ▼
      [Capacity Full]           [Capacity Available]
       HTTP 429 + Retry           Create durable record
                                  Status: "working", Stage: "Admitted"
                                       │
                                       ▼
                                ┌──────────────┐
                                │ Background   │
                                │ Processor    │
                                └──────┬───────┘
                     ┌─────────────────┼─────────────────┐
                     ▼                 ▼                 ▼
             [Normal Success]   [User Cancel]    [Subprocess Error / Crash]
             Status: "ok"       Status: "cancelled" Status: "failed"
             Leads saved        Processes killed    Error details saved
             Stage: "Completed" Stage: "Cancelled"  Stage: "Failed"
```

### State Definitions:
1. **`working`**: The job has been admitted and is actively executing (scraping, cleansing, or enriching). Active jobs consume concurrency capacity.
2. **`ok`**: The job successfully completed its full pipeline. Results are durable on disk.
3. **`failed`**: The job encountered an unhandled exception, subprocess timeout, or was interrupted by a process restart.
4. **`cancelled`**: The job was explicitly stopped by its owner via `POST /api/scrape/stop`. Child processes have been terminated.

---

## 4. Key Architectural Changes

### Task 1 — Bounded Job Scheduling
* **Admission Lock:** Implemented `JOB_ADMISSION_LOCK = threading.Lock()` around job admission in `POST /api/scrape/start`.
* **Active Job Counting:** Introduced `_get_active_job_count()`, which checks both active in-memory cache and persisted records on disk to calculate genuine active job load.
* **HTTP 429 Rejection:** When `active_count >= MAX_CONCURRENT_JOBS`, incoming scrape requests are immediately rejected with HTTP status 429 (`Too Many Requests`), a detailed error message, and a `Retry-After: 30` header.
* **Bounded Startup:** In-memory reservations and locks are released cleanly in `finally` blocks if thread spawning or parameter extraction fails.

### Task 2 — Persistent Job State
* **Durable Records from Inception:** Jobs are written to disk (`data/leads_{job_id}.json`) via `_create_durable_job()` upon admission, establishing durability before background threads begin.
* **Atomic Writes:** All state updates use `_atomic_json_write()`, writing to a unique temporary file (`.tmp_{job_id}_{timestamp}`) followed by an atomic `os.replace` to prevent corrupted JSON on sudden crash.
* **Secret Stripping:** Durable job records filter sensitive parameters using a strict regex: `(token|secret|password|auth|credential|api_?key|^key$|_key$|^key_)`. Legitimate parameters like `keyword`, `city`, and `country` are preserved while session keys, auth tokens, and API credentials are never written to disk.
* **Safe Status Ingestion:** `GET /api/jobs` and `GET /api/job/<id>/status` read status, progress, stage, error details, and metrics directly from disk records rather than hardcoding default status.

### Task 3 — Restart and Crash Recovery
* **Startup Orphan Detection:** `recover_interrupted_jobs()` runs automatically when `dashboard.py` initializes.
* **Safe Transition:** It scans all `leads_*.json` records in the data directory. Any job left in `working`, `starting`, `stopping`, or `running` state is transitioned to `failed`:
  * `status`: `"failed"`
  * `stage`: `"Process interrupted by server restart"`
  * `error`: `"Server restarted while job was in progress"`
  * `completed_at`: `time.time()`
* **No Unvalidated Auto-Relaunch:** The system intentionally avoids blindly relaunching scraper subprocesses on reboot, avoiding duplicate billing, unexpected network traffic, and cross-session input poisoning.
* **Strict Idempotency:** Running `recover_interrupted_jobs()` repeatedly does not modify already-recovered records or touch completed/failed jobs.

### Task 4 — Safe Cancellation and Process Cleanup
* **Process Registry:** `JOB_PROCESSES = {}` and `PROCESS_LOCK = threading.Lock()` track every spawned subprocess (`Popen`) keyed by `job_id`.
* **Ownership Verification:** `POST /api/scrape/stop` strictly checks `job.get("user_id") == current_user.get("id")` before signaling any process.
* **Two-Stage Termination:** `_terminate_job_processes(job_id, grace_timeout=5.0)` issues `SIGTERM` (`terminate()`), waits up to 5 seconds, and escalates to `SIGKILL` (`kill()`) if the scraper process fails to terminate.
* **Process Isolation:** Terminating Job A never touches or interrupts subprocesses belonging to Job B.
* **Guaranteed Cleanup:** `background_job_processor` registers child processes upon launch and unregisters them in a `finally` block on completion, cancellation, or error.

### Task 5 — Stale-Job and Temporary-File Retention
* **Automatic Pruning:** `cleanup_stale_jobs_and_temp_files()` runs on startup and can be scheduled periodically.
* **Active-Job Protection:** Jobs with `status == "working"` (or other active states) are strictly excluded from deletion, even if their creation timestamp is older than `JOB_RETENTION_DAYS`.
* **Cap Enforcement:** If completed/failed jobs exceed `MAX_COMPLETED_JOBS` (100), the oldest terminal records are pruned first.
* **Orphaned File Cleanup:** Dangling `.tmp_*` files created during atomic writes that are older than 1 hour are cleaned up automatically.
* **Safe Logging:** Retention cleanup errors are caught and logged without exposing user IDs or lead contents.

---

## 6. Verification and Test Results

An automated test suite was constructed in [tests/test_phase2_reliability.py](file:///home/vighnesh/Documents/B2B/tests/test_phase2_reliability.py) alongside the full existing Phase 1, Phase 1B, and Phase 5 regression suites.

### Automated Test Execution Log

```
Suite 1: tests/test_phase2_reliability.py (Phase 2 Reliability)
- test_active_job_counting_and_limit_enforcement ..................... PASS
- test_simultaneous_job_admission_contention ......................... PASS
- test_durable_job_survives_memory_wipe .............................. PASS
- test_durable_record_creation_and_secret_exclusion .................. PASS
- test_recover_interrupted_jobs_on_startup ............................ PASS
- test_recovery_idempotency .......................................... PASS
- test_active_jobs_strictly_protected_from_deletion .................. PASS
- test_max_completed_jobs_cap_pruning ................................ PASS
- test_orphaned_temporary_file_pruning ............................... PASS
- test_cancellation_does_not_affect_other_jobs ........................ PASS
- test_process_forced_kill_on_timeout ................................ PASS
- test_process_registration_and_termination .......................... PASS
- test_csv_injection_formula_triggers_neutralized .................... PASS
- test_ssrf_validation_unweakened .................................... PASS
Result: 14/14 PASSED (0.034s)

Suite 2: tests/test_phase1b_security.py (Phase 1B Security Regression)
- 12 tests covering CSV boundaries, formula escaping, phone numbers,
  SSRF DNS rebinding, redirect loops, session concurrency, and RLS policies.
Result: 12/12 PASSED (0.053s)

Suite 3: tests/test_ssrf_and_config_phase5.py (SSRF Defense & Network Isolation)
- 27 tests covering socket pinning, cloud metadata blocks, IPv4-mapped IPv6,
  redirect limits, content size aborts, and configuration standardization.
Result: 27/27 PASSED (3.099s)

Suite 4: tests/test_security_phase1.py (Phase 1 Auth & Core Security)
- 26 tests covering auth lifecycle, signup/login/logout, CORS headers,
  session expiry, job path traversal rejection, payload size limits, and CSV sanitization.
Result: 26/26 PASSED (22.539s)

Suite 5: tests/test_security_phase2.py (Phase 2 Password Reset & Session Revocation)
- 10 tests covering forgot-password enumeration safety, timing-safe hashes,
  session revocation upon reset, single-use reset tokens, and login rate limiting.
Result: 10/10 PASSED (14.682s)
```

### Overall Automated Test Summary

| Test Suite | Total Tests | Passed | Failed | Status |
|---|---|---|---|---|
| `test_phase2_reliability.py` | 14 | 14 | 0 | **PASS** |
| `test_phase1b_security.py` | 12 | 12 | 0 | **PASS** |
| `test_ssrf_and_config_phase5.py` | 27 | 27 | 0 | **PASS** |
| `test_security_phase1.py` | 26 | 26 | 0 | **PASS** |
| `test_security_phase2.py` | 10 | 10 | 0 | **PASS** |
| **Total Automated Tests** | **89** | **89** | **0** | **ALL GREEN** |

---

## 7. Modified Files

| File | Change Type | Summary of Changes |
|---|---|---|
| [dashboard.py](file:///home/vighnesh/Documents/B2B/dashboard.py) | **MODIFIED** | Added concurrency scheduler, durable record initialization, atomic file persistence, restart crash recovery, graceful subprocess termination, stale job/temp file cleanup, and updated job status endpoints. |
| [tests/test_phase2_reliability.py](file:///home/vighnesh/Documents/B2B/tests/test_phase2_reliability.py) | **NEW** | Added 14 unit and integration tests covering concurrency limits, crash recovery, process termination, secret filtering, retention pruning, and security regression checks. |
| [docs/PHASE2_RELIABILITY_REPORT.md](file:///home/vighnesh/Documents/B2B/docs/PHASE2_RELIABILITY_REPORT.md) | **NEW** | Comprehensive Phase 2 reliability and job management documentation. |

---

## 8. Local Commands to Start and Test

### 1. Run Unit and Reliability Test Suites:
```bash
python3 -m unittest -v tests/test_phase2_reliability.py
python3 -m unittest -v tests/test_phase1b_security.py
python3 -m unittest -v tests/test_ssrf_and_config_phase5.py
```

### 2. Run Full Integration and Server Security Suites:
```bash
# Start server in background
python3 dashboard.py &
SERVER_PID=$!
sleep 2

# Execute server-dependent test suites
python3 -m unittest -v tests/test_security_phase1.py
python3 -m unittest -v tests/test_security_phase2.py

# Cleanly stop test server
kill -TERM $SERVER_PID
```

### 3. Start Application in Production/Development:
```bash
# Standard local start (port 3000, 127.0.0.1)
python3 dashboard.py
```

---

## 9. Remaining Limitations and Notes

1. **Subprocess Scraping Engine Dependency:** LeadMap Pro invokes Python scraper scripts (`scripts/scrape.py` / `scripts/cleanser.py`) as child processes. While child processes are registered and bounded by timeouts and SIGKILL escalation, extreme OS-level kills (`SIGKILL` to `dashboard.py` itself) could leave child processes briefly orphaned until system reaper handles them.
2. **Local Multi-Worker Coordination:** Concurrency locks (`JOB_ADMISSION_LOCK`) use in-process Python threading locks and disk inspection. In multi-process WSGI deployments (e.g., multiple Gunicorn workers), shared admission counting relies on file-system scanning in `data/`; an OS-level file lock (like `fcntl.flock`) or Redis semaphore would be required if scaling across multiple distinct server instances.
