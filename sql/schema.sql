-- 1. AIRCRAFT (Entity)
-- Uçakların kimlik bilgilerini tutar. icao24 asıl ID'dir.
CREATE TABLE IF NOT EXISTS aircraft (
    aircraft_key INTEGER PRIMARY KEY,
    icao24 TEXT NOT NULL UNIQUE,
    callsign TEXT,
    origin_country TEXT,
    retrieved_at TEXT NOT NULL
) STRICT;

-- 2. FLIGHT_STATE (Event)
-- Uçakların belirli bir andaki hız, konum, yükseklik durumunu tutar.
CREATE TABLE IF NOT EXISTS flight_state (
    aircraft_key INTEGER NOT NULL,
    observed_at TEXT NOT NULL, -- ISO 8601 UTC formatında
    longitude REAL,
    latitude REAL,
    baro_altitude REAL,
    velocity REAL,
    on_ground INTEGER, -- SQLite STRICT modda boolean yerine 0/1 (INTEGER) kullanılır
    retrieved_at TEXT NOT NULL,
    PRIMARY KEY (aircraft_key, observed_at),
    FOREIGN KEY (aircraft_key) REFERENCES aircraft(aircraft_key)
) STRICT;

-- 3. LOCATION (Entity)
-- Hava durumu çekilen enlem ve boylam lokasyonlarını tutar.
CREATE TABLE IF NOT EXISTS location (
    location_key INTEGER PRIMARY KEY,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    retrieved_at TEXT NOT NULL,
    UNIQUE(latitude, longitude)
) STRICT;

-- 4. WEATHER_READING (Event)
-- Belirli bir lokasyondaki saatlik hava durumu verilerini tutar.
CREATE TABLE IF NOT EXISTS weather_reading (
    location_key INTEGER NOT NULL,
    observed_at TEXT NOT NULL, -- ISO 8601 UTC formatında
    temperature_2m REAL,
    wind_speed_10m REAL,
    precipitation REAL,
    retrieved_at TEXT NOT NULL,
    PRIMARY KEY (location_key, observed_at),
    FOREIGN KEY (location_key) REFERENCES location(location_key)
) STRICT;

-- 5. QUARANTINE
-- Referansı bulunamayan (orphan event) veya parse edilemeyen bozuk verilerin çökme yapmadan saklanacağı tablo.
CREATE TABLE IF NOT EXISTS quarantine (
    quarantine_key INTEGER PRIMARY KEY,
    target_table TEXT NOT NULL,
    record_data TEXT NOT NULL, 
    reason TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    UNIQUE(target_table, record_data, retrieved_at) -- Aynı hatanın kopyalanmasını önler
) STRICT;