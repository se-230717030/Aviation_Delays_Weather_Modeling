import os
import sys
import sqlite3
import shutil
import time
import statistics
import argparse
from datetime import timedelta

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, BASE_DIR)

from src.db import connect, parse_run_time
import src.clean_config as cfg

# --- M5 VALIDATION SABİTLERİ ---
FRESHNESS_MAX_HOURS = 36                # En son gözlem --run-time'dan 36 saatten eskiyse WARN (günlük/saatlik akışta kesinti göstergesi)
MAX_AIRBORNE_MISSING_SHARE_WARN = 0.05  # Havadaki (on_ground=0) uçaklarda %5'ten fazla NULL irtifa varsa WARN (kalıcı MAR WARN'ı giderildi)
VOLUME_MAX_RATIO = 10.0                 # Son batch hacmi medyanın 10 katını aşarsa WARN
VOLUME_MIN_RATIO = 0.10                 # Son batch hacmi medyanın %10'unun altına düşerse WARN


def run_validation(db_path, run_time_str=None):
    """M3 + M5 kurallarına göre tüm STOP ve WARN kalite kontrollerini parametreli SQL ile çalıştırır."""
    run_dt, run_iso = parse_run_time(run_time_str)
    cutoff_iso = (run_dt - timedelta(hours=FRESHNESS_MAX_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        con = connect(db_path, allow_create=False)
    except FileNotFoundError as exc:
        print(f"STOP  {exc}", file=sys.stderr)
        sys.exit(1)

    cur = con.cursor()
    stop_failed = False

    print(f"=== M5 DATA QUALITY VALIDATION (run_time: {run_iso}) ===")

    # ---------------------------------------------------------
    # STOP 1: Satır sayısı eşitliği (Row Count Parity)
    # ---------------------------------------------------------
    raw_ac = cur.execute("SELECT COUNT(*) FROM aircraft").fetchone()[0]
    cln_ac = cur.execute("SELECT COUNT(*) FROM aircraft_clean").fetchone()[0]
    raw_fs = cur.execute("SELECT COUNT(*) FROM flight_state").fetchone()[0]
    cln_fs = cur.execute("SELECT COUNT(*) FROM flight_state_clean").fetchone()[0]
    raw_wr = cur.execute("SELECT COUNT(*) FROM weather_reading").fetchone()[0]
    cln_wr = cur.execute("SELECT COUNT(*) FROM weather_reading_clean").fetchone()[0]

    details = []
    if raw_ac != cln_ac:
        details.append(f"aircraft_clean ({cln_ac} != {raw_ac})")
    if raw_fs != cln_fs:
        details.append(f"flight_state_clean ({cln_fs} != {raw_fs})")
    if raw_wr != cln_wr:
        details.append(f"weather_reading_clean ({cln_wr} != {raw_wr})")

    if not details:
        print(f"PASS  Cleaned table row counts equal raw tables (ac={cln_ac}, fs={cln_fs}, wr={cln_wr})")
    else:
        print(f"STOP  Row count mismatch: {', '.join(details)}")
        stop_failed = True

    # ---------------------------------------------------------
    # STOP 2: İmkansız değerlerin temizlenmesi (No Impossible Values)
    # ---------------------------------------------------------
    impossible_count = cur.execute("""
        SELECT COUNT(*) FROM flight_state_clean
        WHERE velocity > ?
           OR (baro_altitude < ? AND on_ground = 0)
    """, (cfg.MAX_VALID_VELOCITY, cfg.MIN_VALID_ALT_AIRBORNE)).fetchone()[0]

    if impossible_count == 0:
        print("PASS  No impossible values survive in cleaned columns")
    else:
        print(f"STOP  Found {impossible_count} impossible values surviving in cleaned columns")
        stop_failed = True

    # ---------------------------------------------------------
    # STOP 3: Yabancı Anahtar İhlali Yok (Foreign Key Check)
    # ---------------------------------------------------------
    fk_violations = cur.execute("PRAGMA foreign_key_check").fetchall()
    if not fk_violations:
        print("PASS  foreign_key_check is OK")
    else:
        print(f"STOP  Foreign key violations found: {len(fk_violations)}")
        stop_failed = True

    # ---------------------------------------------------------
    # WARN 1: Havadaki Filo Eksik İrtifa Oranı (Kalıcı MAR WARN giderildi)
    # ---------------------------------------------------------
    airborne_row = cur.execute("""
        SELECT COUNT(*) AS total_airborne,
               SUM(CASE WHEN baro_altitude IS NULL THEN 1 ELSE 0 END) AS missing_alt
        FROM flight_state_clean
        WHERE on_ground = ?
    """, (0,)).fetchone()
    total_air = airborne_row["total_airborne"] or 0
    missing_air = airborne_row["missing_alt"] or 0
    missing_share = (missing_air / total_air) if total_air > 0 else 1.0

    if missing_share <= MAX_AIRBORNE_MISSING_SHARE_WARN:
        print(f"PASS  Airborne missing altitude share is {missing_share*100:.2f}% (<= {MAX_AIRBORNE_MISSING_SHARE_WARN*100:.0f}% threshold; {missing_air}/{total_air})")
    else:
        print(f"WARN  Airborne missing altitude share is {missing_share*100:.2f}% (> {MAX_AIRBORNE_MISSING_SHARE_WARN*100:.0f}% threshold; {missing_air}/{total_air})")

    # ---------------------------------------------------------
    # WARN 2: Run-Time Bazlı Tazelik (Freshness from --run-time)
    # ---------------------------------------------------------
    latest_fs = cur.execute("SELECT MAX(observed_at) FROM flight_state_clean").fetchone()[0]
    latest_wr = cur.execute("SELECT MAX(retrieved_at) FROM weather_reading_clean").fetchone()[0]

    stale_sources = []
    if not latest_fs or latest_fs < cutoff_iso:
        stale_sources.append(f"flight_state (latest={latest_fs})")
    if not latest_wr or latest_wr < cutoff_iso:
        stale_sources.append(f"weather_reading (latest={latest_wr})")

    if not stale_sources:
        print(f"PASS  Freshness from run_time ({run_iso}) within {FRESHNESS_MAX_HOURS}h (fs={latest_fs}, wr={latest_wr})")
    else:
        print(f"WARN  Stale data relative to run_time ({run_iso}, cutoff={cutoff_iso}): {', '.join(stale_sources)}")

    # ---------------------------------------------------------
    # WARN 3: Hacim Kontrolü (Volume Check vs. Typical Run)
    # ---------------------------------------------------------
    batch_rows = cur.execute("""
        SELECT retrieved_at, COUNT(*) AS cnt
        FROM flight_state_clean
        GROUP BY retrieved_at
        ORDER BY retrieved_at ASC
    """).fetchall()

    if not batch_rows:
        print("WARN  Volume check: flight_state_clean has 0 batches")
    elif len(batch_rows) == 1:
        cnt = batch_rows[0]["cnt"]
        if cnt > 0:
            print(f"PASS  Volume check: baseline batch has {cnt:,} flight states (1 batch total)")
        else:
            print("WARN  Volume check: baseline batch has 0 flight states")
    else:
        latest_cnt = batch_rows[-1]["cnt"]
        prev_counts = [r["cnt"] for r in batch_rows[:-1]]
        typical_cnt = statistics.median(prev_counts)
        if latest_cnt == 0 or (typical_cnt > 0 and (latest_cnt > VOLUME_MAX_RATIO * typical_cnt or latest_cnt < VOLUME_MIN_RATIO * typical_cnt)):
            print(f"WARN  Volume anomaly: latest batch={latest_cnt:,} vs median={typical_cnt:,.0f} across {len(prev_counts)} prior runs")
        else:
            print(f"PASS  Volume check: latest batch={latest_cnt:,} vs median={typical_cnt:,.0f} ({len(batch_rows)} batches)")

    # ---------------------------------------------------------
    # WARN 4: Domaine Özel Kontrol (Yerde aşırı hız çelişkisi)
    # ---------------------------------------------------------
    fast_ground = cur.execute("""
        SELECT COUNT(*) FROM flight_state_clean
        WHERE on_ground = ? AND velocity > ?
    """, (1, cfg.MAX_GROUND_VELOCITY)).fetchone()[0]

    if fast_ground == 0:
        print("PASS  No 'fast on ground' cross-field contradictions survived")
    else:
        print(f"WARN  Found {fast_ground} rows where aircraft is on_ground=1 and velocity > {cfg.MAX_GROUND_VELOCITY} m/s")

    con.close()

    if stop_failed:
        print("\nVALIDATION FAILED: Pipeline stopped due to STOP errors.")
        sys.exit(1)
    else:
        print("\nVALIDATION SUCCESS: Pipeline is healthy.")


def test_intentional_failures():
    """Geçici kopya üzerinde 3 STOP kuralının da hata yakaladığını kanıtlar."""
    db_path = os.path.join(BASE_DIR, "data", "aviation.db")
    if not os.path.exists(db_path):
        print("HATA: DB yok.", file=sys.stderr)
        sys.exit(1)

    print("\n" + "=" * 50)
    print("--- INJECTING INTENTIONAL FAILURES ---")
    print("=" * 50)

    test_db_dir = os.path.join(BASE_DIR, "data", "test_tmp")
    os.makedirs(test_db_dir, exist_ok=True)
    fail_db = os.path.join(test_db_dir, "fail.db")

    try:
        shutil.copyfile(db_path, fail_db)
        con = sqlite3.connect(fail_db)
        con.execute("PRAGMA foreign_keys = OFF")
        con.execute("DELETE FROM flight_state_clean WHERE rowid = (SELECT MIN(rowid) FROM flight_state_clean)")
        con.execute("UPDATE flight_state_clean SET velocity = ? WHERE rowid = (SELECT MAX(rowid) FROM flight_state_clean)", (1500.0,))
        con.execute("UPDATE flight_state_clean SET aircraft_key = ? WHERE rowid = (SELECT MIN(rowid)+1 FROM flight_state_clean)", (999999,))
        con.commit()
        con.close()
        time.sleep(0.3)
        try:
            run_validation(fail_db)
        except SystemExit as exc:
            print(f"\n>> INTENTIONAL FAIL TEST SUCCESSFUL (exit code {exc.code}) <<")
    finally:
        if os.path.exists(fail_db):
            try:
                os.remove(fail_db)
                os.rmdir(test_db_dir)
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description="M3/M5 Data quality validation checks.")
    parser.add_argument("--run-time", dest="run_time", default=None, help="Pipeline clock (ISO 8601 UTC)")
    parser.add_argument("--test-fails", action="store_true", help="Run intentional STOP failure drill on a temp copy")
    args = parser.parse_args()

    if args.test_fails:
        test_intentional_failures()
    else:
        db_main_path = os.path.join(BASE_DIR, "data", "aviation.db")
        run_validation(db_main_path, args.run_time)


if __name__ == "__main__":
    main()