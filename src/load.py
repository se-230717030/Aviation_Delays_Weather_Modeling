import os
import sys
import json
import glob
import time
import argparse
from datetime import datetime, timezone

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, BASE_DIR)

from src.db import connect, parse_run_time

DB_PATH = os.path.join(BASE_DIR, "data", "aviation.db")
SCHEMA_PATH = os.path.join(BASE_DIR, "sql", "schema.sql")


def init_db(con):
    """Veritabanı şemasını baştan oluşturur (CREATE TABLE IF NOT EXISTS ... STRICT)."""
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema_sql = f.read()
    con.executescript(schema_sql)


def unix_to_iso8601(unix_timestamp):
    """Unix timestamp değerini ISO 8601 UTC metnine çevirir."""
    if not unix_timestamp:
        return None
    return datetime.fromtimestamp(unix_timestamp, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def filename_to_iso8601(filename):
    """20261001T103302Z.json dosya adını 2026-10-01T10:33:02Z ISO 8601 UTC formatına çevirir."""
    stem = filename.replace(".json", "")
    try:
        dt = datetime.strptime(stem, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return stem


def quarantine_record(con, target_table, record_data, reason, retrieved_at, summary):
    """Hatalı veya eksik anahtarlı kayıtları çökmeden karantinaya alır (idempotent)."""
    con.execute("""
        INSERT OR IGNORE INTO quarantine (target_table, record_data, reason, retrieved_at)
        VALUES (?, ?, ?, ?)
    """, (target_table, record_data, reason, retrieved_at))
    summary["quarantined"] += 1


def load_opensky_data(con, summary):
    """OpenSky ham JSON dosyalarını okur ve guarded upsert ile yükler."""
    opensky_dir = os.path.join(BASE_DIR, "data", "raw", "opensky")
    if not os.path.exists(opensky_dir):
        return

    files = sorted(glob.glob(os.path.join(opensky_dir, "*.json")))
    for filepath in files:
        summary["files_read"] += 1
        filename = os.path.basename(filepath)
        retrieved_at_iso = filename_to_iso8601(filename)

        with open(filepath, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                quarantine_record(con, "unknown", filepath, "Bozuk JSON formatı", retrieved_at_iso, summary)
                continue

        states = data.get("states", [])
        if not states:
            continue

        for state in states:
            icao24 = str(state[0]).strip() if state[0] else None
            if not icao24:
                quarantine_record(con, "aircraft", json.dumps(state), "Eksik icao24 (Key NULL)", retrieved_at_iso, summary)
                continue

            callsign = str(state[1]).strip() if state[1] else None
            origin_country = str(state[2]).strip() if state[2] else None

            cur = con.execute("SELECT aircraft_key FROM aircraft WHERE icao24 = ?", (icao24,))
            if not cur.fetchone():
                summary["new_aircrafts"] += 1

            # Guarded Upsert: Dosya okuma sırasından bağımsız olarak en yeni retrieved_at kazanır
            con.execute("""
                INSERT INTO aircraft (icao24, callsign, origin_country, retrieved_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(icao24) DO UPDATE SET
                    callsign = excluded.callsign,
                    origin_country = excluded.origin_country,
                    retrieved_at = excluded.retrieved_at
                WHERE excluded.retrieved_at > aircraft.retrieved_at
            """, (icao24, callsign, origin_country, retrieved_at_iso))

            cur = con.execute("SELECT aircraft_key FROM aircraft WHERE icao24 = ?", (icao24,))
            aircraft_key = cur.fetchone()["aircraft_key"]

            time_position = state[3]
            observed_at = unix_to_iso8601(time_position)
            if not observed_at:
                quarantine_record(con, "flight_state", json.dumps(state), "Eksik observed_at", retrieved_at_iso, summary)
                continue

            def clean_numeric(val):
                if isinstance(val, (int, float)) and not isinstance(val, bool):
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
            """, (aircraft_key, observed_at, longitude, latitude, baro_altitude, velocity, on_ground, retrieved_at_iso))

            summary["flight_states_processed"] += 1


def load_openmeteo_data(con, summary):
    """Open-Meteo ham JSON dosyalarını okur ve guarded upsert ile yükler."""
    openmeteo_dir = os.path.join(BASE_DIR, "data", "raw", "openmeteo")
    if not os.path.exists(openmeteo_dir):
        return

    files = sorted(glob.glob(os.path.join(openmeteo_dir, "*.json")))
    for filepath in files:
        summary["files_read"] += 1
        filename = os.path.basename(filepath)
        retrieved_at_iso = filename_to_iso8601(filename)

        with open(filepath, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                quarantine_record(con, "unknown", filepath, "Bozuk JSON formatı", retrieved_at_iso, summary)
                continue

        lat = data.get("latitude")
        lon = data.get("longitude")
        if lat is None or lon is None:
            quarantine_record(con, "location", json.dumps(data), "Eksik lat/lon", retrieved_at_iso, summary)
            continue

        cur = con.execute("SELECT location_key FROM location WHERE latitude = ? AND longitude = ?", (lat, lon))
        if not cur.fetchone():
            summary["new_locations"] += 1

        con.execute("""
            INSERT INTO location (latitude, longitude, retrieved_at)
            VALUES (?, ?, ?)
            ON CONFLICT(latitude, longitude) DO UPDATE SET
                retrieved_at = excluded.retrieved_at
            WHERE excluded.retrieved_at > location.retrieved_at
        """, (lat, lon, retrieved_at_iso))

        cur = con.execute("SELECT location_key FROM location WHERE latitude = ? AND longitude = ?", (lat, lon))
        location_key = cur.fetchone()["location_key"]

        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        temps = hourly.get("temperature_2m", [])
        winds = hourly.get("wind_speed_10m", [])
        precs = hourly.get("precipitation", [])

        for i, t in enumerate(times):
            observed_at = t + "Z" if not t.endswith("Z") else t
            # Dakika formatı HH:MMZ ise standart HH:MM:00Z formatına normalize et
            if len(observed_at) == 17 and observed_at.endswith("Z"):
                observed_at = observed_at[:-1] + ":00Z"

            temp = temps[i] if i < len(temps) else None
            wind = winds[i] if i < len(winds) else None
            prec = precs[i] if i < len(precs) else None

            con.execute("""
                INSERT INTO weather_reading (location_key, observed_at, temperature_2m, wind_speed_10m, precipitation, retrieved_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(location_key, observed_at) DO UPDATE SET
                    temperature_2m = excluded.temperature_2m,
                    wind_speed_10m = excluded.wind_speed_10m,
                    precipitation = excluded.precipitation,
                    retrieved_at = excluded.retrieved_at
                WHERE excluded.retrieved_at > weather_reading.retrieved_at
            """, (location_key, observed_at, temp, wind, prec, retrieved_at_iso))

            summary["weather_readings_processed"] += 1


def run_load(run_time_str=None):
    """Tam yeniden yükleme (full reload) işlemini atomik olarak yürütür; hatada non-zero çıkar."""
    t0 = time.perf_counter()
    _, run_iso = parse_run_time(run_time_str)

    # Opsiyonel Failure Drill Hook'u (varsayılan: kapalı)
    if os.getenv("PIPELINE_DRILL_FAIL_STEP") == "load":
        print("ERROR [load.py]: Kasıtlı Failure Drill tetiklendi (PIPELINE_DRILL_FAIL_STEP=load).", file=sys.stderr)
        sys.exit(1)

    summary = {
        "files_read": 0,
        "new_aircrafts": 0,
        "new_locations": 0,
        "flight_states_processed": 0,
        "weather_readings_processed": 0,
        "values_cleaned": 0,
        "quarantined": 0
    }

    con = connect(DB_PATH, allow_create=True)
    try:
        init_db(con)
        before_fs = con.execute("SELECT COUNT(*) FROM flight_state").fetchone()[0]
        before_wr = con.execute("SELECT COUNT(*) FROM weather_reading").fetchone()[0]

        with con:
            load_opensky_data(con, summary)
            load_openmeteo_data(con, summary)

            fk_violations = con.execute("PRAGMA foreign_key_check").fetchall()
            if fk_violations:
                raise RuntimeError(f"Foreign key check failed: {len(fk_violations)} violation(s)")

        after_fs = con.execute("SELECT COUNT(*) FROM flight_state").fetchone()[0]
        after_wr = con.execute("SELECT COUNT(*) FROM weather_reading").fetchone()[0]
    except Exception as exc:
        con.close()
        print(f"LOAD FAILED (ATOMIC ROLLBACK): {exc}", file=sys.stderr)
        sys.exit(1)

    con.close()
    elapsed = time.perf_counter() - t0
    rows_added = (after_fs - before_fs) + (after_wr - before_wr)

    print(f"run_time:           {run_iso}")
    print(f"files read:         {summary['files_read']} raw files")
    print(f"aircrafts:          {summary['new_aircrafts']} new this run")
    print(f"locations:          {summary['new_locations']} new this run")
    print(f"flight states:      {after_fs} total (+{after_fs - before_fs} net new)")
    print(f"weather readings:   {after_wr} total (+{after_wr - before_wr} net new)")
    print(f"rows_added:         {rows_added}")
    print(f"values cleaned:     {summary['values_cleaned']}")
    print(f"quarantined:        {summary['quarantined']} records")
    print(f"elapsed_sec:        {elapsed:.2f}s")
    print("foreign_key_check:  OK")
    print("STATUS: COMPLETE")


def main():
    parser = argparse.ArgumentParser(description="M2/M5 Idempotent atomic loader.")
    parser.add_argument("--run-time", dest="run_time", default=None, help="Pipeline clock (ISO 8601 UTC)")
    args = parser.parse_args()
    run_load(args.run_time)


if __name__ == "__main__":
    main()