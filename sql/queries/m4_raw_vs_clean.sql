-- Question: How does M3 cleaning and flag filtering change global airborne velocity and altitude metrics compared to raw M2 data?
-- Grain: One summary row per layer/filter mode (Raw M2 vs Cleaned OK+Unusual vs Cleaned Strict OK).
SELECT 
    '1_Raw_M2_Airborne' AS dataset_layer,
    COUNT(*) AS n_rows,
    COUNT(DISTINCT aircraft_key) AS n_aircraft,
    ROUND(AVG(velocity), 2) AS mean_velocity_ms,
    ROUND(AVG(baro_altitude), 2) AS mean_altitude_m,
    ROUND(MAX(velocity), 2) AS max_velocity_ms,
    ROUND(MIN(baro_altitude), 2) AS min_altitude_m
FROM flight_state
WHERE on_ground = ?

UNION ALL

SELECT 
    '2_Cleaned_Resolved_OK_and_Unusual' AS dataset_layer,
    COUNT(*) AS n_rows,
    COUNT(DISTINCT resolved_aircraft_key) AS n_aircraft,
    ROUND(AVG(velocity), 2) AS mean_velocity_ms,
    ROUND(AVG(baro_altitude), 2) AS mean_altitude_m,
    ROUND(MAX(velocity), 2) AS max_velocity_ms,
    ROUND(MIN(baro_altitude), 2) AS min_altitude_m
FROM flight_state_resolved
WHERE on_ground = ?
  AND velocity_flag IN ('ok', 'unusual_high')
  AND baro_altitude_flag IN ('ok', 'unusual_high')

UNION ALL

SELECT 
    '3_Cleaned_Resolved_Strict_OK_Only' AS dataset_layer,
    COUNT(*) AS n_rows,
    COUNT(DISTINCT resolved_aircraft_key) AS n_aircraft,
    ROUND(AVG(velocity), 2) AS mean_velocity_ms,
    ROUND(AVG(baro_altitude), 2) AS mean_altitude_m,
    ROUND(MAX(velocity), 2) AS max_velocity_ms,
    ROUND(MIN(baro_altitude), 2) AS min_altitude_m
FROM flight_state_resolved
WHERE on_ground = ?
  AND velocity_flag = 'ok'
  AND baro_altitude_flag = 'ok';