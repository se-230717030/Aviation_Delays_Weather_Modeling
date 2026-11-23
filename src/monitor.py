import os
import sys
import argparse
from datetime import timedelta

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, BASE_DIR)

from src.db import connect, parse_run_time, DEFAULT_DB_PATH, PIPELINE_LOG_DB_PATH

# ============================================================================
# M5 MONITOR EŞİK SABİTLERİ (Her sabit docs/pipeline.md içinde gerekçelendirilir)
# ============================================================================
SCHEDULE_INTERVAL_HOURS = 24.0      # Planlanan çalışma aralığı (6 saatte bir; günde 4 çalıştırma)
LAST_SUCCESS_MAX_HOURS = 48.0      # Son tam başarılı çalışma 2x aralık + 2s tampon (14 saat) sınırını aşarsa ALARM
MAX_GAP_HOURS = 48.0               # Ardışık ingestion çalıştırmaları arasında 14 saatten uzun boşluk varsa ALARM
GAP_LOOKBACK_DAYS = 7              # Boşluk taraması son 7 gün üzerinden yapılır
EXPECTED_FULL_STEPS = 4            # Tam bir zamanlanmış çalıştırmada 4 adım (ingest, load, clean, validate) vardır

COMPLETENESS_HOURS = 48            # Son 48 saatlik saatlik takvimde (weather_reading_clean) eksik saat kontrolü
EXPECTED_LOCATIONS = 1             # Her saat için beklenen konum sayısı (İstanbul Havalimanı)
FRESHNESS_MAX_HOURS = 36.0         # --now saatine göre en güncel veri 14 saatten eskiyse ALARM


def check_last_success(log_con, now_iso):
    """Kontrol 1: Tüm adımları exit_code=0 ile biten son tam çalışmanın üzerinden geçen süre."""
    row = log_con.execute("""
        SELECT MAX(run_time) AS last_success,
               ROUND((julianday(:now) - julianday(MAX(run_time))) * 24.0, 1) AS hours_ago
        FROM (
            SELECT run_id, run_time
            FROM pipeline_run
            GROUP BY run_id
            HAVING COUNT(*) = :expected_steps AND MAX(exit_code) = 0
        )
    """, {"now": now_iso, "expected_steps": EXPECTED_FULL_STEPS}).fetchone()

    last_success = row["last_success"]
    hours_ago = row["hours_ago"]

    if last_success is None or hours_ago is None:
        return "ALARM", "last_success", "No completed 4-step successful run found in pipeline_log.db"
    if hours_ago > LAST_SUCCESS_MAX_HOURS:
        return "ALARM", "last_success", f"last full success {hours_ago}h ago ({last_success}) > {LAST_SUCCESS_MAX_HOURS}h limit"
    return "PASS", "last_success", f"last full success {hours_ago}h ago ({last_success})"


def check_gaps(log_con, now_dt):
    """Kontrol 2: Son N gün içinde ardışık zamanlanmış çalıştırmalar arasında izin verilen eşiği aşan boşluk."""
    since_iso = (now_dt - timedelta(days=GAP_LOOKBACK_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = log_con.execute("""
        WITH runs AS (
            SELECT DISTINCT run_time
            FROM pipeline_run
            WHERE run_time >= :since
              AND step = :ingest_step
        )
        SELECT previous_run, run_time, hours_since
        FROM (
            SELECT LAG(run_time) OVER (ORDER BY run_time) AS previous_run,
                   run_time,
                   ROUND((julianday(run_time) - julianday(LAG(run_time) OVER (ORDER BY run_time))) * 24.0, 1) AS hours_since
            FROM runs
        )
        WHERE hours_since > :max_gap
        ORDER BY run_time DESC
    """, {
        "since": since_iso,
        "ingest_step": "src/ingest.py",
        "max_gap": MAX_GAP_HOURS
    }).fetchall()

    if rows:
        worst = rows[0]
        return (
            "ALARM",
            "gaps",
            f"{len(rows)} gap(s) > {MAX_GAP_HOURS}h in last {GAP_LOOKBACK_DAYS}d "
            f"(latest: {worst['hours_since']}h between {worst['previous_run']} and {worst['run_time']})"
        )
    return "PASS", "gaps", f"no gaps > {MAX_GAP_HOURS}h in last {GAP_LOOKBACK_DAYS} days"


def check_completeness(db_con, now_dt):
    """Kontrol 3: Recursive CTE ile üretilen son 48 saatlik takvimde eksik hava durumu saati var mı?"""
    last_hour_dt = now_dt.replace(minute=0, second=0, microsecond=0)
    first_hour_dt = last_hour_dt - timedelta(hours=COMPLETENESS_HOURS - 1)

    first_period = first_hour_dt.strftime("%Y-%m-%dT%H:00:00Z")
    last_period = last_hour_dt.strftime("%Y-%m-%dT%H:00:00Z")

    missing_rows = db_con.execute("""
        WITH RECURSIVE periods(p) AS (
            SELECT :first_period
            UNION ALL
            SELECT strftime('%Y-%m-%dT%H:00:00Z', p, '+1 hour')
            FROM periods
            WHERE p < :last_period
        )
        SELECT p AS period, COUNT(w.location_key) AS entities
        FROM periods
        LEFT JOIN weather_reading_clean w ON w.observed_at = p
        GROUP BY p
        HAVING COUNT(w.location_key) < :expected_entities
    """, {
        "first_period": first_period,
        "last_period": last_period,
        "expected_entities": EXPECTED_LOCATIONS
    }).fetchall()

    if missing_rows:
        sample = ", ".join(r["period"] for r in missing_rows[:3])
        return (
            "ALARM",
            "completeness",
            f"{len(missing_rows)}/{COMPLETENESS_HOURS} expected hours missing in weather_reading_clean (e.g. {sample})"
        )
    return "PASS", "completeness", f"{COMPLETENESS_HOURS}/{COMPLETENESS_HOURS} expected hours present ({first_period} .. {last_period})"


def check_freshness(db_con, now_dt):
    """Kontrol 4: --now saatine göre her iki kaynaktaki en son verinin tazeliği."""
    cutoff_iso = (now_dt - timedelta(hours=FRESHNESS_MAX_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")

    latest_fs = db_con.execute(
        "SELECT MAX(observed_at) AS latest FROM flight_state_clean WHERE baro_altitude IS NOT NULL"
    ).fetchone()["latest"]

    stale_weather = db_con.execute("""
        SELECT location_key, MAX(retrieved_at) AS latest
        FROM weather_reading_clean
        WHERE temperature_2m IS NOT NULL
        GROUP BY location_key
        HAVING MAX(retrieved_at) < :cutoff
    """, {"cutoff": cutoff_iso}).fetchall()

    latest_wr = db_con.execute(
        "SELECT MAX(retrieved_at) AS latest FROM weather_reading_clean WHERE temperature_2m IS NOT NULL"
    ).fetchone()["latest"]

    issues = []
    if not latest_fs or latest_fs < cutoff_iso:
        issues.append(f"flight_state latest={latest_fs}")
    if not latest_wr:
        issues.append("weather_reading latest=None")
    elif stale_weather:
        stale_str = ", ".join(f"loc={r['location_key']}({r['latest']})" for r in stale_weather)
        issues.append(f"weather_reading stale: {stale_str}")

    if issues:
        return "ALARM", "freshness", f"stale relative to cutoff {cutoff_iso}: {'; '.join(issues)}"
    return "PASS", "freshness", f"both feeds fresh within {FRESHNESS_MAX_HOURS}h (fs={latest_fs}, wr={latest_wr})"


def main():
    parser = argparse.ArgumentParser(description="M5 External Pipeline Monitor (read-only).")
    parser.add_argument("--now", dest="now", default=None, help="Reference UTC clock (ISO 8601, default: now)")
    parser.add_argument("--db", dest="db_path", default=DEFAULT_DB_PATH, help="Path to main database")
    parser.add_argument("--log-db", dest="log_db_path", default=PIPELINE_LOG_DB_PATH, help="Path to pipeline_log.db")
    args = parser.parse_args()

    now_dt, now_iso = parse_run_time(args.now)

    try:
        log_con = connect(args.log_db_path, read_only=True)
        db_con = connect(args.db_path, read_only=True)
    except FileNotFoundError as exc:
        print(f"ALARM monitor_init  {exc}")
        sys.exit(1)

    try:
        results = [
            check_last_success(log_con, now_iso),
            check_gaps(log_con, now_dt),
            check_completeness(db_con, now_dt),
            check_freshness(db_con, now_dt),
        ]
    finally:
        log_con.close()
        db_con.close()

    alarm_count = 0
    for status, name, detail in results:
        print(f"{status:<5} {name:<14} {detail}")
        if status == "ALARM":
            alarm_count += 1

    if alarm_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()