-- ============================================================================
-- M3 CLEANED LAYER & MAPPING TABLES SCHEMA
-- Kural: M2 tablolarına dokunulmaz. Tüm tablolar STRICT modundadır.
-- ============================================================================

-- 1. MAPPING TABLE: Ülke İsimleri Standartlaştırma (data/mappings/country_mapping.csv)
CREATE TABLE IF NOT EXISTS country_mapping (
    raw_country TEXT PRIMARY KEY,
    clean_country TEXT NOT NULL,
    decided_by TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    note TEXT NOT NULL
) STRICT;

-- 2. MAPPING TABLE: Uçak Kimlik Çözümleme (data/mappings/aircraft_identity.csv)
CREATE TABLE IF NOT EXISTS aircraft_identity (
    alias_icao24 TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('icao24_bit_error', 'duplicate_callsign', 're_registered_or_overlapping')),
    canonical_icao24 TEXT NOT NULL,
    canonical_aircraft_key INTEGER REFERENCES aircraft(aircraft_key),
    decided_by TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    note TEXT NOT NULL
) STRICT;

-- 3. CLEANED ENTITY TABLE: aircraft_clean
-- Grain: aircraft tablosu ile birebir aynı (aircraft_key başına 1 satır)
CREATE TABLE IF NOT EXISTS aircraft_clean (
    aircraft_key INTEGER PRIMARY KEY REFERENCES aircraft(aircraft_key),
    icao24 TEXT NOT NULL UNIQUE,
    canonical_aircraft_key INTEGER NOT NULL REFERENCES aircraft(aircraft_key),
    identity_flag TEXT NOT NULL CHECK (identity_flag IN (
        'ok', 'aliased_bit_error', 'aliased_duplicate_callsign', 'aliased_re_registered'
    )),
    callsign_raw TEXT,
    callsign TEXT,
    callsign_flag TEXT NOT NULL CHECK (callsign_flag IN (
        'ok', 'missing_not_reported', 'invalid_sentinel'
    )),
    origin_country_raw TEXT,
    origin_country TEXT NOT NULL,
    origin_country_flag TEXT NOT NULL CHECK (origin_country_flag IN (
        'ok', 'encoding_fixed', 'standardised_name'
    )),
    retrieved_at TEXT NOT NULL
) STRICT;

-- 4. CLEANED EVENT TABLE: flight_state_clean
-- Grain: flight_state tablosu ile birebir aynı ((aircraft_key, observed_at) başına 1 satır)
CREATE TABLE IF NOT EXISTS flight_state_clean (
    aircraft_key INTEGER NOT NULL REFERENCES aircraft(aircraft_key),
    observed_at TEXT NOT NULL,
    
    longitude_raw REAL,
    longitude REAL,
    longitude_flag TEXT NOT NULL CHECK (longitude_flag IN (
        'ok', 'missing_not_reported', 'invalid_bounds'
    )),
    
    latitude_raw REAL,
    latitude REAL,
    latitude_flag TEXT NOT NULL CHECK (latitude_flag IN (
        'ok', 'missing_not_reported', 'invalid_bounds'
    )),
    
    baro_altitude_raw REAL,
    baro_altitude REAL,
    baro_altitude_flag TEXT NOT NULL CHECK (baro_altitude_flag IN (
        'ok', 'missing_on_ground', 'missing_sensor_drop', 
        'invalid_negative_airborne', 'invalid_negative_ground', 
        'invalid_ceiling', 'cross_field_ground_alt', 'unusual_high'
    )),
    
    velocity_raw REAL,
    velocity REAL,
    velocity_flag TEXT NOT NULL CHECK (velocity_flag IN (
        'ok', 'missing_not_reported', 'invalid_negative', 
        'invalid_supersonic', 'cross_field_ground_fast', 
        'cross_field_air_stationary', 'unusual_high'
    )),
    
    on_ground_raw INTEGER,
    on_ground INTEGER,
    on_ground_flag TEXT NOT NULL CHECK (on_ground_flag IN (
        'ok', 'missing_not_reported'
    )),
    
    retrieved_at TEXT NOT NULL,
    PRIMARY KEY (aircraft_key, observed_at)
) STRICT;

-- 5. CLEANED EVENT TABLE: weather_reading_clean
-- Grain: weather_reading tablosu ile birebir aynı ((location_key, observed_at) başına 1 satır)
CREATE TABLE IF NOT EXISTS weather_reading_clean (
    location_key INTEGER NOT NULL REFERENCES location(location_key),
    observed_at TEXT NOT NULL,
    
    temperature_2m_raw REAL,
    temperature_2m REAL,
    temperature_2m_flag TEXT NOT NULL CHECK (temperature_2m_flag IN (
        'ok', 'missing_not_reported', 'invalid_range', 'stuck_sensor', 'unusual_high', 'unusual_low'
    )),
    
    wind_speed_10m_raw REAL,
    wind_speed_10m REAL,
    wind_speed_10m_flag TEXT NOT NULL CHECK (wind_speed_10m_flag IN (
        'ok', 'missing_not_reported', 'invalid_negative', 'invalid_extreme', 'stuck_sensor', 'unusual_high'
    )),
    
    precipitation_raw REAL,
    precipitation REAL,
    precipitation_flag TEXT NOT NULL CHECK (precipitation_flag IN (
        'ok', 'missing_not_reported', 'invalid_negative', 'unusual_high'
    )),
    
    retrieved_at TEXT NOT NULL,
    PRIMARY KEY (location_key, observed_at)
) STRICT;

-- 6. ANALYTICAL VIEWS (M3 Bölüm 4.2 & 4.3 Önerisi: Kimlik Çözümlenmiş Görünümler)
CREATE VIEW IF NOT EXISTS flight_state_resolved AS
SELECT 
    ac.canonical_aircraft_key AS resolved_aircraft_key,
    fs.aircraft_key AS original_aircraft_key,
    ac.icao24 AS original_icao24,
    ac.callsign,
    ac.origin_country,
    ac.identity_flag,
    fs.observed_at,
    fs.longitude,
    fs.longitude_flag,
    fs.latitude,
    fs.latitude_flag,
    fs.baro_altitude,
    fs.baro_altitude_flag,
    fs.velocity,
    fs.velocity_flag,
    fs.on_ground,
    fs.retrieved_at
FROM flight_state_clean fs
JOIN aircraft_clean ac ON fs.aircraft_key = ac.aircraft_key;

CREATE VIEW IF NOT EXISTS weather_reading_resolved AS
SELECT 
    wr.location_key,
    l.latitude,
    l.longitude,
    wr.observed_at,
    wr.temperature_2m,
    wr.temperature_2m_flag,
    wr.wind_speed_10m,
    wr.wind_speed_10m_flag,
    wr.precipitation,
    wr.precipitation_flag,
    wr.retrieved_at
FROM weather_reading_clean wr
JOIN location l ON wr.location_key = l.location_key;