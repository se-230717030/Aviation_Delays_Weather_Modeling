-- Question: What is the observation coverage and usable airborne share per origin country?
-- Grain: One row per resolved origin_country.
-- Flag filter: Explicitly counts 'ok' and 'unusual_high' vs missing/invalid flags.
SELECT 
    origin_country,
    COUNT(*) AS total_states,
    COUNT(DISTINCT resolved_aircraft_key) AS unique_aircraft,
    SUM(CASE WHEN on_ground = 1 THEN 1 ELSE 0 END) AS ground_states,
    SUM(CASE WHEN on_ground = 0 THEN 1 ELSE 0 END) AS airborne_states,
    SUM(CASE WHEN on_ground = 0 AND baro_altitude_flag IN ('ok', 'unusual_high') AND velocity_flag IN ('ok', 'unusual_high') THEN 1 ELSE 0 END) AS trusted_airborne_n,
    SUM(CASE WHEN on_ground = 0 AND baro_altitude_flag = 'ok' AND velocity_flag = 'ok' THEN 1 ELSE 0 END) AS strict_ok_airborne_n,
    SUM(CASE WHEN baro_altitude_flag = 'unusual_high' OR velocity_flag = 'unusual_high' THEN 1 ELSE 0 END) AS unusual_high_n,
    ROUND(SUM(CASE WHEN on_ground = 0 AND baro_altitude_flag IN ('ok', 'unusual_high') THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2) AS usable_airborne_pct
FROM flight_state_resolved
GROUP BY origin_country
ORDER BY total_states DESC;