-- Question: How do hourly temperature, wind speed, and precipitation evolve over the 7-day forecast window?
-- Grain: One row per (location_key, observed_at) hour.
-- Flag filter: temperature_2m_flag IN ('ok', 'unusual_high', 'unusual_low') AND wind_speed_10m_flag IN ('ok', 'unusual_high').
SELECT 
    location_key,
    latitude,
    longitude,
    observed_at,
    temperature_2m,
    temperature_2m_flag,
    wind_speed_10m,
    wind_speed_10m_flag,
    precipitation,
    precipitation_flag
FROM weather_reading_resolved
WHERE location_key = ?
  AND temperature_2m_flag IN ('ok', 'unusual_high', 'unusual_low')
  AND wind_speed_10m_flag IN ('ok', 'unusual_high')
ORDER BY observed_at ASC;