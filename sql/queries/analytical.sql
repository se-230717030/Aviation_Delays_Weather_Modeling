-- Question: Hangi ülkelere (origin_country) ait uçakların havada (on_ground = 0) kaydettikleri ortalama yükseklik (baro_altitude) en fazladır?
-- Result grain: Her bir origin_country (menşe ülke) için tek bir satır (One row per country).
-- Aggregation logic: Okumalar kendi taneliğinde (aircraft-time) toplanır. AIRCRAFT tablosuyla yapılan JOIN işlemi bu taneliği bozmaz. Hatalı/sayısal olmayan baro_altitude verileri load aşamasında NULL yapıldığı için AVG() fonksiyonu bunları otomatik olarak atlar ve doğru ortalama hesaplar.

SELECT 
    a.origin_country,
    COUNT(DISTINCT a.aircraft_key) as total_aircraft_in_air,
    ROUND(AVG(fs.baro_altitude), 2) as avg_baro_altitude
FROM flight_state fs
JOIN aircraft a ON fs.aircraft_key = a.aircraft_key
WHERE fs.on_ground = 0 AND fs.baro_altitude IS NOT NULL
GROUP BY a.origin_country
ORDER BY avg_baro_altitude DESC
LIMIT 10;