"""
M3 Data Cleaning Configuration & Thresholds
Her sabit docs/cleaning_report.md Bölüm 5'te kanıtlarıyla gerekçelendirilmiştir.
"""

# --- COĞRAFİ SINIRLAR (Family 2: Invalid Values) ---
MIN_LON = -180.0  # Dünya boylam alt sınırı (derece); bkz. rapor §5
MAX_LON = 180.0   # Dünya boylam üst sınırı (derece); bkz. rapor §5
MIN_LAT = -90.0   # Dünya enlem alt sınırı (derece); bkz. rapor §5
MAX_LAT = 90.0    # Dünya enlem üst sınırı (derece); bkz. rapor §5

# --- İRTİFA (BARO_ALTITUDE) EŞİKLERİ (Metre) ---
# Yüksek basınçlı havalarda deniz seviyesindeki havalimanlarında hafif negatif barometrik irtifa (-100m'ye kadar) normaldir;
# ancak havada (on_ground=0) negatif irtifa veya yerde -100m altı sensör kalibrasyon hatasıdır.
MIN_VALID_ALT_AIRBORNE = 0.0      # Havada minimum geçerli irtifa (m); altı invalid_negative_airborne; bkz. rapor §5
MIN_VALID_ALT_GROUND = -100.0     # Yerde minimum geçerli barometrik irtifa (m); altı invalid_negative_ground; bkz. rapor §5
MAX_VALID_ALTITUDE = 20000.0      # Sivil/ticari havacılık mutlak tavan sınırı (m); üstü invalid_ceiling; bkz. rapor §5
UNUSUAL_HIGH_ALTITUDE = 13100.0   # Robust IQR / Q75 + 1.5*IQR (~43,000 ft iş jeti/uzun menzil sınırı); silinmez, unusual_high işaretlenir; bkz. rapor §5

# --- HIZ (VELOCITY) EŞİKLERİ (m/s) ---
MIN_VALID_VELOCITY = 0.0          # Hız negatif olamaz (m/s); bkz. rapor §5
MAX_VALID_VELOCITY = 350.0        # Ses hızı (~343 m/s) üstü sivil uçaklar için imkansızdır (Örn: MGH188 1202.04 m/s); bkz. rapor §5
UNUSUAL_HIGH_VELOCITY = 285.0     # Q75 + 1.5*IQR (~285 m/s, kuvvetli jet akımı); silinmez, unusual_high işaretlenir; bkz. rapor §5

# --- ÇAPRAZ ALAN (CROSS-FIELD) ÇELİŞKİ EŞİKLERİ (Family 4) ---
MAX_GROUND_ALTITUDE = 1000.0      # on_ground=1 iken 1000m üstü irtifa (yüksek irtifa havalimanları hariç şüpheli/çelişkili); bkz. rapor §5
MAX_GROUND_VELOCITY = 100.0       # on_ground=1 iken 100 m/s (360 km/s) üstü hız (kalkış hızı sınırını aşan transponder gecikmesi); bkz. rapor §5
MIN_AIRBORNE_VELOCITY = 15.0      # on_ground=0 ve irtifa > 500m iken hızın 0 veya <15 m/s olması (stall/stationary çelişkisi); bkz. rapor §5

# --- METİN VE SENTINEL (SAHTE) DEĞERLER (Family 2 & 5) ---
SENTINEL_CALLSIGNS = {
    "00000000", "0000000", "000000", "UNIDENT", "TEST", "999999", "9999999", "N/A", "NONE"
}  # Transponder test veya tanımsız çağrı işaretleri; NULL yapılır ve invalid_sentinel işaretlenir; bkz. rapor §5

# --- HAVA DURUMU (WEATHER_READING) EŞİKLERİ (Open-Meteo) ---
MIN_VALID_TEMP = -60.0            # Dünya meteorolojik mantıklı alt sıcaklık sınırı (°C); bkz. rapor §5
MAX_VALID_TEMP = 60.0             # Dünya meteorolojik mantıklı üst sıcaklık sınırı (°C); bkz. rapor §5
MAX_VALID_WIND = 150.0            # Maksimum geçerli rüzgar hızı (km/s veya m/s); bkz. rapor §5
STUCK_SENSOR_RUN = 12             # Saatlik hava durumu verisinde ardışık 12 saat (0.0 yağış hariç) birebir aynı değer tekrarı; bkz. rapor §5
ROBUST_Z_THRESHOLD = 3.5          # Medyan ve MAD tabanlı Robust Z-Score uç değer eşiği (|z| > 3.5); bkz. rapor §5

# --- VALIDATION (DOĞRULAMA) EŞİKLERİ (src/validate.py için) ---
MAX_MISSING_SHARE_WARN = 0.25     # Tablo genelinde %25'ten fazla eksik veri varsa WARN üretir; bkz. rapor §6