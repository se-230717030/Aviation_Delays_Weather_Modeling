# Pipeline — Aviation Delays & Weather Modeling

## 1. The graph

```text
[OpenSky Network API (/states/all) & Open-Meteo API (/v1/forecast)]
                              │
                              ▼  (1) src/ingest.py --run-time <UTC> (timeout: 120s)
     [data/raw/opensky/<STAMP>.json  &  data/raw/openmeteo/<STAMP>.json]
                              │
                              ▼  (2) src/load.py --run-time <UTC> (timeout: 180s)
[data/aviation.db — M2 Raw STRICT Tables: aircraft, flight_state, location, weather_reading, quarantine]
                              │
                              ▼  (3) src/clean.py --run-time <UTC> (timeout: 180s) + [data/mappings/*.csv]
[data/aviation.db — M3 Cleaned STRICT Tables (*_clean) & Views (*_resolved)]
                              │
                              ▼  (4) src/validate.py --run-time <UTC> (timeout: 60s)
                   [PASS / WARN / STOP Gate]
                              │
                              └──► Every executed step logs exit_code, timestamps & detail to [data/pipeline_log.db]
                                                                                                         ▲
                                                                                                         │ (Read-only: mode=ro)
                                                                                                  src/monitor.py --now <UTC>
```

- **Step 1 — `src/ingest.py`**: Input: OpenSky `/states/all`, Open-Meteo `/v1/forecast` (Istanbul Airport `41.25, 28.75`, self-healing `past_days` + `forecast_days=7`), latest raw filenames, and `--run-time` · Output: atomic timestamped JSON payloads in `data/raw/opensky/<YYYYMMDDTHHMMSSZ>.json` and `data/raw/openmeteo/<YYYYMMDDTHHMMSSZ>.json` · Rebuildable from: external APIs (Open-Meteo up to 7 days back in automated mode; OpenSky live-only).
- **Step 2 — `src/load.py`**: Input: all landed raw JSON files in `data/raw/opensky/` and `data/raw/openmeteo/`, `sql/schema.sql`, and `--run-time` · Output: M2 `STRICT` tables in `data/aviation.db` (`aircraft`, `flight_state`, `location`, `weather_reading`, `quarantine`) via guarded `UPSERT` with `0.25°` weather grid snapping · Rebuildable from: `data/raw/` via `python src/run_pipeline.py --skip-ingest`.
- **Step 3 — `src/clean.py`**: Input: M2 tables in `data/aviation.db`, `sql/clean_schema.sql`, `src/clean_config.py`, `data/mappings/country_mapping.csv`, `data/mappings/aircraft_identity.csv`, and `--run-time` · Output: M3 `STRICT` cleaned tables (`aircraft_clean`, `flight_state_clean`, `weather_reading_clean`) and analytical views (`flight_state_resolved`, `weather_reading_resolved`) · Rebuildable from: M2 tables in `data/aviation.db` (full atomic rebuild).
- **Step 4 — `src/validate.py`**: Input: M2 and M3 tables in `data/aviation.db` (`allow_create=False`) and `--run-time` · Output: `PASS` / `WARN` / `STOP` health report on stdout and exit code (`0` or `1`) · Rebuildable from: stateless check; re-runnable anytime against `data/aviation.db`.
- **External Monitor — `src/monitor.py`**: Input: `data/pipeline_log.db` and `data/aviation.db` (both opened read-only via `mode=ro`) and `--now` · Output: 4-check `PASS` / `ALARM` report (`last_success`, `gaps`, `completeness`, `freshness`) and exit code (`0` or `1`) · Rebuildable from: stateless external check; re-runnable anytime against both databases.

---

## 2. Design decisions

| Decision | Our choice | Why |
| :--- | :--- | :--- |
| **Ingestion: where each run starts** | Reads the latest timestamped filename in `data/raw/openmeteo/` and `data/raw/opensky/`. Open-Meteo computes `gap_days` since the last file and requests `past_days = min(7, max(1, gap_days + 1))` plus `forecast_days=7`; OpenSky queries live `/states/all` at `--run-time` and warns if the gap exceeds `2.0h` | Automatically heals missed runs for hourly weather up to 7 days without manual intervention, while logging an explicit `WARN` whenever an OpenSky outage exceeds 2 hours |
| **Overlap** | `24 hours` (`OPENMETEO_OVERLAP_DAYS = 1`) for Open-Meteo; `0 hours` (`OPENSKY_SOURCE_MEMORY_HOURS = 0`) for OpenSky | Open-Meteo revises recent hourly values retroactively; a 1-day overlap captures late model updates and heals missed runs within our 6-hour schedule while keeping payloads bounded at `192` hourly rows on normal runs (see delay analysis below) |
| **Loading** | Full reload of all landed raw JSON files in `data/raw/opensky/` and `data/raw/openmeteo/` inside a single atomic transaction (`with con:`) on every run | Simple, deterministic, and immune to manifest drift. Loads `7` raw files (`40,769` net new rows) in **`0.50s`** and `9` raw files (`51,263` flight states, `504` weather readings) in **`0.66s`**. At ~11,000 flight states per 6-hour run (4 runs/day), full reload remains well under 60s up to ~700 runs (~6 months), after which we would migrate to a transactional file manifest table |
| **Version rule** | Guarded `UPSERT` using `retrieved_at` (`ON CONFLICT (...) DO UPDATE SET ... WHERE excluded.retrieved_at > <table>.retrieved_at`) across `aircraft`, `flight_state`, `location`, and `weather_reading` | Guarantees that the newest retrieval always wins regardless of file read order, so re-loading older raw files during backfills or recovery can never overwrite newer versions |
| **Cleaning** | Full rebuild (`DELETE` + `INSERT` inside one atomic transaction) of `country_mapping`, `aircraft_identity`, and all `*_clean` tables on every run (`0.67s`–`0.88s`), leaving M2 tables untouched | Ensures all flags and distribution-based checks (median/MAD) are computed consistently over the complete accumulated history with zero stale rows; historical `unusual_low`/`unusual_high` flags may shift slightly as history grows |
| **Validation updates (M5)** | Run-time freshness (`FRESHNESS_MAX_HOURS = 36` from `--run-time`), batch volume check (`0.10x`–`10.0x` of historical median), and airborne-only missing altitude check (`MAX_AIRBORNE_MISSING_SHARE_WARN = 0.05`) | Measures freshness against the pipeline clock instead of `MAX(observed_at)` so stalled feeds are caught, detects empty or anomalous batches, and removes the permanent M3 `WARN` caused by on-ground transponders omitting barometric altitude |
| **The clock** | `--run-time` ISO 8601 UTC parameter parsed via `src/db.py:parse_run_time()` (defaults to current UTC) and passed from `run_pipeline.py` to every child step; `wall_clock_utc()` (`datetime.now()`) is restricted exclusively to log `started_at` and `finished_at` | Makes every run, freshness check, and offline rebuild fully deterministic and reproducible |

### How late our data arrives
We measure source arrival lag by comparing each record's event timestamp (`observed_at`) against its retrieval timestamp (`retrieved_at`) for realized observations (`observed_at <= retrieved_at`):

```sql
SELECT
    'flight_state' AS source_table,
    COUNT(*) AS n_rows,
    ROUND(AVG((julianday(retrieved_at) - julianday(observed_at)) * 86400), 1) AS avg_lag_sec,
    ROUND(MAX((julianday(retrieved_at) - julianday(observed_at)) * 86400), 1) AS max_lag_sec
FROM flight_state
WHERE observed_at <= retrieved_at
UNION ALL
SELECT
    'weather_reading' AS source_table,
    COUNT(*) AS n_rows,
    ROUND(AVG((julianday(retrieved_at) - julianday(observed_at)) * 24), 2) AS avg_lag_hours,
    ROUND(MAX((julianday(retrieved_at) - julianday(observed_at)) * 24), 2) AS max_lag_hours
FROM weather_reading
WHERE observed_at <= retrieved_at;
```

| Source Table | Observed Lag Behaviour | Evidence from Pipeline Runs |
| :--- | :--- | :--- |
| `flight_state` (OpenSky) | Live ADS-B state vectors arrive with ~`10s`–`15s` transmission lag; OpenSky drops stale aircraft vectors upstream | In `run_id=9c1080ed` (`run_time=2026-11-22T16:27:44Z`), the latest `flight_state` `observed_at` was `2026-11-22T16:27:34Z` (`10.0s` lag) |
| `weather_reading` (Open-Meteo) | Hourly timestamps align to top-of-hour UTC (`HH:00:00Z`), with values updated retroactively across the `past_days=1` (`24h`) overlap window | In `run_id=9c1080ed`, `ingest.py` fetched `192` hourly rows (`past_days=1`) and `load.py` reported `504 total (+0 net new)` because all overlapping hours were already present and guarded by `retrieved_at` |

*(TODO: Paste the exact numeric output table from the SQL latency query above after the 7-day run completes and before tagging `m5`.)*

### The source's memory
- **Open-Meteo (`/v1/forecast`):** Automated catch-up is capped at **`OPENMETEO_MAX_PAST_DAYS = 7`** (`7 days` / `168 hours`) per call (Open-Meteo supports up to `92` past days manually). Any outage shorter than 7 days heals automatically on the next scheduled run; an outage longer than 7 days logs a `WARN` in `ingest.py` and leaves permanent hourly gaps unless manually backfilled. `src/monitor.py` detects any missing hour in the trailing 48-hour window (`COMPLETENESS_HOURS = 48`) via its recursive-CTE `completeness` check and raises an `ALARM`.
- **OpenSky Network (`/states/all`):** Has **`0` hours of historical memory (`OPENSKY_SOURCE_MEMORY_HOURS = 0`)**—it returns only live aircraft active at the moment of the HTTP request. Any outage causing a missed 6-hour run permanently loses that global flight snapshot and cannot be backfilled from the API. We detect such outages loudly via `ingest.py`'s `> 2.0h` gap `WARN` and `src/monitor.py`'s `gaps` (`MAX_GAP_HOURS = 14.0`), `last_success` (`LAST_SUCCESS_MAX_HOURS = 14.0`), and `freshness` (`FRESHNESS_MAX_HOURS = 14.0`) `ALARM` checks.

---

## 3. Operations

- **Run by hand:**
  ```bash
  python src/run_pipeline.py
  ```
  Verified full 4-step run output (`run_id=9c1080ed`):
  ```text
  === PIPELINE RUN START | run_id=9c1080ed | run_time=2026-11-22T16:27:44Z | skip_ingest=False ===

  --- Running src/ingest.py (timeout=120s) ---
  run_time:        2026-11-22T16:27:44Z
  opensky:         11088 states -> 20261122T162744Z.json
  openmeteo:       192 hourly rows (past_days=1) -> 20261122T162744Z.json
  STATUS: COMPLETE

  --- Running src/load.py (timeout=180s) ---
  run_time:           2026-11-22T16:27:44Z
  files read:         9 raw files
  aircrafts:          1904 new this run
  locations:          0 new this run
  flight states:      51263 total (+10998 net new)
  weather readings:   504 total (+0 net new)
  rows_added:         10998
  values cleaned:     0
  quarantined:        290 records
  elapsed_sec:        0.66s
  foreign_key_check:  OK
  STATUS: COMPLETE

  --- Running src/clean.py (timeout=180s) ---
  run_time: 2026-11-22T16:27:44Z (elapsed: 0.88s)
  aircraft_clean: 20,029 rows (raw: 20,029)
    identity         -> ok: 20024  aliased_bit_error: 2  aliased_duplicate_callsign: 2  aliased_re_registered: 1
    callsign         -> ok: 19574  missing_not_reported: 441  invalid_sentinel: 14
    origin_country   -> ok: 19456  encoding_fixed: 1  standardised_name: 572

  flight_state_clean: 51,263 rows (raw: 51,263)
    longitude        -> ok: 51263
    latitude         -> ok: 51263
    baro_altitude    -> ok: 45507  missing_on_ground: 4715  missing_sensor_drop: 42  invalid_negative_airborne: 114  invalid_negative_ground: 2  invalid_ceiling: 8  cross_field_ground_alt: 17  unusual_high: 858
    velocity         -> ok: 50663  missing_not_reported: 6  invalid_supersonic: 14  cross_field_ground_fast: 9  cross_field_air_stationary: 65  unusual_high: 506
    on_ground        -> ok: 51263

  weather_reading_clean: 504 rows (raw: 504)
    temperature_2m   -> ok: 490  unusual_low: 14
    wind_speed_10m   -> ok: 504
    precipitation    -> ok: 504

  mappings: country_mapping 11 rows (matched 11 of 11; unmatched: none)
            aircraft_identity 5 rows (matched 5 of 5; unmatched: none)
  STATUS: COMPLETE

  --- Running src/validate.py (timeout=60s) ---
  === M5 DATA QUALITY VALIDATION (run_time: 2026-11-22T16:27:44Z) ===
  PASS  Cleaned table row counts equal raw tables (ac=20029, fs=51263, wr=504)
  PASS  No impossible values survive in cleaned columns
  PASS  foreign_key_check is OK
  PASS  Airborne missing altitude share is 0.35% (<= 5% threshold; 160/46290)
  PASS  Freshness from run_time (2026-11-22T16:27:44Z) within 36h (fs=2026-11-22T16:27:34Z, wr=2026-11-22T16:27:44Z)
  PASS  Volume check: latest batch=11,030 vs median=10,648 (5 batches)
  PASS  No 'fast on ground' cross-field contradictions survived

  VALIDATION SUCCESS: Pipeline is healthy.

  PIPELINE SUCCEEDED: run_id=9c1080ed completed all 4 steps (run_time=2026-11-22T16:27:44Z).
  ```

- **Rebuild / backfill:**
  ```bash
  python src/run_pipeline.py --skip-ingest --run-time 2026-10-01T12:00:00Z
  ```
  Rebuilds `data/aviation.db` offline from all landed JSON files in `data/raw/` without network calls. Running `--skip-ingest` twice with the same `--run-time` produces identical tables via guarded `UPSERT` and full atomic clean rebuild. Verified offline rebuild output (`run_id=ea8caf91`):
  ```text
  === PIPELINE RUN START | run_id=ea8caf91 | run_time=2026-10-01T12:00:00Z | skip_ingest=True ===

  --- Running src/load.py (timeout=180s) ---
  run_time:           2026-10-01T12:00:00Z
  files read:         7 raw files
  aircrafts:          18125 new this run
  locations:          1 new this run
  flight states:      40265 total (+40265 net new)
  weather readings:   504 total (+504 net new)
  rows_added:         40769
  values cleaned:     0
  quarantined:        232 records
  elapsed_sec:        0.50s
  foreign_key_check:  OK
  STATUS: COMPLETE

  --- Running src/clean.py (timeout=180s) ---
  run_time: 2026-10-01T12:00:00Z (elapsed: 0.67s)
  aircraft_clean: 18,125 rows (raw: 18,125)
    identity         -> ok: 18120  aliased_bit_error: 2  aliased_duplicate_callsign: 2  aliased_re_registered: 1
    callsign         -> ok: 17736  missing_not_reported: 380  invalid_sentinel: 9
    origin_country   -> ok: 17604  encoding_fixed: 1  standardised_name: 520

  flight_state_clean: 40,265 rows (raw: 40,265)
    longitude        -> ok: 40265
    latitude         -> ok: 40265
    baro_altitude    -> ok: 35773  missing_on_ground: 3694  missing_sensor_drop: 31  invalid_negative_airborne: 97  invalid_negative_ground: 1  invalid_ceiling: 8  cross_field_ground_alt: 12  unusual_high: 649
    velocity         -> ok: 39820  missing_not_reported: 4  invalid_supersonic: 10  cross_field_ground_fast: 9  cross_field_air_stationary: 42  unusual_high: 380
    on_ground        -> ok: 40265

  weather_reading_clean: 504 rows (raw: 504)
    temperature_2m   -> ok: 490  unusual_low: 14
    wind_speed_10m   -> ok: 504
    precipitation    -> ok: 504

  mappings: country_mapping 11 rows (matched 11 of 11; unmatched: none)
            aircraft_identity 5 rows (matched 5 of 5; unmatched: none)
  STATUS: COMPLETE

  --- Running src/validate.py (timeout=60s) ---
  === M5 DATA QUALITY VALIDATION (run_time: 2026-10-01T12:00:00Z) ===
  PASS  Cleaned table row counts equal raw tables (ac=18125, fs=40265, wr=504)
  PASS  No impossible values survive in cleaned columns
  PASS  foreign_key_check is OK
  PASS  Airborne missing altitude share is 0.36% (<= 5% threshold; 132/36366)
  PASS  Freshness from run_time (2026-10-01T12:00:00Z) within 36h (fs=2026-11-22T16:10:25Z, wr=2026-11-22T16:10:27Z)
  PASS  Volume check: latest batch=10,944 vs median=10,551 (4 batches)
  PASS  No 'fast on ground' cross-field contradictions survived

  VALIDATION SUCCESS: Pipeline is healthy.

  PIPELINE SUCCEEDED: run_id=ea8caf91 completed all 3 steps (run_time=2026-10-01T12:00:00Z).
  ```

- **External monitoring:** `python src/monitor.py [--now <ISO-8601-UTC>]` (opens `data/pipeline_log.db` and `data/aviation.db` read-only via `mode=ro`).
- **Exit codes:** `0` success (all executed steps succeeded / all `monitor.py` checks `PASS`) · `1` step failure or `ALARM` (HTTP 401/403 or exhausted retries in `ingest.py`, atomic rollback in `load.py`/`clean.py`, `STOP` check in `validate.py`, or any `ALARM` in `monitor.py`; downstream steps do not run) · `75` already running (`data/pipeline.lock` already exists via `os.O_CREAT | os.O_EXCL`, zero steps run) · `124` timeout (step exceeded `STEP_TIMEOUTS`: `ingest.py` `120s`, `load.py` `180s`, `clean.py` `180s`, `validate.py` `60s`, or `PIPELINE_STEP_TIMEOUT_SEC` override).
- **Logs:**
  - Structured per-step run log: `data/pipeline_log.db` (table `pipeline_run`: `run_id`, `run_time`, `step`, `started_at`, `finished_at`, `exit_code`, `detail`; gitignored and submitted separately).
  - Lock file: `data/pipeline.lock` (created atomically on startup, removed in `finally:`, gitignored).
  - Scheduled console output: `logs/pipeline.log` (via `ops/run_pipeline.bat`) and `logs/monitor.log` (via `ops/run_monitor.bat`), both gitignored.

---

## 4. Evidence

### The schedule
- **Host machine:** Muhammet Ali Öztürk's Windows workstation (`DESKTOP-IU6TDC9\Ali`, working directory `C:\Users\Ali\Desktop\Aviation_Delays_Weather_Modeling`).
- **Interval:** Every **6 hours (`PT6H` — 4 runs/day)** starting `2026-11-22T20:07:00` local time (`MultipleInstancesPolicy: IgnoreNew`, `LogonType: Password`, `StartWhenAvailable: true`, `DisallowStartIfOnBatteries: false`). `src/monitor.py` enforces a `14.0h` threshold (`2 × 6h` interval + `2h` buffer) for `last_success`, `gaps`, and `freshness`.
- **Committed ops files:** [`ops/AviationPipelineRun.xml`](../ops/AviationPipelineRun.xml), [`ops/run_pipeline.bat`](../ops/run_pipeline.bat), and [`ops/run_monitor.bat`](../ops/run_monitor.bat).

### The run log
Summary queries executed against `data/pipeline_log.db`:

```sql
SELECT
    MIN(run_time) AS first_run,
    MAX(run_time) AS last_run,
    COUNT(DISTINCT run_id) AS total_runs,
    COUNT(DISTINCT substr(run_time, 1, 10)) AS days_covered,
    SUM(CASE WHEN exit_code != 0 THEN 1 ELSE 0 END) AS failed_steps
FROM pipeline_run;

SELECT step, exit_code, COUNT(*) AS occurrences, MAX(detail) AS sample_detail
FROM pipeline_run
WHERE exit_code != 0
GROUP BY step, exit_code
ORDER BY occurrences DESC;
```

```text
[TODO: Paste the final 7+ consecutive day summary and failure-by-step output from data/pipeline_log.db before tagging m5.]
```

### Missed runs
- **What we did:** We deliberately allowed an outage of over 20 hours (missing 3 consecutive 6-hour scheduled triggers between `2026-11-22T20:29:43Z` and `2026-11-23T17:05:03Z`, exceeding `monitor.py`'s `14.0h` threshold).
- **The `ALARM` before recovery (`python src/monitor.py`):**
  ```text
  ALARM last_success   last full success 20.6h ago (2026-11-22T20:29:43Z) > 14.0h limit
  ALARM gaps           1 gap(s) > 14.0h in last 7d (latest: 20.3h between 2026-11-22T20:46:28Z and 2026-11-23T17:05:03Z)
  PASS  completeness   48/48 expected hours present (2026-11-21T18:00:00Z .. 2026-11-23T17:00:00Z)
  ALARM freshness      stale relative to cutoff 2026-11-23T03:05:52Z: flight_state latest=2026-11-22T20:29:53Z; weather_reading stale: loc=1(2026-11-22T20:29:43Z)
  ```
- **Completeness and freshness before and after recovery:**
  Before the recovery run, `weather_reading_clean` held `504` hourly rows last refreshed on `2026-11-22T20:29:43Z` (`48/48` calendar hours existed from the prior 7-day forecast horizon, but all observations were stale relative to `--now`):
  ```text
  BEFORE_MAX_RETRIEVED: ('2026-11-22T20:29:43Z', 504)
  ```
  The recovery run (`python src/run_pipeline.py`, `run_id=09de1b78`, `run_time=2026-11-23T17:24:12Z`) fetched `11,879` live OpenSky states and `192` Open-Meteo hourly rows (`past_days=1` overlap), loaded `20` raw files (`+45,543` net new flight states, `+24` net new weather hours, `elapsed_sec: 2.05s`), rebuilt all cleaned tables (`2.15s`), and passed all `7` `validate.py` checks (`VALIDATION SUCCESS: Pipeline is healthy.`).

  After recovery, `weather_reading_clean` expanded to `528` rows with updated `retrieved_at` across the overlapping 24-hour window, and `monitor.py` returned `PASS` on `last_success`, `completeness`, and `freshness` while continuing to report `ALARM` on the historical `20.3h` gap inside the 7-day lookback window (confirming `gaps` also raises `ALARM` with `Exit code: 1`):
  ```text
  AFTER_MAX_RETRIEVED: ('2026-11-23T17:24:12Z', 528)
  ```
  ```text
  PASS  last_success   last full success 0.0h ago (2026-11-23T17:24:12Z)
  ALARM gaps           1 gap(s) > 14.0h in last 7d (latest: 20.3h between 2026-11-22T20:46:28Z and 2026-11-23T17:05:03Z)
  PASS  completeness   48/48 expected hours present (2026-11-21T18:00:00Z .. 2026-11-23T17:00:00Z)
  PASS  freshness      both feeds fresh within 14.0h (fs=2026-11-23T17:24:11Z, wr=2026-11-23T17:24:12Z)
  Exit code: 1
  ```

### Drills
All drill hooks (`PIPELINE_DRILL_BAD_CRED`, `PIPELINE_DRILL_FAIL_STEP`, `PIPELINE_DRILL_SLEEP_SEC`, `PIPELINE_STEP_TIMEOUT_SEC`) are disabled by default and left unset in the tagged commit. Each failure mode was verified individually, and its outcome was confirmed in `data/pipeline_log.db`:

1. **Credential failure (`PIPELINE_DRILL_BAD_CRED=1`):** Executed `python src/run_pipeline.py` with `PIPELINE_DRILL_BAD_CRED=1` (`run_id=f74eac77`, `run_time=2026-11-22T20:46:28Z`). `src/ingest.py` immediately aborted on `HTTP 401` (`ERROR [OpenSky]: Kimlik doğrulama başarısız (HTTP 401).`), and the runner halted with `PIPELINE FAILED: run_id=f74eac77 stopped at src/ingest.py with exit code 1.` (shell `Exit code: 1`). Downstream steps (`load.py`, `clean.py`, `validate.py`) did not run, leaving only the single failed `src/ingest.py` row for `f74eac77` in `pipeline_run`.

2. **A step that fails (`PIPELINE_DRILL_FAIL_STEP=clean`):** Executed `python src/run_pipeline.py --skip-ingest` with `PIPELINE_DRILL_FAIL_STEP=clean` (`run_id=75518986`, `run_time=2026-11-22T20:26:59Z`). `src/load.py` completed cleanly (`exit_code=0`, `13 raw files`, `rows_added: 0`, `0.87s`), after which `src/clean.py` triggered the intentional fault (`ERROR [clean.py]: Kasıtlı Failure Drill tetiklendi (PIPELINE_DRILL_FAIL_STEP=clean).`) and exited `1`. The runner stopped immediately (`PIPELINE FAILED: run_id=75518986 stopped at src/clean.py with exit code 1.`, shell `Exit code: 1`), `src/validate.py` did not run, `data/aviation.db` remained unchanged, and `pipeline_run` recorded exactly two rows (`src/load.py` `exit_code=0`, `src/clean.py` `exit_code=1`).

3. **A step that hangs (`PIPELINE_DRILL_SLEEP_SEC=10`, `PIPELINE_STEP_TIMEOUT_SEC=3`):** Executed `python src/run_pipeline.py` with a 10-second artificial sleep and a 3-second timeout (`run_id=16b6af25`, `run_time=2026-11-22T20:28:10Z`). The runner terminated `src/ingest.py` after 3.0s (`ERROR [src/ingest.py]: TIMEOUT after 3.0s`), printed `PIPELINE FAILED: run_id=16b6af25 stopped at src/ingest.py with exit code 124 (timeout).` (shell `Exit code: 124`), skipped all downstream steps, and logged `('16b6af25', '2026-11-22T20:28:10Z', 'src/ingest.py', 124, 'TIMEOUT after 3.0s')` in `pipeline_run`.

4. **Two runs at once:** In Terminal 1, launched `python src/run_pipeline.py` with `PIPELINE_DRILL_SLEEP_SEC=15` (`run_id=8f4cce4e`, `run_time=2026-11-22T20:29:43Z`), holding `data/pipeline.lock`. While Terminal 1 was active, Terminal 2 ran `python src/run_pipeline.py` and exited immediately with `PIPELINE ALREADY RUNNING: Lock file exists at C:\Users\Ali\Desktop\Aviation_Delays_Weather_Modeling\data\pipeline.lock. Exiting with code 75.` (shell `Exit code: 75`), executing zero steps and writing zero rows to `pipeline_run`. Terminal 1 subsequently completed all 4 steps cleanly (`11,536` OpenSky states, `192` Open-Meteo rows, `+11,383` net new flight states, `exit_code=0`).

5. **Missed runs:** Deliberately skipped 3 consecutive 6-hour scheduled triggers (~20.3h outage between `2026-11-22T20:46:28Z` and `2026-11-23T17:05:03Z`; detailed in *Missed runs* above). `src/monitor.py` raised `ALARM` on `last_success` (`20.6h > 14.0h`), `gaps` (`20.3h > 14.0h`), and `freshness`. Subsequent recovery run `run_id=09de1b78` (`2026-11-23T17:24:12Z`) healed Open-Meteo via `past_days=1` (`weather_reading_clean` updated from `504` rows at `2026-11-22T20:29:43Z` to `528` rows at `2026-11-23T17:24:12Z`, `48/48` hours complete) and resumed live OpenSky snapshots (`11,879` states), restoring `PASS` on `last_success` and `freshness` while retaining the `gaps` `ALARM` (`Exit code: 1`) across the 7-day audit window.

6. **An old file re-loaded:** Queried the newest `weather_reading` record in `data/aviation.db`, re-ran an offline full reload across all `15` landed raw files (including older snapshots) via `python src/run_pipeline.py --skip-ingest` (`run_id=ffa96158`, `run_time=2026-11-22T20:32:55Z`, `rows_added: 0`, `1.00s`), and re-queried the same record:
   ```text
   BEFORE: ('2026-11-21T00:00:00Z', 14.4, '2026-11-22T20:29:43Z')
   AFTER:  ('2026-11-21T00:00:00Z', 14.4, '2026-11-22T20:29:43Z')
   ```
   The guarded `UPSERT` (`WHERE excluded.retrieved_at > weather_reading.retrieved_at`) preserved the newer retrieval without overwriting it.

#### `pipeline_log.db` Verification for Drills
Excerpt from `SELECT run_id, run_time, step, exit_code, detail FROM pipeline_run ORDER BY started_at DESC LIMIT 12`:

```text
('ffa96158', '2026-11-22T20:32:55Z', 'src/validate.py', 0, 'VALIDATION SUCCESS: Pipeline is healthy.')
('ffa96158', '2026-11-22T20:32:55Z', 'src/clean.py', 0, 'STATUS: COMPLETE')
('ffa96158', '2026-11-22T20:32:55Z', 'src/load.py', 0, 'rows_added:         0 | elapsed_sec:        1.00s')
('8f4cce4e', '2026-11-22T20:29:43Z', 'src/validate.py', 0, 'VALIDATION SUCCESS: Pipeline is healthy.')
('8f4cce4e', '2026-11-22T20:29:43Z', 'src/clean.py', 0, 'STATUS: COMPLETE')
('8f4cce4e', '2026-11-22T20:29:43Z', 'src/load.py', 0, 'rows_added:         11383 | elapsed_sec:        1.10s')
('8f4cce4e', '2026-11-22T20:29:43Z', 'src/ingest.py', 0, 'opensky:         11536 states -> 20261122T202943Z.json | openmeteo:       192 hourly rows (past_days=1) -> 20261122T202943Z.json')
('16b6af25', '2026-11-22T20:28:10Z', 'src/ingest.py', 124, 'TIMEOUT after 3.0s')
('75518986', '2026-11-22T20:26:59Z', 'src/clean.py', 1, 'ERROR [clean.py]: Kasıtlı Failure Drill tetiklendi (PIPELINE_DRILL_FAIL_STEP=clean).')
('75518986', '2026-11-22T20:26:59Z', 'src/load.py', 0, 'rows_added:         0 | elapsed_sec:        0.87s')
```

#### `monitor.py` `ALARM` Verification
To verify that `src/monitor.py` fails loudly and exits non-zero when thresholds are breached, we ran it with a future clock (`python src/monitor.py --now 2026-12-01T12:00:00Z`), triggering `ALARM` on `last_success`, `completeness`, and `freshness` with `Exit code: 1` (`gaps` `ALARM` will be verified during Drill 5 / Missed runs):

```text
ALARM last_success   last full success 207.5h ago (2026-11-22T20:29:43Z) > 14.0h limit
PASS  gaps           no gaps > 14.0h in last 7 days
ALARM completeness   48/48 expected hours missing in weather_reading_clean (e.g. 2026-11-29T13:00:00Z, 2026-11-29T14:00:00Z, 2026-11-29T15:00:00Z)
ALARM freshness      stale relative to cutoff 2026-11-30T22:00:00Z: flight_state latest=2026-11-22T20:29:53Z; weather_reading stale: loc=1(2026-11-22T20:29:43Z)
Exit code: 1
```

## 5. What can still go wrong
- **Permanent loss of OpenSky snapshots during outages:** Because OpenSky `/states/all` has `0h` historical memory, any power loss, laptop sleep, or internet outage spanning a 6-hour trigger permanently loses that global flight snapshot. `monitor.py` raises an `ALARM` once the gap exceeds `14.0h`, and `ingest.py` logs a `WARN` after `2.0h`, but past aircraft positions cannot be backfilled.
- **Single-host scheduler failure:** Both `ops/run_pipeline.bat` and `ops/run_monitor.bat` run on a single Windows workstation (`DESKTOP-IU6TDC9`). If the machine remains powered off or Windows Update halts Task Scheduler, neither the pipeline nor the external monitor executes until a team member inspects the host.
- **Silent upstream unit or semantic drift:** If OpenSky or Open-Meteo changes units (e.g., knots instead of m/s) or returns HTTP `200` with plausible-looking degraded readings inside our configured physical bounds, `STOP` checks will still pass and anomalies will only surface if they breach `validate.py`'s `5%` airborne missingness or `0.10x–10.0x` batch volume thresholds.
- **Outages exceeding Open-Meteo's 7-day automated lookback:** If the host stays offline for more than `OPENMETEO_MAX_PAST_DAYS = 7` days, automated recovery will only heal the most recent 7 days and older weather hours will remain permanently missing unless manually backfilled.
- **System clock skew and long-term full-reload scaling:** When `--run-time` is omitted during scheduled runs, the pipeline relies on the host UTC clock; major clock drift could skew freshness and `past_days` calculations. Additionally, while full reload currently completes in `0.66s` for `9` raw files, multi-month accumulation will eventually push `load.py` toward its `180s` timeout (`exit_code = 124`) unless migrated to a transactional file manifest table.