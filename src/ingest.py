import os
import json
import time
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

# 1. YOL DÜZELTMESİ: Projenin ana kök dizinini otomatik bulma
# Bu dosyanın (ingest.py) bulunduğu yerin (src) bir üst klasörünü (ana proje dizini) alır.
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

# .env dosyasını kök dizinden yükleme (M1 Sıfır Tolerans Kuralı)
env_path = os.path.join(BASE_DIR, '.env')
load_dotenv(dotenv_path=env_path)

# Sabitler
OPENSKY_URL = "https://opensky-network.org/api/states/all"
OPENMETEO_URL = "https://api.open-meteo.com/v1/forecast"
MAX_RETRIES = 3
TIMEOUT_SEC = 15

def setup_directories():
    # Kök dizin altındaki data/raw klasörlerini oluşturur
    os.makedirs(os.path.join(BASE_DIR, "data", "raw", "opensky"), exist_ok=True)
    os.makedirs(os.path.join(BASE_DIR, "data", "raw", "openmeteo"), exist_ok=True)

def fetch_data(url, params=None, auth=None):
    retries = 0
    delay = 2  # Başlangıç bekleme süresi

    while retries <= MAX_RETRIES:
        try:
            # Zaman aşımı süresi belirlenmiş istek (M1 Kriteri)
            response = requests.get(url, params=params, auth=auth, timeout=TIMEOUT_SEC)
            
            # Yetkilendirme hataları 401/403 (Hemen durdur, tekrar deneme - M1 Kriteri)
            if response.status_code in (401, 403):
                print(f"KRİTİK HATA: Yetkilendirme başarısız ({response.status_code}) - {url}")
                print("Lütfen .env dosyasındaki kimlik bilgilerinizi kontrol edin.")
                return None
            
            # Başarılı yanıt
            if response.status_code == 200:
                data = response.json()
                # Boş veya beklenmeyen yanıt kontrolü (M1 Kriteri)
                if not data:
                    print(f"UYARI: {url} adresinden boş veri döndü. Hiçbir şey kaydedilmedi.")
                    return None
                return data
            
            # Oran limitleri (429) veya sunucu hataları (5xx) (Artan gecikme ile tekrar - M1 Kriteri)
            if response.status_code == 429 or response.status_code >= 500:
                print(f"HATA {response.status_code}: Erişim sorunu. {delay} saniye bekleniyor...")
                time.sleep(delay)
                retries += 1
                delay *= 2  # Gecikmeyi katlayarak artır (Exponential backoff)
                continue
                
            # Diğer durumlar
            print(f"BEKLENMEYEN DURUM {response.status_code}: {url}")
            return None

        except requests.exceptions.Timeout:
            print(f"ZAMAN AŞIMI: {url} isteği zaman aşımına uğradı. Yeniden deneniyor...")
            retries += 1
            time.sleep(delay)
            delay *= 2
        except requests.exceptions.RequestException as e:
            print(f"AĞ HATASI: {url} bağlantısı koptu - {e}")
            return None

    print(f"BAŞARISIZ: {url} için ayrılan deneme hakkı bitti.")
    return None

def save_raw_data(data, source_name):
    if not data:
        return False
    
    # UTC Zaman Damgası kullanımı (M1 Kriteri)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"{timestamp}.json"
    
    # Dosyanın kaydedileceği kesin yolu belirleme (src/data sorununu çözen kısım)
    filepath = os.path.join(BASE_DIR, "data", "raw", source_name, filename)
    
    # Veriyi hiçbir şekilde değiştirmeden (temizlemeden/filtrelemeden) kaydetme (M1 Kriteri)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    return True

def main():
    print("M1 Ingestion süreci başlatılıyor...\n")
    setup_directories()
    
    summary = {"opensky": 0, "openmeteo": 0, "failed": 0}
    
    # 1. OpenSky Kimlik Bilgilerini Çekme (M1 Sırlar Kriteri)
    opensky_user = os.environ.get("OPENSKY_USERNAME")
    opensky_pass = os.environ.get("OPENSKY_PASSWORD")
    
    if not opensky_user or not opensky_pass:
        print("KRİTİK HATA: OpenSky kimlik bilgileri (.env) bulunamadı!")
        print("Lütfen projenin kök dizininde .env dosyası oluşturup şifreleri girin.")
        return # Kodda şifre yazmasını engellemek için doğrudan çıkış yapar
    
    # 2. OpenSky Verisi Çekimi
    print("OpenSky API'den şifreli veri çekiliyor...")
    opensky_auth = (opensky_user, opensky_pass)
    opensky_data = fetch_data(OPENSKY_URL, auth=opensky_auth)
    
    if save_raw_data(opensky_data, "opensky"):
        record_count = len(opensky_data.get("states", []))
        summary["opensky"] = record_count
        print(f"  -> OpenSky verisi başarıyla kaydedildi ({record_count} kayıt).")
    else:
        summary["failed"] += 1
        print("  -> OpenSky verisi çekilemedi.")

    # 3. Open-Meteo Verisi Çekimi (Şifresiz API)
    print("\nOpen-Meteo API'den veri çekiliyor...")
    meteo_params = {
        "latitude": 41.2753, # Örnek koordinat
        "longitude": 28.7519,
        "current_weather": "true",
        "hourly": "temperature_2m,wind_speed_10m,precipitation"
    }
    meteo_data = fetch_data(OPENMETEO_URL, params=meteo_params)
    
    if save_raw_data(meteo_data, "openmeteo"):
        record_count = len(meteo_data.get("hourly", {}).get("time", []))
        summary["openmeteo"] = record_count
        print(f"  -> Open-Meteo verisi başarıyla kaydedildi ({record_count} saatlik veri).")
    else:
        summary["failed"] += 1
        print("  -> Open-Meteo verisi çekilemedi.")

    # 4. Süreç Özeti Raporu (M1 Kriteri)
    print("\n" + "="*30)
    print("INGESTION ÖZETİ")
    print("="*30)
    print(f"OpenSky Alınan Kayıt Sayısı: {summary['opensky']}")
    print(f"Open-Meteo Alınan Kayıt Sayısı: {summary['openmeteo']}")
    print(f"Başarısız Kaynak Sayısı: {summary['failed']}")
    print("==============================\n")

if __name__ == "__main__":
    main()