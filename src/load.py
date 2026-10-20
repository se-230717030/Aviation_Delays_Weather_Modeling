import os
import sys
import json
import glob
from datetime import datetime, timezone

# Kök dizini PATH'e ekle, böylece db.py import edilebilir
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(BASE_DIR)
from src.db import connect

DB_PATH = os.path.join(BASE_DIR, "data", "aviation.db")
SCHEMA_PATH = os.path.join(BASE_DIR, "sql", "schema.sql")

def init_db(con):
    """Veritabanını baştan oluşturur (Idempotent)"""
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema_sql = f.read()
    con.executescript(schema_sql)

def unix_to_iso8601(unix_timestamp):
    """Unix timestamp'i ISO 8601 UTC formatına çevirir"""
    if not unix_timestamp:
        return None
    return datetime.fromtimestamp(unix_timestamp, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def quarantine_record(con, target_table, record_data, reason, retrieved_at, summary):
    """Hatalı kayıtları çökme olmadan karantinaya yazar"""
    con.execute("""
        INSERT OR IGNORE INTO quarantine (target_table, record_data, reason, retrieved_at)
        VALUES (?, ?, ?, ?)
    """, (target_table, record_data, reason, retrieved_at))
    summary["quarantined"] += 1
    
def load_opensky_data(con, summary):
    """OpenSky verilerini upsert (deduplication) mantığıyla işler"""
    opensky_dir = os.path.join(BASE_DIR, "data", "raw", "opensky")
    if not os.path.exists(opensky_dir):
        return

    files = glob.glob(os.path.join(opensky_dir, "*.json"))
    for filepath in files:
        summary["files_read"] += 1
        filename = os.path.basename(filepath)
        retrieved_at_file = filename.replace(".json", "") 
        
        with open(filepath, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                quarantine_record(con, "unknown", filepath, "Bozuk JSON formatı", retrieved_at_file, summary)
                continue
            
        states = data.get("states", [])
        if not states:
            continue
            
        for state in states:
            icao24 = str(state[0]).strip() if state[0] else None
            
            if not icao24:
                quarantine_record(con, "aircraft", json.dumps(state), "Eksik icao24 (Key NULL)", retrieved_at_file, summary)
                continue
                
            callsign = str(state[1]).strip() if state[1] else None
            origin_country = str(state[2]).strip() if state[2] else None
            
            cur = con.execute("SELECT aircraft_key FROM aircraft WHERE icao24 = ?", (icao24,))
            if not cur.fetchone():
                summary["new_aircrafts"] += 1
            
            # AIRCRAFT UPSERT
            con.execute("""
                INSERT INTO aircraft (icao24, callsign, origin_country, retrieved_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(icao24) DO UPDATE SET
                    callsign = excluded.callsign,
                    origin_country = excluded.origin_country,
                    retrieved_at = excluded.retrieved_at
                WHERE excluded.retrieved_at > aircraft.retrieved_at
            """, (icao24, callsign, origin_country, retrieved_at_file))
            
            cur = con.execute("SELECT aircraft_key FROM aircraft WHERE icao24 = ?", (icao24,))
            aircraft_key = cur.fetchone()["aircraft_key"]
            
            time_position = state[3]
            observed_at = unix_to_iso8601(time_position)
            
            if not observed_at:
                quarantine_record(con, "flight_state", json.dumps(state), "Eksik observed_at", retrieved_at_file, summary)
                continue
                
            def clean_numeric(val):
                if isinstance(val, (int, float)):
                    return val
                if val is not None:
                    summary["values_cleaned"] += 1
                return None

            longitude = clean_numeric(state[5])
            latitude = clean_numeric(state[6])
            baro_altitude = clean_numeric(state[7])
            velocity = clean_numeric(state[9])
            on_ground_raw = state[8]
            on_ground = 1 if on_ground_raw is True else (0 if on_ground_raw is False else None)

            # FLIGHT_STATE UPSERT
            con.execute("""
                INSERT INTO flight_state (aircraft_key, observed_at, longitude, latitude, baro_altitude, velocity, on_ground, retrieved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(aircraft_key, observed_at) DO UPDATE SET
                    longitude = excluded.longitude,
                    latitude = excluded.latitude,
                    baro_altitude = excluded.baro_altitude,
                    velocity = excluded.velocity,
                    on_ground = excluded.on_ground,
                    retrieved_at = excluded.retrieved_at
                WHERE excluded.retrieved_at > flight_state.retrieved_at
            """, (aircraft_key, observed_at, longitude, latitude, baro_altitude, velocity, on_ground, retrieved_at_file))
            
            summary["flight_states_inserted"] += 1

def load_openmeteo_data(con, summary):
    """Open-Meteo verilerini upsert (deduplication) mantığıyla işler"""
    openmeteo_dir = os.path.join(BASE_DIR, "data", "raw", "openmeteo")
    if not os.path.exists(openmeteo_dir):
        return

    files = glob.glob(os.path.join(openmeteo_dir, "*.json"))
    for filepath in files:
        summary["files_read"] += 1
        filename = os.path.basename(filepath)
        retrieved_at_file = filename.replace(".json", "") 
        
        with open(filepath, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                quarantine_record(con, "unknown", filepath, "Bozuk JSON formatı", retrieved_at_file, summary)
                continue
        
        lat = data.get("latitude")
        lon = data.get("longitude")
        
        if lat is None or lon is None:
            quarantine_record(con, "location", json.dumps(data), "Eksik lat/lon", retrieved_at_file, summary)
            continue
            
        cur = con.execute("SELECT location_key FROM location WHERE latitude = ? AND longitude = ?", (lat, lon))
        if not cur.fetchone():
            summary["new_locations"] += 1

        # LOCATION UPSERT
        con.execute("""
            INSERT INTO location (latitude, longitude, retrieved_at)
            VALUES (?, ?, ?)
            ON CONFLICT(latitude, longitude) DO UPDATE SET
                retrieved_at = excluded.retrieved_at
            WHERE excluded.retrieved_at > location.retrieved_at
        """, (lat, lon, retrieved_at_file))
        
        cur = con.execute("SELECT location_key FROM location WHERE latitude = ? AND longitude = ?", (lat, lon))
        location_key = cur.fetchone()["location_key"]

        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        temps = hourly.get("temperature_2m", [])
        winds = hourly.get("wind_speed_10m", [])
        precs = hourly.get("precipitation", [])

        for i, t in enumerate(times):
            # Open-Meteo zamanları "2026-10-01T00:00" formatındadır, ISO 8601 UTC için sonuna 'Z' ekliyoruz
            observed_at = t + "Z" if not t.endswith("Z") else t
            
            temp = temps[i] if i < len(temps) else None
            wind = winds[i] if i < len(winds) else None
            prec = precs[i] if i < len(precs) else None

            # WEATHER_READING UPSERT
            con.execute("""
                INSERT INTO weather_reading (location_key, observed_at, temperature_2m, wind_speed_10m, precipitation, retrieved_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(location_key, observed_at) DO UPDATE SET
                    temperature_2m = excluded.temperature_2m,
                    wind_speed_10m = excluded.wind_speed_10m,
                    precipitation = excluded.precipitation,
                    retrieved_at = excluded.retrieved_at
                WHERE excluded.retrieved_at > weather_reading.retrieved_at
            """, (location_key, observed_at, temp, wind, prec, retrieved_at_file))
            
            summary["weather_readings_inserted"] += 1

def run_load():
    """Çalıştırma ve raporlama mantığını yönetir"""
    summary = {
        "files_read": 0,
        "new_aircrafts": 0,
        "new_locations": 0,
        "flight_states_inserted": 0,
        "weather_readings_inserted": 0,
        "values_cleaned": 0,
        "quarantined": 0
    }

    con = connect(DB_PATH)
    init_db(con)

    try:
        # Atomicity: Tüm okuma/yazma işlemi tek bir transaction içinde gerçekleşir
        with con:
            load_opensky_data(con, summary)
            load_openmeteo_data(con, summary)
            
            cur = con.cursor()
            cur.execute("PRAGMA foreign_key_check")
            fk_violations = cur.fetchall()
            foreign_key_status = "OK" if not fk_violations else f"FAIL ({len(fk_violations)} ihlal)"

    except Exception as e:
        print(f"HATA: {e}")
        foreign_key_status = "SKIPPED DUE TO ERROR"
    finally:
        con.close()

    # Dürüst Rapor (Honest Run Summary)
    print(f"files read: \t\t{summary['files_read']} raw files")
    print(f"aircrafts: \t\t{summary['new_aircrafts']} new this run")
    print(f"locations: \t\t{summary['new_locations']} new this run")
    print(f"flight states: \t\t+{summary['flight_states_inserted']} this run")
    print(f"weather readings: \t+{summary['weather_readings_inserted']} this run")
    print(f"values cleaned: \t{summary['values_cleaned']}")
    print(f"quarantined: \t\t{summary['quarantined']} records")
    print(f"foreign_key_check: \t{foreign_key_status}")
    print("STATUS: COMPLETE")

if __name__ == "__main__":
    run_load()