import shutil
import sqlite3
import tempfile
import pathlib
import os
import sys

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(BASE_DIR)
from src.db import connect

def counts(con):
    """Tablolardaki satır sayılarını döndürür"""
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}

def run_checks():
    db_path = os.path.join(BASE_DIR, "data", "aviation.db")
    if not os.path.exists(db_path):
        print("Lütfen önce python src/load.py komutunu çalıştırarak veritabanını oluşturun.")
        return

    with tempfile.TemporaryDirectory() as tmp:
        check_db = pathlib.Path(tmp) / "check.db"
        shutil.copyfile(db_path, check_db)
        
        # Test 5: Idempotence
        # Yükleme işleminin iki kez çalıştırıldığında satır sayılarının değişmemesini test eder
        con = connect(str(check_db))
        before_counts = counts(con)
        con.close()
        
        # İkinci yüklemeyi simüle et (load.py içerisindeki fonksiyonu geçici db yoluyla çağır)
        import src.load
        src.load.DB_PATH = str(check_db)
        # Çıktıları gizlemek için sessiz yükleme çalıştırıyoruz
        sys.stdout = open(os.devnull, 'w')
        src.load.run_load()
        sys.stdout = sys.__stdout__
        
        con = connect(str(check_db))
        after_counts = counts(con)
        print("Idempotence:\t\t", "PASS" if before_counts == after_counts else f"FAIL {before_counts} -> {after_counts}")
        
        # Test 1: Duplicate natural key
        try:
            con.execute("INSERT INTO aircraft (icao24, retrieved_at) VALUES ('duplicate_test_id', 'test')")
            con.execute("INSERT INTO aircraft (icao24, retrieved_at) VALUES ('duplicate_test_id', 'test2')")
            print("Duplicate natural key:\t FAIL (Kayıt reddedilmeliydi)")
        except sqlite3.IntegrityError:
            con.rollback() # SIZINTIYI ÖNLER
            print("Duplicate natural key:\t PASS")

        # Test 2: Orphan event
        try:
            con.execute("INSERT INTO flight_state (aircraft_key, observed_at, retrieved_at) VALUES (999999, '2026-10-13T12:00:00Z', 'test')")
            print("Orphan event:\t\t FAIL (Kayıt reddedilmeliydi)")
        except sqlite3.IntegrityError:
            con.rollback() # SIZINTIYI ÖNLER
            print("Orphan event:\t\t PASS")

        # Test 3: NULL key
        try:
            con.execute("INSERT INTO flight_state (aircraft_key, observed_at, retrieved_at) VALUES (1, NULL, 'test')")
            print("NULL key:\t\t FAIL (Kayıt reddedilmeliydi)")
        except sqlite3.IntegrityError:
            con.rollback() # SIZINTIYI ÖNLER
            print("NULL key:\t\t PASS")

        # Test 4: Wrong type
        try:
            con.execute("INSERT INTO flight_state (aircraft_key, observed_at, velocity, retrieved_at) VALUES (1, '2026-10-13T12:00:00Z', 'yanlış_metin', 'test')")
            print("Wrong type:\t\t FAIL (Kayıt reddedilmeliydi - STRICT mod çalışmıyor)")
        except sqlite3.IntegrityError:
            con.rollback() # SIZINTIYI ÖNLER
            print("Wrong type:\t\t PASS")

        # Test 6: Atomicity
        atom_before = counts(con)
        try:
            with con:
                con.execute("INSERT INTO aircraft (icao24, retrieved_at) VALUES ('atom_test_id', 'test')")
                con.execute("INSERT INTO flight_state (aircraft_key, observed_at) VALUES (NULL, 'bad')") # Hata verecek komut
        except sqlite3.IntegrityError:
            pass
            
        atom_after = counts(con)
        print("Atomicity:\t\t", "PASS" if atom_before == atom_after else f"FAIL {atom_before} -> {atom_after}")
        
        con.close()

if __name__ == "__main__":
    run_checks()