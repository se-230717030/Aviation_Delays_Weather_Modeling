# Data Source Cards

## Source 1: OpenSky Network
- **source_name:** OpenSky Network Live Flight Data
- **provider:** OpenSky Network
- **url:** https://openskynetwork.github.io/opensky-api/
- **access_method:** REST API, Username/Password authentication for higher rate limits.
- **licence:** Custom / OpenSky Terms of Use
- **terms_notes:** Data is free for academic and non-commercial research purposes. Redistribution of the raw real-time dataset in massive volumes is restricted; therefore, bulk raw data is kept out of version control (.gitignore). Attribution is required.
- **update_cadence:** Continuous / Real-time (states update every few seconds)
- **coverage:** Global airspace, active flights.
- **record_meaning:** One row/item = the current state vector (position, velocity, identity) of a single aircraft at the polling second.
- **join_key:** `origin_airport` / `destination_airport` via geographic coordinates to weather data, joined by timestamp.
- **first_retrieved:** 2026-10-01T10:15:00Z
- **known_issues:** Strict rate limits apply even with an account (429 Too Many Requests). Many flights may have missing origin or destination fields in the live states endpoint, which will require filtering during M2/M3.

## Source 2: Open-Meteo
- **source_name:** Open-Meteo API
- **provider:** Open-Meteo
- **url:** https://open-meteo.com/
- **access_method:** Public REST API, no API key required.
- **licence:** CC BY 4.0
- **terms_notes:** Free for non-commercial use up to 10,000 API calls per day. Requires attribution.
- **update_cadence:** Hourly
- **coverage:** Global weather, forecast and historical.
- **record_meaning:** One row/item = the weather forecast and conditions at a specific latitude/longitude coordinate for a specific hour.
- **join_key:** Geographic coordinates (latitude/longitude) and the rounded hourly timestamp.
- **first_retrieved:** 2026-10-01T10:15:00Z
- **known_issues:** Grid-based mapping means exact airport coordinates must be snapped to the nearest weather grid cell.