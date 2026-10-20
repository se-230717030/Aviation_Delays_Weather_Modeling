import os
import sys
import csv
import statistics

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(BASE_DIR)

from src.db import connect
import src.clean_config as cfg

DB_PATH = os.path.join(BASE_DIR, "data", "aviation.db")
CLEAN_SCHEMA_PATH = os.path.join(BASE_DIR, "sql", "clean_schema.sql")
COUNTRY_MAP_CSV = os.path.join(BASE_DIR, "data", "mappings", "country_mapping.csv")
IDENTITY_MAP_CSV = os.path.join(BASE_DIR, "data", "mappings", "aircraft_identity.csv")


def init_clean_schema(con):
    """Temizlenmiş katman şemasını oluşturur (M2 tablolarına dokunmaz)."""
    with open(CLEAN_SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema_sql = f.read()
    con.executescript(schema_sql)


def load_mappings(con):
    """
    data/mappings/ altındaki CSV dosyalarını okur ve veritabanındaki
    mapping tablolarına yükler. Eşleşme istatistiklerini döndürür.
    """
    con.execute("DELETE FROM country_mapping")
    con.execute("DELETE FROM aircraft_identity")

    country_map = {}
    with open(COUNTRY_MAP_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw_c = row["raw_country"].strip()
            clean_c = row["clean_country"].strip()
            con.execute("""
                INSERT INTO country_mapping (raw_country, clean_country, decided_by, decided_at, note)
                VALUES (?, ?, ?, ?, ?)
            """, (raw_c, clean_c, row["decided_by"].strip(), row["decided_at"].strip(), row["note"].strip()))
            country_map[raw_c] = clean_c

    # Mevcut uçakların icao24 -> aircraft_key haritasını çıkar
    cur = con.execute("SELECT aircraft_key, icao24 FROM aircraft")
    icao_to_key = {r["icao24"]: r["aircraft_key"] for r in cur.fetchall()}

    identity_map = {}
    unmatched_identities = []
    matched_identities = 0

    with open(IDENTITY_MAP_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            alias_icao = row["alias_icao24"].strip()
            canon_icao = row["canonical_icao24"].strip()
            kind = row["kind"].strip()
            canon_key = icao_to_key.get(canon_icao)

            if alias_icao in icao_to_key and canon_key is not None:
                matched_identities += 1
            else:
                unmatched_identities.append(f"{alias_icao}->{canon_icao}")

            con.execute("""
                INSERT INTO aircraft_identity (alias_icao24, kind, canonical_icao24, canonical_aircraft_key, decided_by, decided_at, note)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (alias_icao, kind, canon_icao, canon_key, row["decided_by"].strip(), row["decided_at"].strip(), row["note"].strip()))

            if canon_key is not None:
                identity_map[alias_icao] = (canon_key, kind)

    # Ülke eşleşmelerini kontrol et
    cur = con.execute("SELECT DISTINCT origin_country FROM aircraft")
    db_countries = {r["origin_country"] for r in cur.fetchall() if r["origin_country"]}
    matched_countries = sum(1 for c in country_map if c in db_countries)
    unmatched_countries = [c for c in country_map if c not in db_countries]

    return {
        "country_map": country_map,
        "identity_map": identity_map,
        "country_total": len(country_map),
        "country_matched": matched_countries,
        "country_unmatched": unmatched_countries,
        "identity_total": len(identity_map) + len(unmatched_identities),
        "identity_matched": matched_identities,
        "identity_unmatched": unmatched_identities
    }


def clean_aircraft_table(con, mapping_info):
    """
    M2 aircraft tablosunu okur, kimlik (identity), çağrı işareti (callsign) ve
    ülke (origin_country) kurallarını uygulayarak aircraft_clean tablosuna yazar.
    """
    con.execute("DELETE FROM aircraft_clean")
    country_map = mapping_info["country_map"]
    identity_map = mapping_info["identity_map"]

    cur = con.execute("SELECT aircraft_key, icao24, callsign, origin_country, retrieved_at FROM aircraft")
    rows = cur.fetchall()

    flag_counts = {
        "identity": {"ok": 0, "aliased_bit_error": 0, "aliased_duplicate_callsign": 0, "aliased_re_registered": 0},
        "callsign": {"ok": 0, "missing_not_reported": 0, "invalid_sentinel": 0},
        "origin_country": {"ok": 0, "encoding_fixed": 0, "standardised_name": 0}
    }

    batch = []
    for r in rows:
        ak = r["aircraft_key"]
        icao24 = r["icao24"]
        cs_raw = r["callsign"]
        oc_raw = r["origin_country"]
        ret_at = r["retrieved_at"]

        # 1. Kural: Kimlik Çözümleme (Identity Resolution)
        if icao24 in identity_map:
            canon_key, kind = identity_map[icao24]
            if kind == "icao24_bit_error":
                id_flag = "aliased_bit_error"
            elif kind == "duplicate_callsign":
                id_flag = "aliased_duplicate_callsign"
            else:
                id_flag = "aliased_re_registered"
        else:
            canon_key = ak
            id_flag = "ok"
        flag_counts["identity"][id_flag] += 1

        # 2. Kural: Callsign Temizliği (Önce eksik kontrolü, sonra sentinel kontrolü)
        if cs_raw is None or cs_raw.strip() == "":
            cs_clean = None
            cs_flag = "missing_not_reported"
        elif cs_raw.strip().upper() in cfg.SENTINEL_CALLSIGNS:
            cs_clean = None
            cs_flag = "invalid_sentinel"
        else:
            cs_clean = cs_raw.strip().upper()
            cs_flag = "ok"
        flag_counts["callsign"][cs_flag] += 1

        # 3. Kural: Origin Country Standartlaştırma
        if oc_raw in country_map:
            oc_clean = country_map[oc_raw]
            if " " in oc_raw and not oc_raw.isascii() or "C te" in oc_raw:
                oc_flag = "encoding_fixed"
            else:
                oc_flag = "standardised_name"
        else:
            oc_clean = oc_raw.strip() if oc_raw else "Unknown"
            oc_flag = "ok"
        flag_counts["origin_country"][oc_flag] += 1

        batch.append((
            ak, icao24, canon_key, id_flag,
            cs_raw, cs_clean, cs_flag,
            oc_raw, oc_clean, oc_flag,
            ret_at
        ))

    con.executemany("""
        INSERT INTO aircraft_clean (
            aircraft_key, icao24, canonical_aircraft_key, identity_flag,
            callsign_raw, callsign, callsign_flag,
            origin_country_raw, origin_country, origin_country_flag,
            retrieved_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, batch)

    return len(rows), len(batch), flag_counts


def clean_flight_state_table(con):
    """
    M2 flight_state tablosunu okur, dokümante edilmiş kural sırasına göre
    değerleri temizler (veya NULL yapar) ve bayraklarıyla flight_state_clean tablosuna yazar.
    """
    con.execute("DELETE FROM flight_state_clean")
    cur = con.execute("""
        SELECT aircraft_key, observed_at, longitude, latitude, baro_altitude, velocity, on_ground, retrieved_at
        FROM flight_state
    """)
    rows = cur.fetchall()

    flag_counts = {
        "longitude": {"ok": 0, "missing_not_reported": 0, "invalid_bounds": 0},
        "latitude": {"ok": 0, "missing_not_reported": 0, "invalid_bounds": 0},
        "baro_altitude": {
            "ok": 0, "missing_on_ground": 0, "missing_sensor_drop": 0,
            "invalid_negative_airborne": 0, "invalid_negative_ground": 0,
            "invalid_ceiling": 0, "cross_field_ground_alt": 0, "unusual_high": 0
        },
        "velocity": {
            "ok": 0, "missing_not_reported": 0, "invalid_negative": 0,
            "invalid_supersonic": 0, "cross_field_ground_fast": 0,
            "cross_field_air_stationary": 0, "unusual_high": 0
        },
        "on_ground": {"ok": 0, "missing_not_reported": 0}
    }

    batch = []
    for r in rows:
        ak = r["aircraft_key"]
        obs = r["observed_at"]
        lon_raw = r["longitude"]
        lat_raw = r["latitude"]
        alt_raw = r["baro_altitude"]
        vel_raw = r["velocity"]
        og_raw = r["on_ground"]
        ret_at = r["retrieved_at"]

        # 1. ON_GROUND Temizliği
        if og_raw is None:
            og_clean = None
            og_flag = "missing_not_reported"
        else:
            og_clean = int(og_raw)
            og_flag = "ok"
        flag_counts["on_ground"][og_flag] += 1

        # 2. LONGITUDE Temizliği
        if lon_raw is None:
            lon_clean = None
            lon_flag = "missing_not_reported"
        elif lon_raw < cfg.MIN_LON or lon_raw > cfg.MAX_LON:
            lon_clean = None
            lon_flag = "invalid_bounds"
        else:
            lon_clean = lon_raw
            lon_flag = "ok"
        flag_counts["longitude"][lon_flag] += 1

        # 3. LATITUDE Temizliği
        if lat_raw is None:
            lat_clean = None
            lat_flag = "missing_not_reported"
        elif lat_raw < cfg.MIN_LAT or lat_raw > cfg.MAX_LAT:
            lat_clean = None
            lat_flag = "invalid_bounds"
        else:
            lat_clean = lat_raw
            lat_flag = "ok"
        flag_counts["latitude"][lat_flag] += 1

        # 4. BARO_ALTITUDE Temizliği (Öncelik sırası: Missing -> Invalid -> Cross-field -> Unusual -> OK)
        if alt_raw is None:
            alt_clean = None
            alt_flag = "missing_on_ground" if og_clean == 1 else "missing_sensor_drop"
        elif og_clean == 0 and alt_raw < cfg.MIN_VALID_ALT_AIRBORNE:
            alt_clean = None
            alt_flag = "invalid_negative_airborne"
        elif og_clean == 1 and alt_raw < cfg.MIN_VALID_ALT_GROUND:
            alt_clean = None
            alt_flag = "invalid_negative_ground"
        elif alt_raw > cfg.MAX_VALID_ALTITUDE:
            alt_clean = None
            alt_flag = "invalid_ceiling"
        elif og_clean == 1 and alt_raw > cfg.MAX_GROUND_ALTITUDE:
            alt_clean = None
            alt_flag = "cross_field_ground_alt"
        elif alt_raw > cfg.UNUSUAL_HIGH_ALTITUDE:
            # Uç değerler (Outliers) SİLİNMEZ/NULL YAPILMAZ, olduğu gibi korunur ve işaretlenir!
            alt_clean = alt_raw
            alt_flag = "unusual_high"
        else:
            alt_clean = alt_raw
            alt_flag = "ok"
        flag_counts["baro_altitude"][alt_flag] += 1

        # 5. VELOCITY Temizliği (Öncelik sırası: Missing -> Invalid -> Cross-field -> Unusual -> OK)
        if vel_raw is None:
            vel_clean = None
            vel_flag = "missing_not_reported"
        elif vel_raw < cfg.MIN_VALID_VELOCITY:
            vel_clean = None
            vel_flag = "invalid_negative"
        elif vel_raw > cfg.MAX_VALID_VELOCITY:
            vel_clean = None
            vel_flag = "invalid_supersonic"
        elif og_clean == 1 and vel_raw > cfg.MAX_GROUND_VELOCITY:
            vel_clean = None
            vel_flag = "cross_field_ground_fast"
        elif og_clean == 0 and alt_clean is not None and alt_clean > 500.0 and vel_raw < cfg.MIN_AIRBORNE_VELOCITY:
            vel_clean = None
            vel_flag = "cross_field_air_stationary"
        elif vel_raw > cfg.UNUSUAL_HIGH_VELOCITY:
            # Uç değerler korunur!
            vel_clean = vel_raw
            vel_flag = "unusual_high"
        else:
            vel_clean = vel_raw
            vel_flag = "ok"
        flag_counts["velocity"][vel_flag] += 1

        batch.append((
            ak, obs,
            lon_raw, lon_clean, lon_flag,
            lat_raw, lat_clean, lat_flag,
            alt_raw, alt_clean, alt_flag,
            vel_raw, vel_clean, vel_flag,
            og_raw, og_clean, og_flag,
            ret_at
        ))

    con.executemany("""
        INSERT INTO flight_state_clean (
            aircraft_key, observed_at,
            longitude_raw, longitude, longitude_flag,
            latitude_raw, latitude, latitude_flag,
            baro_altitude_raw, baro_altitude, baro_altitude_flag,
            velocity_raw, velocity, velocity_flag,
            on_ground_raw, on_ground, on_ground_flag,
            retrieved_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, batch)

    return len(rows), len(batch), flag_counts


def clean_weather_reading_table(con):
    """
    M2 weather_reading tablosunu okur, medyan/MAD (Robust Z-Score) ve
    ardışık tekrar (stuck sensor) analizlerini yaparak weather_reading_clean tablosuna yazar.
    """
    con.execute("DELETE FROM weather_reading_clean")
    cur = con.execute("""
        SELECT location_key, observed_at, temperature_2m, wind_speed_10m, precipitation, retrieved_at
        FROM weather_reading
        ORDER BY location_key, observed_at
    """)
    rows = cur.fetchall()

    # Robust istatistikler (Medyan ve MAD) için geçerli değerleri topla
    temps = [r["temperature_2m"] for r in rows if r["temperature_2m"] is not None]
    winds = [r["wind_speed_10m"] for r in rows if r["wind_speed_10m"] is not None and r["wind_speed_10m"] >= 0]

    temp_med = statistics.median(temps) if temps else 0.0
    temp_mad = statistics.median([abs(x - temp_med) for x in temps]) if temps else 1.0
    wind_med = statistics.median(winds) if winds else 0.0
    wind_mad = statistics.median([abs(x - wind_med) for x in winds]) if winds else 1.0

    def robust_z(val, med, mad):
        if mad == 0:
            return 0.0
        return 0.6745 * (val - med) / mad

    flag_counts = {
        "temperature_2m": {"ok": 0, "missing_not_reported": 0, "invalid_range": 0, "stuck_sensor": 0, "unusual_high": 0, "unusual_low": 0},
        "wind_speed_10m": {"ok": 0, "missing_not_reported": 0, "invalid_negative": 0, "invalid_extreme": 0, "stuck_sensor": 0, "unusual_high": 0},
        "precipitation": {"ok": 0, "missing_not_reported": 0, "invalid_negative": 0, "unusual_high": 0}
    }

    # Ardışık takılı kalma (stuck sensor) tespiti için çalışma uzunluklarını hesapla
    temp_run_len = 1
    wind_run_len = 1
    stuck_temp_indices = set()
    stuck_wind_indices = set()

    for i in range(1, len(rows)):
        if rows[i]["location_key"] == rows[i-1]["location_key"]:
            if rows[i]["temperature_2m"] is not None and rows[i]["temperature_2m"] == rows[i-1]["temperature_2m"]:
                temp_run_len += 1
                if temp_run_len >= cfg.STUCK_SENSOR_RUN:
                    for k in range(i - temp_run_len + 1, i + 1):
                        stuck_temp_indices.add(k)
            else:
                temp_run_len = 1

            if rows[i]["wind_speed_10m"] is not None and rows[i]["wind_speed_10m"] == rows[i-1]["wind_speed_10m"]:
                wind_run_len += 1
                if wind_run_len >= cfg.STUCK_SENSOR_RUN:
                    for k in range(i - wind_run_len + 1, i + 1):
                        stuck_wind_indices.add(k)
            else:
                wind_run_len = 1
        else:
            temp_run_len = 1
            wind_run_len = 1

    batch = []
    for idx, r in enumerate(rows):
        lk = r["location_key"]
        obs = r["observed_at"]
        t_raw = r["temperature_2m"]
        w_raw = r["wind_speed_10m"]
        p_raw = r["precipitation"]
        ret_at = r["retrieved_at"]

        # 1. TEMPERATURE_2M
        if t_raw is None:
            t_clean, t_flag = None, "missing_not_reported"
        elif t_raw < cfg.MIN_VALID_TEMP or t_raw > cfg.MAX_VALID_TEMP:
            t_clean, t_flag = None, "invalid_range"
        elif idx in stuck_temp_indices:
            t_clean, t_flag = None, "stuck_sensor"
        elif robust_z(t_raw, temp_med, temp_mad) > cfg.ROBUST_Z_THRESHOLD:
            t_clean, t_flag = t_raw, "unusual_high"
        elif robust_z(t_raw, temp_med, temp_mad) < -cfg.ROBUST_Z_THRESHOLD:
            t_clean, t_flag = t_raw, "unusual_low"
        else:
            t_clean, t_flag = t_raw, "ok"
        flag_counts["temperature_2m"][t_flag] += 1

        # 2. WIND_SPEED_10M
        if w_raw is None:
            w_clean, w_flag = None, "missing_not_reported"
        elif w_raw < 0.0:
            w_clean, w_flag = None, "invalid_negative"
        elif w_raw > cfg.MAX_VALID_WIND:
            w_clean, w_flag = None, "invalid_extreme"
        elif idx in stuck_wind_indices:
            w_clean, w_flag = None, "stuck_sensor"
        elif robust_z(w_raw, wind_med, wind_mad) > cfg.ROBUST_Z_THRESHOLD:
            w_clean, w_flag = w_raw, "unusual_high"
        else:
            w_clean, w_flag = w_raw, "ok"
        flag_counts["wind_speed_10m"][w_flag] += 1

        # 3. PRECIPITATION
        if p_raw is None:
            p_clean, p_flag = None, "missing_not_reported"
        elif p_raw < 0.0:
            p_clean, p_flag = None, "invalid_negative"
        elif p_raw > 10.0:
            p_clean, p_flag = p_raw, "unusual_high"
        else:
            p_clean, p_flag = p_raw, "ok"
        flag_counts["precipitation"][p_flag] += 1

        batch.append((
            lk, obs,
            t_raw, t_clean, t_flag,
            w_raw, w_clean, w_flag,
            p_raw, p_clean, p_flag,
            ret_at
        ))

    con.executemany("""
        INSERT INTO weather_reading_clean (
            location_key, observed_at,
            temperature_2m_raw, temperature_2m, temperature_2m_flag,
            wind_speed_10m_raw, wind_speed_10m, wind_speed_10m_flag,
            precipitation_raw, precipitation, precipitation_flag,
            retrieved_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, batch)

    return len(rows), len(batch), flag_counts


def print_clean_summary(ac_stats, fs_stats, wr_stats, map_info):
    """M3 Bölüm 5.1 Kural 5'e uygun hizalı özet çıktısı basar."""
    ac_raw, ac_clean, ac_flags = ac_stats
    fs_raw, fs_clean, fs_flags = fs_stats
    wr_raw, wr_clean, wr_flags = wr_stats

    print(f"aircraft_clean: {ac_clean:,} rows (raw: {ac_raw:,})")
    for col, fdict in ac_flags.items():
        flags_str = "  ".join(f"{k}: {v}" for k, v in fdict.items())
        print(f"  {col:<16} -> {flags_str}")

    print(f"\nflight_state_clean: {fs_clean:,} rows (raw: {fs_raw:,})")
    for col, fdict in fs_flags.items():
        flags_str = "  ".join(f"{k}: {v}" for k, v in fdict.items() if v > 0 or k == "ok")
        print(f"  {col:<16} -> {flags_str}")

    print(f"\nweather_reading_clean: {wr_clean:,} rows (raw: {wr_raw:,})")
    for col, fdict in wr_flags.items():
        flags_str = "  ".join(f"{k}: {v}" for k, v in fdict.items() if v > 0 or k == "ok")
        print(f"  {col:<16} -> {flags_str}")

    c_unm = ", ".join(map_info["country_unmatched"]) if map_info["country_unmatched"] else "none"
    i_unm = ", ".join(map_info["identity_unmatched"]) if map_info["identity_unmatched"] else "none"
    print(f"\nmappings: country_mapping {map_info['country_total']} rows (matched {map_info['country_matched']} of {map_info['country_total']}; unmatched: {c_unm})")
    print(f"          aircraft_identity {map_info['identity_total']} rows (matched {map_info['identity_matched']} of {map_info['identity_total']}; unmatched: {i_unm})")
    print("STATUS: COMPLETE")


def run_clean():
    if not os.path.exists(DB_PATH):
        print("HATA: Veritabanı bulunamadı. Önce 'python src/load.py' çalıştırın.", file=sys.stderr)
        sys.exit(1)

    con = connect(DB_PATH)
    try:
        init_clean_schema(con)
        # Bütün temizleme işlemi tek bir atomik transaction (with con:) içinde gerçekleşir
        with con:
            map_info = load_mappings(con)
            ac_stats = clean_aircraft_table(con, map_info)
            fs_stats = clean_flight_state_table(con)
            wr_stats = clean_weather_reading_table(con)
    except Exception as e:
        print(f"CLEAN FAILED (ATOMIC ROLLBACK): {e}", file=sys.stderr)
        con.close()
        sys.exit(1)

    con.close()
    print_clean_summary(ac_stats, fs_stats, wr_stats, map_info)


if __name__ == "__main__":
    run_clean()