import os
import sys
import sqlite3
import tempfile
import shutil
import time

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(BASE_DIR)
from src.db import connect
import src.clean_config as cfg

def run_validation(db_path):
    """M3 kurallarına göre 6 adet STOP ve WARN doğrulama testini çalıştırır."""
    con = connect(db_path)
    cur = con.cursor()
    
    stop_failed = False
    
    print("=== M3 DATA QUALITY VALIDATION ===")

    # ---------------------------------------------------------
    # STOP 1: Satır sayısı eşitliği (Row Count Parity)
    # ---------------------------------------------------------
    tables_to_check = ['aircraft', 'flight_state', 'weather_reading']
    parity_ok = True
    details = []
    
    for t in tables_to_check:
        raw_count = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        clean_count = cur.execute(f"SELECT COUNT(*) FROM {t}_clean").fetchone()[0]
        if raw_count != clean_count:
            parity_ok = False
            details.append(f"{t}_clean ({clean_count} != {raw_count})")
            
    if parity_ok:
        print("PASS  Cleaned table row counts equal raw tables")
    else:
        print(f"STOP  Row count mismatch: {', '.join(details)}")
        stop_failed = True

    # ---------------------------------------------------------
    # STOP 2: İmkansız değerlerin temizlenmesi (No Impossible Values)
    # ---------------------------------------------------------
    impossible_count = cur.execute(f"""
        SELECT COUNT(*) FROM flight_state_clean 
        WHERE velocity > {cfg.MAX_VALID_VELOCITY} 
           OR (baro_altitude < 0 AND on_ground = 0)
    """).fetchone()[0]
    
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
    warn_missing = cur.execute(f"""
        SELECT aircraft_key, 
               (SUM(CASE WHEN baro_altitude IS NULL THEN 1 ELSE 0 END) * 1.0 / COUNT(*)) AS missing_share
        FROM flight_state_clean
        GROUP BY aircraft_key
        HAVING missing_share > {cfg.MAX_MISSING_SHARE_WARN}
    """).fetchall()
    
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
    fast_ground = cur.execute(f"""
        SELECT COUNT(*) FROM flight_state_clean 
        WHERE on_ground = 1 AND velocity > {cfg.MAX_GROUND_VELOCITY}
    """).fetchone()[0]
    
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
    """
    M3 Belgesi Kuralı: "A check that has never failed may not work. Make each STOP fail once on purpose."
    """
    db_path = os.path.join(BASE_DIR, "data", "aviation.db")
    if not os.path.exists(db_path):
        print("HATA: DB yok.")
        sys.exit(1)
        
    print("\n\n" + "="*50)
    print("--- INJECTING INTENTIONAL FAILURES FOR M3 REPORT ---")
    print("="*50)
        
    # Windows temp dizini silme sorunlarını aşmak için manuel bir test dizini kullanıyoruz
    test_db_dir = os.path.join(BASE_DIR, "data", "test_tmp")
    os.makedirs(test_db_dir, exist_ok=True)
    fail_db = os.path.join(test_db_dir, "fail.db")
    
    try:
        shutil.copyfile(db_path, fail_db)
        
        # Kısıtlamaları kapatarak hatalı veri sokabilmek için standart sqlite3 kullanıyoruz
        con = sqlite3.connect(fail_db)
        con.execute("PRAGMA foreign_keys = OFF")
        
        # STOP 1 İhlali: Temizlenmiş tablodan 1 satır silerek satır eşitliğini boz
        con.execute("DELETE FROM flight_state_clean WHERE rowid = (SELECT MIN(rowid) FROM flight_state_clean)")
        
        # STOP 2 İhlali: İmkansız değeri clean tabloya göm (Hız = 1500 m/s)
        con.execute("UPDATE flight_state_clean SET velocity = 1500 WHERE rowid = (SELECT MAX(rowid) FROM flight_state_clean)")
        
       # STOP 3 İhlali: PRAGMA foreign_keys = OFF iken var olmayan bir aircraft_key (999999) ata
        con.execute("UPDATE flight_state_clean SET aircraft_key = 999999 WHERE rowid = (SELECT MIN(rowid)+1 FROM flight_state_clean)")
        
        con.commit()
        con.close()
        
        # Dosya kilitlerinin kalkması için kısa bir bekleme
        time.sleep(0.5)
        
        try:
            run_validation(fail_db)
        except SystemExit:
            print("\n>> INTENTIONAL FAIL TEST SUCCESSFUL: Script correctly intercepted the failures and exited with non-zero status! <<")
        else:
            print("\n>> ERROR: Intentional failure test did not stop the script! <<")
            
    finally:
        # Test bittikten sonra test veritabanını temizle
        if os.path.exists(fail_db):
            try:
                os.remove(fail_db)
                os.rmdir(test_db_dir)
            except Exception as e:
                print(f"Uyarı: Test dosyası silinemedi (Windows kilitledi). Önemli değil. Detay: {e}")

if __name__ == "__main__":
    db_main_path = os.path.join(BASE_DIR, "data", "aviation.db")
    
    if len(sys.argv) > 1 and sys.argv[1] == "--test-fails":
        test_intentional_failures()
    else:
        run_validation(db_main_path)