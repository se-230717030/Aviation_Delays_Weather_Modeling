# Cleaning Report — Aviation Delays & Weather Modeling

## 1. Summary
This report documents the data profiling, anomaly investigation, and cleaning architecture we carried out on our M2 database (`data/aviation.db`), which holds live flight state vectors from the **OpenSky Network API** (`aircraft`: 8,104 rows; `flight_state`: 8,026 rows; `quarantine`: 78 rows) and hourly meteorological forecasts from the **Open-Meteo API** (`location`: 1 row; `weather_reading`: 168 rows). Across the six data quality families we examined, our most important findings were: **820 missing barometric altitude readings** caused by transponder behavior when aircraft are on the ground (`on_ground = 1`) (823 missing altitude records in total, MAR mechanism); **37 negative altitude readings while airborne** caused by high local atmospheric pressure exceeding the standard QNE pressure (1013.25 hPa) (45 negative records in total, including those on the ground); **3 impossible supersonic speed spikes** (up to `2,160.22 m/s`); **13 cross-field contradictions** reporting a speed below stall speed (`< 15 m/s`) while airborne (`> 500 m` altitude); **5 sentinel (fake) callsigns** (`00000000`, `0000000`, `UNIDENT`, `TEST`); and **5 transponder identity collisions**, including 1-bit hexadecimal corruptions. The cleaned layer we built (`aircraft_clean`, `flight_state_clean`, `weather_reading_clean`) preserves the grain, primary keys, and row counts of the M2 tables exactly, without deleting any rows or imputing missing values, and keeps `<column>_raw`, `<column>`, and `<column>_flag` columns for every cleanable attribute. All country name standardizations (`country_mapping.csv`) and identity resolutions (`aircraft_identity.csv`) are stored as version-controlled data files and exposed through analytical views (`flight_state_resolved`, `weather_reading_resolved`). Despite these corrections, we cannot compute true departure/arrival delay times from a single snapshot of data, and we cannot determine the operational category of the 184 aircraft that report no callsign until multiple time-series snapshots are collected in M4/M5 . *(M2 Correction Note: Before the M3 profiling stage, to ensure full idempotence across repeated runs, a `UNIQUE(target_table, record_data, retrieved_at)` constraint was added to the `quarantine` table in `sql/schema.sql`, and explicit `con.rollback()` calls were added to the integrity tests in `src/checks.py`).*

---

## 2. Profile Highlights
All of our profiling queries, distribution analyses, and sequence checks are recorded, together with their run outputs, in [`notebooks/m3_profile.ipynb`](../notebooks/m3_profile.ipynb).

### 2.1 Table Coverage & Row Counts
| Table | M2 Raw Row Count | M3 Cleaned Row Count | Primary Key / Grain | Entity Coverage |
| :--- | :--- | :--- | :--- | :--- |
| `aircraft` | 8,104 | 8,104 | `aircraft_key` (1 row per `icao24` transponder) | 8,104 raw / 8,099 resolved unique aircraft |
| `flight_state` | 8,026 | 8,026 | `(aircraft_key, observed_at)` (1 row per aircraft-second) | 8,026 flight states with valid timestamps |
| `location` | 1 | *(Unchanged M2 entity table)* | `location_key` (1 row per coordinate pair: `41.2753, 28.7519`) | 1 (Istanbul Airport grid point) |
| `weather_reading` | 168 | 168 | `(location_key, observed_at)` (1 row per location-hour) | 1 location × 168 hours (7-day series) |
| `quarantine` | 78 | *(Unchanged M2 table)* | `quarantine_key` (raw records with missing keys / that could not be parsed) | 78 records with missing `observed_at` |

### 2.2 Summary Statistics & Robust Spread
Because parametric statistics such as the mean and standard deviation are directly affected by extreme sensor errors (such as the `2,160.22 m/s` speed spike), we examined our numeric columns in `notebooks/m3_profile.ipynb` (Cell 6) using the **median**, **Median Absolute Deviation (MAD)**, and **Interquartile Range (IQR)**:

| Table.Column | Valid Rows | Missing (`NULL`) | Missing % | Min | Q25 | Median | Q75 | Max | IQR | MAD |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `flight_state.longitude` | 8,026 | 0 | 0.00% | -172.90 | — | 7.91 | — | 179.69 | 36.86 | 18.69 |
| `flight_state.latitude` | 8,026 | 0 | 0.00% | -45.12 | — | 41.29 | — | 69.98 | 14.10 | 7.05 |
| `flight_state.baro_altitude` (m) | 7,203 | 823 | 10.25% | -152.40 | — | 9,144.00 | — | 20,604.48 | 7,924.80 | 2,438.40 |
| `flight_state.velocity` (m/s) | 8,025 | 1 | 0.01% | 0.00 | — | 208.18 | — | 2,160.22 | 141.92 | 38.28 |
| `weather_reading.temperature_2m` (°C) | 168 | 0 | 0.00% | 15.40 | 16.40 | 17.30 | 18.50 | 20.40 | 2.10 | — |
| `weather_reading.wind_speed_10m` | 168 | 0 | 0.00% | 4.30 | 12.30 | 16.70 | 19.32 | 25.00 | 7.02 | — |
| `weather_reading.precipitation` (mm) | 168 | 0 | 0.00% | 0.00 | 0.00 | 0.00 | 0.10 | 2.00 | 0.10 | — |

---

## 3. Issues Register
All issues we identified across the six data quality families during the profiling stage are listed below, together with their evidence, cause classifications (`confirmed` / `inferred` / `unknown`), the cleaning decisions we made, and the team members who investigated them:

| ID | Family | Table.Column | What (Issue Description) | Where / When / How Many | Evidence | Cause (`confirmed` / `inferred` / `unknown`) | Decision | Flag | Investigated by |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **I-01** | 1. Missing values | `flight_state.baro_altitude` | `NULL` barometric altitude while the aircraft is on the ground | `on_ground = 1`; 820 of 849 total ground records (96.58%) | `m3_profile.ipynb` Cell 4 cross-tabulation: 820 of the 849 ground records are `NULL`, while only 3 of the 7,177 airborne records are `NULL` | **Inferred:** After landing, ADS-B Mode-S transponders switch to surface mode (surface squitter) and stop transmitting barometric altitude | Leave as `NULL` (`0 m` is not assigned because airport elevations differ); flag the mechanism | `missing_on_ground` | Muhammet Ali Ozturk |
| **I-02** | 1. Missing values | `flight_state.baro_altitude` | `NULL` barometric altitude while the aircraft is airborne | `on_ground = 0`; 3 rows (0.04% of 7,177 total airborne records) | `m3_profile.ipynb` Cell 4: `Missing_Altitude = 3.0` in the `on_ground = 0` group | **Inferred:** Momentary barometric altimeter/encoder data dropout while the GNSS position broadcast continues | Leave as `NULL`; flag with a separate flag from the on-ground missingness | `missing_sensor_drop` | Muhammet Ali Ozturk |
| **I-03** | 1. Missing values | `aircraft.callsign` | Empty string `""` or `NULL` callsign | 184 of 8,104 aircraft (2.27%) | `m3_profile.ipynb` Cell 4: `Empty or NULL callsigns: 184 / 8104 (2.27%)` | **Inferred:** Crew not entering a flight identifier into the FMS on general aviation, private, or military flights | Convert empty strings to `NULL` and flag as not reported | `missing_not_reported` | Muhammed Osman Kara |
| **I-04** | 1. Missing values | `flight_state.velocity` | `NULL` ground speed | 1 row (0.01% of 8,026 rows) | `m3_profile.ipynb` Cell 4: `Missing_Count = 1` in the `velocity` column | **Inferred:** ADS-B velocity subframe missed during surface movement | Keep as `NULL` and flag as not reported | `missing_not_reported` | Muhammed Osman Kara |
| **I-05** | 2. Invalid values | `flight_state.velocity` | Impossible supersonic speed spikes (`> 350 m/s`) | 3 rows (`aircraft_key`: `3218` $\rightarrow$ `2160.22 m/s`; `3887` $\rightarrow$ `914.97 m/s`; `7109` $\rightarrow$ `1390.56 m/s`) | `m3_profile.ipynb` Cell 6: Civilian subsonic aircraft cannot exceed Mach 1 (~343 m/s), yet `2160.22 m/s` (~Mach 6.3) was recorded | **Inferred:** ADS-B velocity message decoding error or telemetry frame corruption | Set the cleaned `velocity` column to `NULL`; preserve the raw value in `velocity_raw` | `invalid_supersonic` | Oguzhan Sukru Keles |
| **I-06** | 2. Invalid values | `flight_state.baro_altitude` | Negative altitude while airborne (`< 0 m` with `on_ground = 0`) | 37 rows (37 of the 45 total negative altitude records are airborne; e.g. `aircraft_key` `153` `-68.58 m`, `1200` `-99.06 m`, min `-152.40 m`) | `m3_profile.ipynb` Cell 6 and `clean.py` output: 37 records satisfying `on_ground == 0 AND baro_altitude_raw < 0.0` | **Confirmed (Domain Rule):** Standard pressure altitude (QNE = 1013.25 hPa) reads negative for flights near sea level under high local pressure | Set uncalibrated negative altitude to `NULL` in the `baro_altitude` column; preserve the raw value | `invalid_negative_airborne` | Oguzhan Sukru Keles |
| **I-07** | 2. Invalid values | `flight_state.baro_altitude` | Altitude exceeding the civil aviation ceiling (`> 20,000 m`) | 1 row (`baro_altitude = 20,604.48 m`) | `m3_profile.ipynb` Cell 6: Maximum altitude is `20604.48 m` (~67,600 ft), which exceeds the civil/business jet ceiling (~15,500 m) | **Inferred:** Transponder Gillham code bit flip | Set the cleaned `baro_altitude` column to `NULL`; preserve the raw value | `invalid_ceiling` | Muhammet Ali Ozturk |
| **I-08** | 2. Invalid values | `aircraft.callsign` | Sentinel / test callsigns | 5 rows (`a98abf`: `UNIDENT`, `a249ca`: `0000000`, `adfb16`: `00000000`, `a3962d`: `00000000`, `87ad9b`: `TEST`) | `m3_profile.ipynb` Cell 6: Exact match with known default transponder test strings | **Confirmed:** Transponder factory/test default identification strings | Set the cleaned `callsign` value to `NULL` and flag as sentinel | `invalid_sentinel` | Muhammed Osman Kara |
| **I-09** | 3. Unusual values | `flight_state.baro_altitude` | High-altitude flights (`> 13,100 m` / FL430) | 57 rows (between `13,100 m` and `20,000 m`) | Although above the standard airliner ceiling, these are within the legal cruising altitudes (FL430–FL510) of long-range business jets | **Confirmed:** Business jets using high-altitude corridors to avoid commercial traffic | **Keep the value as is, without deleting it**; flag as unusually high | `unusual_high` | Oguzhan Sukru Keles |
| **I-10** | 3. Unusual values | `flight_state.velocity` | High subsonic ground speeds (`> 285 m/s` and `<= 350 m/s`) | 50 rows | `clean.py` output: flights exceeding the `285.0 m/s` (~1,026 km/h) threshold but remaining below the speed of sound | **Confirmed:** Increased ground speed due to a strong jet stream tailwind | **Keep the value as is, without deleting it**; flag as unusually high | `unusual_high` | Muhammet Ali Ozturk |
| **I-11** | 4. Cross-field failures | `flight_state.baro_altitude` | `baro_altitude > 1,000 m` while `on_ground = 1` | 1 row | `m3_profile.ipynb` Cell 8: `On ground but baro_altitude > 1000m: 1` | **Inferred:** Landing gear weight-on-wheels sensor delay or pressure difference at a high-elevation runway | Set the cleaned `baro_altitude` value to `NULL` due to the state contradiction | `cross_field_ground_alt` | Muhammed Osman Kara |
| **I-12** | 4. Cross-field failures | `flight_state.velocity` | Speed `< 15 m/s` while airborne (`on_ground = 0`, `alt > 500 m`) | 13 rows (plus 4 records with `on_ground = 0` and `velocity = 0.0` at all altitudes: `aircraft_key` `487`, `2413`, `2542`, `2652`) | `m3_profile.ipynb` Cell 8 and `src/clean.py`: Fixed-wing aircraft cannot stay airborne (they would stall) at speeds `< 15 m/s` above 500 m | **Inferred:** ADS-B velocity vector remaining frozen/stale while the altitude message is updated | Set the cleaned `velocity` value to `NULL`; keep the valid altitude | `cross_field_air_stationary` | Oguzhan Sukru Keles |
| **I-13** | 5. Text & categories | `aircraft.origin_country` | Non-ASCII characters and long official UN country names | 303 rows in total: 1 row `Côte d'Ivoire` and 302 rows across 7 official country names | `m3_profile.ipynb` Cell 10 `repr()` check: `Côte d'Ivoire` (1), `Kingdom of the Netherlands` (126), `Republic of Korea` (135), `Russian Federation` (35), `Islamic Republic of Iran` (3), `The former Yugoslav Republic of Macedonia` (1), `United Republic of Tanzania` (1), `Syrian Arab Republic` (1) | **Confirmed:** The OpenSky ICAO hex allocation table uses official UN names and accented characters | Convert to standard country names via `data/mappings/country_mapping.csv` | `encoding_fixed` (1), `standardised_name` (302) | Muhammet Ali Ozturk |
| **I-14** | 6. Identity | `aircraft.icao24` | The same active callsign appearing under different `icao24` codes | 5 alias pairs verified out of 42 colliding rows in `m3_profile.ipynb` Cell 10 (`PGT2RJ`, `CWL24`, `WZZ61GJ`, `FIN84G`) | The `4ac887` and `4b4887` codes are 1-bit hex deviations from the Turkish Pegasus code `4bc887`; collisions of `480ed8` (`CWL24`), `471e5a` (`WZZ61GJ`), and `461f22` (`FIN84G`) | **Inferred:** Mode-S downlink bit flip and re-registration / callsign collision | Link to `canonical_aircraft_key` via `data/mappings/aircraft_identity.csv` | `aliased_bit_error` (2), `aliased_duplicate_callsign` (2), `aliased_re_registered` (1) | Muhammed Osman Kara & Oguzhan Sukru Keles |

### 3.1 Checked and Not Found
Per Section 3 and Section 7.3, we explicitly checked our dataset in code for the following potential problems and confirmed that they were not present:

| Family | Check | Query / Method | Result |
| :--- | :--- | :--- | :--- |
| **2. Invalid values** | Out-of-range latitude/longitude values | `SELECT COUNT(*) FROM flight_state WHERE longitude < -180 OR longitude > 180 OR latitude < -90 OR latitude > 90` | `0 rows` (all 8,026 rows are within valid WGS84 bounds; the 78 records with missing time/position were separated into the `quarantine` table in M2). |
| **2. Invalid values** | Negative ground speed, negative wind speed, or negative precipitation | `m3_profile.ipynb` Cell 6: `(df_fs['velocity'] < 0).sum()`, `(df_wr['wind_speed_10m'] < 0).sum()`, `(df_wr['precipitation'] < 0).sum()` | `0 rows` in all three columns. |
| **2. Invalid values** | Extremely negative barometric altitude on the ground (`< -100 m` with `on_ground = 1`) | `on_ground == 1 and alt_raw < -100.0` check in `clean.py` | `0 rows` (`invalid_negative_ground: 0`; the 8 negative altitude records on the ground are between `-100 m` and `0 m` and within high-pressure limits, e.g. `aircraft_key = 731` `-53.34 m`). |
| **4. Cross-field failures** | Aircraft moving at `> 100 m/s` while appearing on the ground (`on_ground = 1`) | `m3_profile.ipynb` Cell 8: `df_fs[(df_fs['on_ground'] == 1) & (df_fs['velocity'] > 100)]` | `0 rows` (`cross_field_ground_fast: 0`). |
| **4. Sequence failures** | Stuck weather sensor ($\ge 12$ consecutive hours with the same value) | `m3_profile.ipynb` Cell 8: consecutive run length (`runs.max()`) check on `df_wr_sorted` | `0 stuck sensors` (the longest consecutive repeat was measured as **4 hours** for `temperature_2m` and **3 hours** for `wind_speed_10m`). |

---

## 4. Missing Values
We identified three main missingness patterns in our dataset and determined the mechanism (MCAR, MAR, MNAR) of each with supporting evidence:

1. **`flight_state.baro_altitude` Missingness When `on_ground = 1` (820 rows — MAR: Missing At Random):**
   * **Where and When:** As seen in our `notebooks/m3_profile.ipynb` (Cell 4) output, `baro_altitude` is `NULL` in **820 of the 849 rows (96.58%)** recorded while the aircraft is on the ground (`on_ground = 1`), whereas it is `NULL` in only **3 of the 7,177 rows (0.04%)** recorded while the aircraft is airborne (`on_ground = 0`).
   * **Mechanism and Evidence:** This pattern is a direct example of the **MAR (Missing At Random)** mechanism, because the probability of the altitude data being missing depends on another observed variable in the dataset (`on_ground = 1`). When aircraft land, their ADS-B transponders switch from "Airborne Position" messages, which carry the altitude field, to "Surface Position" messages, which do not contain an altitude field.
   * **Consequence for M4 Analyses:** We flagged these records as `missing_on_ground` and left them as `NULL` (assigning `0 m` would create an incorrect assumption for airports at different elevations). In M4, all altitude comparisons must apply an `on_ground = 0` filter (or `baro_altitude_flag IN ('ok', 'unusual_high')`); otherwise, countries that happen to have more aircraft on the ground at that moment will artificially appear to have higher data loss.

2. **`flight_state.baro_altitude` Missingness When `on_ground = 0` (3 rows — MCAR: Missing Completely At Random):**
   * **Where and When:** Only 3 of the 7,177 airborne flight records (0.04%) are affected, with no country or location clustering.
   * **Mechanism and Evidence:** This is an **MCAR (Missing Completely At Random)** mechanism; these are isolated packet drops from the barometric altimeter encoder while the GPS position broadcast continues.
   * **Consequence for M4 Analyses:** Flagged as `missing_sensor_drop`; excluding these 0.04% of records from `AVG()` calculations does not introduce any bias into M4 results.

3. **`aircraft.callsign` Empty / Not Reported Values (184 rows — MNAR: Missing Not At Random):**
   * **Where and When:** The callsign is empty or `NULL` for 184 of the 8,104 aircraft (2.27%) in the `aircraft` table.
   * **Mechanism and Evidence:** This is an **MNAR (Missing Not At Random)** mechanism; while scheduled commercial airline flights must broadcast their flight plan callsign, general aviation (VFR), private jets, or military/state aircraft may leave the callsign blank for operational confidentiality. The missingness therefore depends directly on the aircraft's unobserved flight category.
   * **Consequence for M4 Analyses:** Flagged as `missing_not_reported`; in M4, analyses based on callsign prefix (airline) should take into account that they represent only scheduled commercial traffic and do not cover the 2.27% of the fleet.

---

## 5. Flags and Rules

### 5.1 Flag Dictionary & Rule Precedence
In `src/clean.py`, our rules run in a deterministic precedence order (**1. Missing $\rightarrow$ 2. Invalid / Sentinel $\rightarrow$ 3. Cross-Field / Sequence Failure $\rightarrow$ 4. Unusual / Outlier $\rightarrow$ 5. OK**), and the first matching rule assigns the flag:

| Table.Column | Order | Flag | Meaning | Rule with Constants | Cleaned Value | Count |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`aircraft_clean.identity_flag`** | 1 | `aliased_bit_error` | The transponder `icao24` code is a 1-bit hex corruption of a known aircraft | `kind == 'icao24_bit_error'` in `aircraft_identity.csv` | Redirected to the main aircraft via `canonical_aircraft_key` | 2 |
| | 2 | `aliased_duplicate_callsign` | Two different hex codes are simultaneously broadcasting the same callsign | `kind == 'duplicate_callsign'` in `aircraft_identity.csv` | Redirected to the main aircraft via `canonical_aircraft_key` | 2 |
| | 3 | `aliased_re_registered` | Overlapping/re-registered transponder record for the same flight | `kind == 're_registered_or_overlapping'` in `aircraft_identity.csv` | Redirected to the main aircraft via `canonical_aircraft_key` | 1 |
| | 4 | `ok` | Unique, non-colliding aircraft entity | Default | `canonical_aircraft_key = aircraft_key` | 8,099 |
| **`aircraft_clean.callsign_flag`** | 1 | `missing_not_reported` | Callsign is `NULL` or an empty string `""` in the raw source | `cs_raw is None or cs_raw.strip() == ""` | `NULL` | 184 |
| | 2 | `invalid_sentinel` | Transponder test or placeholder string | `cs_raw.strip().upper() in SENTINEL_CALLSIGNS` | `NULL` | 5 |
| | 3 | `ok` | Valid callsign | Default | Trimmed, uppercase `callsign_raw` | 7,915 |
| **`aircraft_clean.origin_country_flag`** | 1 | `encoding_fixed` | Non-ASCII character (`Côte d'Ivoire`) converted to the standard English name | `oc_raw in country_mapping` and contains a non-ASCII character | Matched `clean_country` (`Ivory Coast`) | 1 |
| | 2 | `standardised_name` | Long official UN country name converted to the standard short country name | `oc_raw in country_mapping` | Matched `clean_country` | 302 |
| | 3 | `ok` | Standard country name | Default | `origin_country_raw` | 7,801 |
| **`flight_state_clean.baro_altitude_flag`** | 1 | `missing_on_ground` | No altitude transmitted in surface mode (`on_ground = 1`) | `alt_raw is None and og_clean == 1` | `NULL` | 820 |
| | 2 | `missing_sensor_drop` | Momentary altitude sensor loss while airborne (`on_ground = 0`) | `alt_raw is None and og_clean == 0` | `NULL` | 3 |
| | 3 | `invalid_negative_airborne` | Uncalibrated negative QNE altitude while airborne | `og_clean == 0 and alt_raw < MIN_VALID_ALT_AIRBORNE (0.0)` | `NULL` | 37 |
| | 4 | `invalid_negative_ground` | Extremely negative altitude on the ground | `og_clean == 1 and alt_raw < MIN_VALID_ALT_GROUND (-100.0)` | `NULL` | 0 |
| | 5 | `invalid_ceiling` | Altitude above the physical civil aviation ceiling | `alt_raw > MAX_VALID_ALTITUDE (20000.0)` | `NULL` | 1 |
| | 6 | `cross_field_ground_alt` | High-altitude contradiction while `on_ground = 1` | `og_clean == 1 and alt_raw > MAX_GROUND_ALTITUDE (1000.0)` | `NULL` | 1 |
| | 7 | `unusual_high` | High-altitude business jet cruise (outlier preserved) | `alt_raw > UNUSUAL_HIGH_ALTITUDE (13100.0)` | `baro_altitude_raw` **(Preserved)** | 57 |
| | 8 | `ok` | Reliable normal altitude reading | Default | `baro_altitude_raw` | 7,107 |
| **`flight_state_clean.velocity_flag`** | 1 | `missing_not_reported` | Velocity data not reported | `vel_raw is None` | `NULL` | 1 |
| | 2 | `invalid_negative` | Negative ground speed | `vel_raw < MIN_VALID_VELOCITY (0.0)` | `NULL` | 0 |
| | 3 | `invalid_supersonic` | Supersonic speed that is impossible for civil aircraft | `vel_raw > MAX_VALID_VELOCITY (350.0)` | `NULL` | 3 |
| | 4 | `cross_field_ground_fast` | Moving above takeoff speed on the ground (`on_ground = 1`) | `og_clean == 1 and vel_raw > MAX_GROUND_VELOCITY (100.0)` | `NULL` | 0 |
| | 5 | `cross_field_air_stationary` | Speed below stall speed while airborne (`> 500 m`) contradiction | `og_clean == 0 and alt_clean > 500.0 and vel_raw < MIN_AIRBORNE_VELOCITY (15.0)` | `NULL` | 13 |
| | 6 | `unusual_high` | Jet-stream-assisted high ground speed (outlier preserved) | `vel_raw > UNUSUAL_HIGH_VELOCITY (285.0)` | `velocity_raw` **(Preserved)** | 50 |
| | 7 | `ok` | Reliable normal speed reading | Default | `velocity_raw` | 7,959 |
| **`weather_reading_clean.*_flag`** | 1–6 | `ok` | All 168 hourly readings for `temperature_2m`, `wind_speed_10m`, and `precipitation` passed the bounds, stuck-sensor, and Robust Z-Score checks | `MIN/MAX_VALID_TEMP`, `MAX_VALID_WIND`, `STUCK_SENSOR_RUN (12)`, `ROBUST_Z_THRESHOLD (3.5)` | Raw value preserved | 168 each |

### 5.2 Constants
The rationale for choosing each constant we defined in `src/clean_config.py`, and the sensitivity effect of changing each threshold, are explained below:

* **`MIN_LON = -180.0`, `MAX_LON = 180.0`, `MIN_LAT = -90.0`, `MAX_LAT = 90.0`**: Standard WGS84 geographic coordinate bounds; narrowing these bounds would reject valid transoceanic or polar flights, while widening them would allow geometrically impossible coordinates.
* **`MIN_VALID_ALT_AIRBORNE = 0.0`**: Aircraft in flight (`on_ground = 0`) cannot fly below sea level; the negative airborne altitudes in our dataset (between `-15.24 m` and `-152.40 m`) are uncalibrated QNE deviations in high-pressure systems, and if this threshold is moved to `-100.0`, 37 approach records would be classified as `ok` instead of `invalid_negative_airborne`.
* **`MIN_VALID_ALT_GROUND = -100.0`**: Allows slightly negative QNE readings on the ground (`on_ground = 1`) at sea-level airports on days with high atmospheric pressure (`> 1025 hPa`) (8 records in our dataset, such as `-53.34 m`); setting it to `0.0` would unnecessarily set these 8 valid ground records to `NULL`.
* **`MAX_VALID_ALTITUDE = 20000.0`**: Commercial aircraft have a ceiling of ~13,100 m (FL430) and business jets ~15,500 m (FL510); the `20000.0 m` limit catches the bit error at `20,604.48 m` (`invalid_ceiling`: 1 row), and even if lowered to `15000.0 m`, only the same 1 row is affected.
* **`UNUSUAL_HIGH_ALTITUDE = 13100.0`**: Corresponds to FL430, the standard upper ceiling of commercial passenger aircraft ($\approx \text{Median} + 1.62 \times \text{MAD}$), and flags 57 high-altitude flights as `unusual_high` without deleting them; setting it to `12500.0 m` would increase the count by including commercial cruise flights, while setting it to `14000.0 m` would miss most business jets.
* **`MIN_VALID_VELOCITY = 0.0`**: Ground speed is a scalar magnitude and cannot be negative.
* **`MAX_VALID_VELOCITY = 350.0`**: The speed of sound at sea level is ~343 m/s; the `350.0 m/s` threshold catches the 3 corrupted telemetry records at `914.97 m/s`, `1,390.56 m/s`, and `2,160.22 m/s`, while lowering the threshold to `300.0 m/s` would wrongly delete valid transoceanic flights riding the jet stream.
* **`UNUSUAL_HIGH_VELOCITY = 285.0`**: Determined based on the Median (`208.18 m/s`) + $2.0 \times \text{MAD}$ (`38.28 m/s`) limit (`284.74 m/s`), and flags the 50 aircraft flying with a strong tailwind as `unusual_high` while preserving their values; lowering the threshold would also classify normal cruise flights as outliers.
* **`MAX_GROUND_ALTITUDE = 1000.0`**: Catches the altitude contradiction above 1,000 m while `on_ground = 1` (1 row); raising it would leave this contradictory record as `ok`.
* **`MAX_GROUND_VELOCITY = 100.0`**: Since the rotation speed (`V_R`) of heavy passenger aircraft is ~75–85 m/s, moving on the ground above `100.0 m/s` (360 km/h) is physically impossible.
* **`MIN_AIRBORNE_VELOCITY = 15.0`**: Since fixed-wing aircraft above an altitude of `500 m` cannot remain airborne below `15.0 m/s`, this catches frozen velocity vectors (13 rows); raising the threshold to `50.0 m/s` would also affect slow flights in approach/holding patterns.
* **`SENTINEL_CALLSIGNS`**: Contains transponder test and default strings (`00000000`, `0000000`, `000000`, `UNIDENT`, `TEST`, `999999`, `9999999`, `N/A`, `NONE`) and cleans the 5 sentinel callsigns.
* **`MIN_VALID_TEMP = -60.0`, `MAX_VALID_TEMP = 60.0`, `MAX_VALID_WIND = 150.0`**: Physical lower and upper bounds for worldwide meteorological surface stations.
* **`STUCK_SENSOR_RUN = 12`**: In our 168-hour Istanbul weather series, the longest natural constant run is 4 hours for `temperature_2m` and 3 hours for `wind_speed_10m`; choosing `12` consecutive hours catches genuine sensor freezes without producing false positives on calm nights.
* **`ROBUST_Z_THRESHOLD = 3.5`**: The standard statistical outlier threshold for the modified Z-score based on the median and MAD ($0.6745 \times \vert{}x - \text{Median}\vert{} / \text{MAD} > 3.5$).
* **`MAX_MISSING_SHARE_WARN = 0.25`**: The validation threshold in `src/validate.py` that produces a `WARN` when more than 25% of altitude values are missing per entity (aircraft).

---

## 6. Mapping and Identity Decisions
All of our text standardization and identity resolution decisions are stored as data in [`data/mappings/country_mapping.csv`](../data/mappings/country_mapping.csv) and [`data/mappings/aircraft_identity.csv`](../data/mappings/aircraft_identity.csv) rather than being embedded in Python code.

### 6.1 Matching Steps & Counts
1. **Country Name Standardization (`data/mappings/country_mapping.csv`):**
   * *Step 1 (Profiling with `repr()`):* All distinct `origin_country` values were examined with `repr()` in `notebooks/m3_profile.ipynb` (Cell 10).
   * *Step 2 (Normalization and Human Decision):* `Côte d'Ivoire`, which contains a non-ASCII character (**1 aircraft** $\rightarrow$ `Ivory Coast`, `encoding_fixed`), and 7 countries with long UN diplomatic names (`Kingdom of the Netherlands`: **126 aircraft** $\rightarrow$ `Netherlands`; `Republic of Korea`: **135 aircraft** $\rightarrow$ `South Korea`; `Russian Federation`: **35 aircraft** $\rightarrow$ `Russia`; `Islamic Republic of Iran`: **3 aircraft** $\rightarrow$ `Iran`; `The former Yugoslav Republic of Macedonia`: **1 aircraft** $\rightarrow$ `North Macedonia`; `United Republic of Tanzania`: **1 aircraft** $\rightarrow$ `Tanzania`; `Syrian Arab Republic`: **1 aircraft** $\rightarrow$ `Syria` — **302 aircraft** in total, `standardised_name`) were mapped.
   * *Matching Result:* **All 8 of 8 rules matched** (`matched 8 of 8; unmatched: none`; 303 rows updated in total).

2. **Aircraft Identity Resolution (`data/mappings/aircraft_identity.csv`):**
   * *Step 1 (Callsign Collision Check):* Different `icao24` transponder codes sharing the same `callsign` value were scanned in `notebooks/m3_profile.ipynb` (Cell 10), yielding a candidate pool of 42 rows.
   * *Step 2 (Hexadecimal Hamming Distance and Trajectory Verification):* From the candidates, 5 identity rules with verified evidence were created:
     * **`4ac887` and `4b4887` $\rightarrow$ `4bc887` (`PGT2RJ`):** All three transponders are simultaneously broadcasting the Pegasus (`PGT2RJ`) callsign; `4bc887` lies in the Turkish ICAO block, while `4ac887` (Swedish prefix) and `4b4887` (Swiss prefix) are downlink bit errors differing by only 1 hex digit (`icao24_bit_error` $\rightarrow$ `aliased_bit_error`, 2 rows).
     * **`480ed8` $\rightarrow$ `400ed8` (`CWL24`):** The UK Royal Air Force (`CWL24`) callsign was seen both on the active UK code `400ed8` and on `480ed8`, which was quarantined due to missing coordinates (`duplicate_callsign` $\rightarrow$ `aliased_duplicate_callsign`, 1 row).
     * **`471e5a` $\rightarrow$ `471e5e` (`WZZ61GJ`):** Two WizzAir Hungary codes reported the same `WZZ61GJ` flight seconds apart at coordinates ~`50.0N, 21.0E`; `471e5e`, the most recent active transponder, was chosen as the main entity (`re_registered_or_overlapping` $\rightarrow$ `aliased_re_registered`, 1 row).
     * **`461f22` $\rightarrow$ `461fa2` (`FIN84G`):** Of the two Finnair records broadcasting the `FIN84G` callsign in the same data packet, `461fa2` was assigned as the main entity (`duplicate_callsign` $\rightarrow$ `aliased_duplicate_callsign`, 1 row).
   * *Matching Result:* **All 5 of 5 identity rules matched** (`matched 5 of 5; unmatched: none`).

---

## 7. Counts and Validation

### 7.1 Final Output of `python src/clean.py` (Run Twice for Idempotence)
When the `python src/clean.py` command is run twice in succession, it produces the exact same cleaned layer and summary output without touching the M2 tables:

```text
aircraft_clean: 8,104 rows (raw: 8,104)
  identity         -> ok: 8099  aliased_bit_error: 2  aliased_duplicate_callsign: 2  aliased_re_registered: 1
  callsign         -> ok: 7915  missing_not_reported: 184  invalid_sentinel: 5
  origin_country   -> ok: 7801  encoding_fixed: 1  standardised_name: 302

flight_state_clean: 8,026 rows (raw: 8,026)
  longitude        -> ok: 8026
  latitude         -> ok: 8026
  baro_altitude    -> ok: 7107  missing_on_ground: 820  missing_sensor_drop: 3  invalid_negative_airborne: 37  invalid_ceiling: 1  cross_field_ground_alt: 1  unusual_high: 57
  velocity         -> ok: 7959  missing_not_reported: 1  invalid_supersonic: 3  cross_field_air_stationary: 13  unusual_high: 50
  on_ground        -> ok: 8026

weather_reading_clean: 168 rows (raw: 168)
  temperature_2m   -> ok: 168
  wind_speed_10m   -> ok: 168
  precipitation    -> ok: 168

mappings: country_mapping 8 rows (matched 8 of 8; unmatched: none)
          aircraft_identity 5 rows (matched 5 of 5; unmatched: none)
STATUS: COMPLETE
```

## 8. What We Can't Say

Even after constructing a strict, non-destructive cleaned layer and validating our expectations, several structural and observational limitations remain that our team must account for during M4 exploratory analysis:

### 8.1 Entities and Periods That Cannot Be Compared
* **Single Flight Snapshot vs. 7-Day Weather Window (Temporal & Spatial Mismatch):** Our `flight_state_clean` table captures 8,026 aircraft states from a single global polling window on `2026-10-01` (between `08:55:11Z` and `10:33:02Z`), whereas `weather_reading_clean` contains 168 hourly observations spanning seven full days for a single grid point (`location_key = 1` at `41.2753N, 28.7519E`, Istanbul Airport). In M4, we cannot compare flight behaviour across different days or diurnal cycles, nor can we join global flights outside the Istanbul terminal area to `weather_reading_clean` without introducing severe spatial mismatch.
* **Unfiltered Country Comparisons and Extreme Sample-Size Imbalance:** Across the 8,104 registered airframes, fleet sizes per `origin_country` range from 1,725 (`United States`) and 655 (`United Kingdom`) down to just 1 aircraft for several nations (`Trinidad and Tobago`, `Bolivia`, `Tanzania`, `North Macedonia`, `Syria`, and `Ivory Coast`). Because 96.58% (`820 / 849`) of surface records (`on_ground = 1`) omit barometric altitude under an MAR mechanism, comparing raw country-level altitude averages or missing-data shares without filtering to airborne traffic (`on_ground = 0`) and enforcing a minimum aircraft count threshold is statistically invalid in M4.
* **Actual Departure and Arrival Delay Minutes:** The OpenSky `/states/all` endpoint provides instantaneous state vectors (`longitude`, `latitude`, `baro_altitude`, `velocity`, `on_ground`) but does not expose scheduled departure/arrival times or origin/destination airport codes. Consequently, we cannot directly quantify schedule delay durations from this table alone until longitudinal trajectory tracking or schedule tables are integrated.

### 8.2 Mechanisms We Couldn't Establish
* **Airborne Telemetry Glitches vs. Persistent Stuck Sensors:** Because `flight_state` contains exactly one observation per `aircraft_key` (8,026 rows across 8,026 unique aircraft), we could not perform longitudinal sequence checks (such as consecutive identical value runs or step-change detection) on aircraft telemetry as we did on the 168-hour `weather_reading` series. Thus, for the 13 airborne stationary velocity contradictions (`cross_field_air_stationary`) and the 3 airborne altitude dropouts (`missing_sensor_drop`), we cannot definitively establish whether they represent isolated single-packet decoding glitches (MCAR) or hardware transponders that remained frozen throughout the entire flight.
* **Unresolved Callsign Collisions (31 Remaining Candidate Rows):** In `notebooks/m3_profile.ipynb` (Cell 10), our duplicate callsign check identified 42 aircraft rows sharing identical non-null callsigns across distinct `icao24` hex addresses. While we resolved 5 clear alias pairs (11 rows total, such as the 1-bit hex corruptions on `PGT2RJ` and overlapping positions on `WZZ61GJ`) in `data/mappings/aircraft_identity.csv`, we could not establish the exact mechanism for the remaining pairs (such as `ACA178` on Canadian hexes `c01317` and `c0193b`, `BAW42DB` on UK hexes `407464` and `406464`, `CAR3` on Australian hexes `7cf353` and `7cf451`, or `EZY35AY` across `407f79`, `406f79`, and `407d79`). Without multi-hour trajectory histories, we cannot distinguish legitimate airline flight-number reuse across turnaround legs or military formation flying from hardware bit-flips.
* **Root Cause of the 78 Quarantined Records:** During M2 loading, 78 OpenSky records were routed to `quarantine` due to missing `observed_at` (`time_position` was `NULL`). Because OpenSky does not expose ground-receiver diagnostic logs in the state vector payload, the exact mechanism behind these missing timestamps remains `unknown` (potentially stale terrestrial receiver caches at the edge of coverage).

### 8.3 Decisions We Would Revisit With More Data
* **Global vs. Per-Airframe Outlier Thresholds (`UNUSUAL_HIGH_ALTITUDE = 13100.0`, `UNUSUAL_HIGH_VELOCITY = 285.0`):** Because our M2 schema lacks an aircraft type/wake-turbulence category column and holds only one timestamp per aircraft, we applied fleet-wide thresholds based on overall robust statistics (median and MAD) to flag `unusual_high` altitudes (57 rows) and velocities (50 rows). Once M5 scheduled ingestion accumulates multi-hour time series per `aircraft_key`, we would revisit this design to compute **per-entity** medians, MADs, and consecutive stuck-sensor run lengths (`STUCK_SENSOR_RUN`) for individual aircraft.
* **Nullifying Negative Airborne Altitudes (`MIN_VALID_ALT_AIRBORNE = 0.0`, 37 Rows):** We set 37 negative airborne `baro_altitude` readings (ranging from `-15.24 m` down to `-152.40 m`) to `NULL` under `invalid_negative_airborne` because OpenSky reports uncalibrated standard pressure altitude ($QNE = 1013.25\text{ hPa}$), which reads negative near sea level during high-pressure weather systems. If we expand our Open-Meteo ingestion to fetch local sea-level atmospheric pressure ($QNH$) at each flight's coordinates, we would revisit this rule and convert raw pressure altitude into true pressure-corrected altitude rather than discarding those 37 low-altitude approach measurements as `NULL`.
