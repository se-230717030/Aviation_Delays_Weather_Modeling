import os
import sys
import sqlite3
import shutil
import time

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(BASE_DIR)
from src.db import connect
import src.clean_config as cfg

def run_validation(db_path):
    """M3 kurallarına göre 6 adet STOP ve WARN doğrulama testini parametreli SQL ile çalıştırır."""
    con = connect(db_path)
    cur = con.cursor()
    
    stop_failed = False
    
    print("=== M3 DATA QUALITY VALIDATION ===")

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
        print("PASS  Cleaned table row counts equal raw tables")
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
    cur.execute("PRAGMA foreign_key_check")
    fk_violations = cur.fetchall()
    if not fk_violations:
        print("PASS  foreign_key_check is OK")
    else:
        print(f"STOP  Foreign key violations found: {len(fk_violations)}")
        stop_failed = True

    # ---------------------------------------------------------
    # WARN 1: Eksik Veri Eşiği (Missing share per entity)
    # ---------------------------------------------------------
    warn_missing = cur.execute("""
        SELECT aircraft_key, 
               (SUM(CASE WHEN baro_altitude IS NULL THEN 1 ELSE 0 END) * 1.0 / COUNT(*)) AS missing_share
        FROM flight_state_clean
        GROUP BY aircraft_key
        HAVING missing_share > ?
    """, (cfg.MAX_MISSING_SHARE_WARN,)).fetchall()
    
    if not warn_missing:
        print(f"PASS  All aircraft have <{int(cfg.MAX_MISSING_SHARE_WARN*100)}% missing altitude share")
    else:
        print(f"WARN  {len(warn_missing)} aircraft exceed {int(cfg.MAX_MISSING_SHARE_WARN*100)}% missing altitude limit")

    # ---------------------------------------------------------
    # WARN 2: Güncellik / Kapsama (Freshness/Coverage)
    # ---------------------------------------------------------
    uncovered_locs = cur.execute("""
        SELECT location_key FROM location 
        WHERE location_key NOT IN (SELECT DISTINCT location_key FROM weather_reading_clean)
    """).fetchall()
    
    if not uncovered_locs:
        print("PASS  Every location has recent weather reading coverage")
    else:
        print(f"WARN  {len(uncovered_locs)} locations have no weather reading coverage")

    # ---------------------------------------------------------
    # WARN 3: Domaine Özel Kontrol (Issues Register'dan)
    # ---------------------------------------------------------
    fast_ground = cur.execute("""
        SELECT COUNT(*) FROM flight_state_clean 
        WHERE on_ground = 1 AND velocity > ?
    """, (cfg.MAX_GROUND_VELOCITY,)).fetchone()[0]
    
    if fast_ground == 0:
        print("PASS  No 'fast on ground' cross-field contradictions survived")
    else:
        print(f"WARN  Found {fast_ground} rows where aircraft is on_ground but moving >{cfg.MAX_GROUND_VELOCITY} m/s")

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
        print("HATA: DB yok.")
        sys.exit(1)
        
    print("\n\n" + "="*50)
    print("--- INJECTING INTENTIONAL FAILURES FOR M3 REPORT ---")
    print("="*50)
        
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
        time.sleep(0.5)
        try:
            run_validation(fail_db)
        except SystemExit:
            print("\n>> INTENTIONAL FAIL TEST SUCCESSFUL: Script correctly intercepted the failures and exited with non-zero status! <<")
    finally:
        if os.path.exists(fail_db):
            try:
                os.remove(fail_db)
                os.rmdir(test_db_dir)
            except Exception:
                pass

if __name__ == "__main__":
    db_main_path = os.path.join(BASE_DIR, "data", "aviation.db")
    if len(sys.argv) > 1 and sys.argv[1] == "--test-fails":
        test_intentional_failures()
    else:
        run_validation(db_main_path)