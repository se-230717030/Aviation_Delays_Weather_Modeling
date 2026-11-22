import os
import sys
import json
import time
import glob
import argparse
from datetime import datetime, timezone
import requests
from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, BASE_DIR)

from src.db import parse_run_time

load_dotenv(os.path.join(BASE_DIR, ".env"))

# --- INGESTION SABİTLERİ ---
OPENSKY_URL = "https://opensky-network.org/api/states/all"
OPENMETEO_URL = "https://api.open-meteo.com/v1/forecast"

# İstanbul Havalimanı (LTFM) koordinatları
IST_LAT = 41.25
IST_LON = 28.75

REQUEST_TIMEOUT_SEC = 25          # Tek bir HTTP isteği için üst sınır
MAX_RETRIES = 3                   # Ağ hatalarında maksimum deneme sayısı
RETRY_BACKOFF_SEC = 3             # Denemeler arası bekleme çarpanı (3s, 6s, 9s)
OPENMETEO_OVERLAP_DAYS = 1        # Geç güncellenen/revize edilen saatleri kaçırmamak için 1 gün örtüşme
OPENMETEO_MAX_PAST_DAYS = 7       # Open-Meteo /forecast endpoint'inin tek çağrıda güvenli geriye dönük sınırı
OPENSKY_SOURCE_MEMORY_HOURS = 0   # OpenSky /states/all geçmişe dönük snapshot tutmaz (sadece anlık)

RAW_OPENSKY_DIR = os.path.join(BASE_DIR, "data", "raw", "opensky")
RAW_OPENMETEO_DIR = os.path.join(BASE_DIR, "data", "raw", "openmeteo")


def get_last_file_time(directory):
    """Klasördeki en son indirilen ham JSON dosyasının UTC zamanını döndürür."""
    files = sorted(glob.glob(os.path.join(directory, "*.json")))
    if not files:
        return None
    latest_name = os.path.basename(files[-1]).replace(".json", "")
    try:
        return datetime.strptime(latest_name, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def fetch_with_retries(url, params=None, auth=None, headers=None, source_name="API"):
    """Üstel geri çekilme ile HTTP GET yapar; auth hatasında hemen, diğer hatalarda retry sonrası fail eder."""
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(
                url,
                params=params,
                auth=auth,
                headers=headers,
                timeout=REQUEST_TIMEOUT_SEC
            )
            # M5 Kural 2.3: Kimlik doğrulama hatasında (401/403) yeniden deneme, hemen non-zero çık
            if resp.status_code in (401, 403):
                print(
                    f"ERROR [{source_name}]: Kimlik doğrulama başarısız (HTTP {resp.status_code}).",
                    file=sys.stderr
                )
                sys.exit(1)

            if resp.status_code == 429:
                wait = RETRY_BACKOFF_SEC * attempt * 2
                print(f"WARN [{source_name}]: Rate limit (429), {wait}s bekleniyor (deneme {attempt}/{MAX_RETRIES})...")
                time.sleep(wait)
                last_err = f"HTTP 429 Too Many Requests"
                continue

            resp.raise_for_status()
            data = resp.json()
            if not data or not isinstance(data, dict):
                raise ValueError(f"{source_name} boş veya beklenmeyen JSON yanıtı döndürdü.")
            return data

        except (requests.RequestException, ValueError) as exc:
            last_err = exc
            if attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF_SEC * attempt
                print(f"WARN [{source_name}]: Deneme {attempt}/{MAX_RETRIES} başarısız ({exc}). {wait}s sonra tekrar deneniyor...")
                time.sleep(wait)

    print(f"ERROR [{source_name}]: {MAX_RETRIES} deneme tükendi. Son hata: {last_err}", file=sys.stderr)
    sys.exit(1)


def ingest_opensky(run_dt, file_stamp):
    """OpenSky canlı uçuş durumlarını indirir. Kaynak hafızası 0 saat olduğu için uzun boşlukları WARN olarak bildirir."""
    os.makedirs(RAW_OPENSKY_DIR, exist_ok=True)
    last_dt = get_last_file_time(RAW_OPENSKY_DIR)
    if last_dt is not None:
        gap_hours = (run_dt - last_dt).total_seconds() / 3600.0
        if gap_hours > 2.0:
            print(
                f"WARN [OpenSky]: Son başarılı alımdan bu yana {gap_hours:.1f} saat geçti. "
                f"OpenSky /states/all geçmiş snapshot tutmadığı için (memory={OPENSKY_SOURCE_MEMORY_HOURS}h) "
                f"aradaki anlık görüntüler geri alınamaz; güncel snapshot alınıyor."
            )

    # Opsiyonel Failure Drill Hook'u (varsayılan: kapalı)
    username = os.getenv("OPENSKY_USERNAME")
    password = os.getenv("OPENSKY_PASSWORD")
    if os.getenv("PIPELINE_DRILL_BAD_CRED") == "1":
        auth = ("invalid_drill_user", "invalid_drill_pass")
    elif username and password:
        auth = (username, password)
    else:
        auth = None

    data = fetch_with_retries(OPENSKY_URL, auth=auth, source_name="OpenSky")
    states = data.get("states")
    if states is None or len(states) == 0:
        print("ERROR [OpenSky]: 'states' listesi boş veya eksik döndü.", file=sys.stderr)
        sys.exit(1)

    out_path = os.path.join(RAW_OPENSKY_DIR, f"{file_stamp}.json")
    tmp_path = out_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp_path, out_path)
    return len(states), out_path


def ingest_openmeteo(run_dt, file_stamp):
    """
    Open-Meteo saatlik hava durumu verisini kendi kendini iyileştiren (self-healing)
    past_days + overlap penceresiyle indirir.
    """
    os.makedirs(RAW_OPENMETEO_DIR, exist_ok=True)
    last_dt = get_last_file_time(RAW_OPENMETEO_DIR)

    if last_dt is None:
        past_days = OPENMETEO_OVERLAP_DAYS
    else:
        gap_days = max(0, int((run_dt - last_dt).total_seconds() // 86400))
        requested_past = gap_days + OPENMETEO_OVERLAP_DAYS
        if requested_past > OPENMETEO_MAX_PAST_DAYS:
            print(
                f"WARN [Open-Meteo]: Kesinti süresi ({gap_days} gün) kaynak limitini "
                f"({OPENMETEO_MAX_PAST_DAYS} gün) aştı! En fazla son {OPENMETEO_MAX_PAST_DAYS} gün telafi edilecek."
            )
            past_days = OPENMETEO_MAX_PAST_DAYS
        else:
            past_days = max(OPENMETEO_OVERLAP_DAYS, requested_past)

    params = {
        "latitude": IST_LAT,
        "longitude": IST_LON,
        "hourly": "temperature_2m,wind_speed_10m,precipitation",
        "timezone": "UTC",
        "past_days": past_days,
        "forecast_days": 7
    }

    data = fetch_with_retries(OPENMETEO_URL, params=params, source_name="Open-Meteo")
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    if not times:
        print("ERROR [Open-Meteo]: 'hourly.time' listesi boş döndü.", file=sys.stderr)
        sys.exit(1)

    out_path = os.path.join(RAW_OPENMETEO_DIR, f"{file_stamp}.json")
    tmp_path = out_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp_path, out_path)
    return len(times), past_days, out_path


def main():
    parser = argparse.ArgumentParser(description="M5 Self-healing data ingestion for OpenSky and Open-Meteo.")
    parser.add_argument("--run-time", dest="run_time", default=None, help="Pipeline clock (ISO 8601 UTC)")
    args = parser.parse_args()

    # Opsiyonel Timeout Drill Hook'u (varsayılan: kapalı)
    sleep_sec = os.getenv("PIPELINE_DRILL_SLEEP_SEC")
    if sleep_sec:
        time.sleep(float(sleep_sec))

    run_dt, run_iso = parse_run_time(args.run_time)
    file_stamp = run_dt.strftime("%Y%m%dT%H%M%SZ")

    opensky_n, opensky_path = ingest_opensky(run_dt, file_stamp)
    meteo_n, past_days, meteo_path = ingest_openmeteo(run_dt, file_stamp)

    print(f"run_time:        {run_iso}")
    print(f"opensky:         {opensky_n} states -> {os.path.basename(opensky_path)}")
    print(f"openmeteo:       {meteo_n} hourly rows (past_days={past_days}) -> {os.path.basename(meteo_path)}")
    print("STATUS: COMPLETE")


if __name__ == "__main__":
    main()