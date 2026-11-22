import os
import sqlite3
from datetime import datetime, timezone

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "data", "aviation.db")
PIPELINE_LOG_DB_PATH = os.path.join(BASE_DIR, "data", "pipeline_log.db")


def connect(db_path=DEFAULT_DB_PATH, allow_create=False, read_only=False):
    """
    Veritabanına bağlanır ve PRAGMA foreign_keys = ON ayarını uygular.
    M5 Kural 2.3: allow_create=False (varsayılan) iken dosya yoksa sessizce boş DB oluşturmaz.
    """
    if read_only:
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"Veritabanı bulunamadı (read-only): {db_path}")
        abs_path = os.path.abspath(db_path).replace("\\", "/")
        con = sqlite3.connect(f"file:{abs_path}?mode=ro", uri=True)
    else:
        if not allow_create and not os.path.exists(db_path):
            raise FileNotFoundError(
                f"Veritabanı bulunamadı: {db_path}. Önce 'python src/load.py' çalıştırın."
            )
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        con = sqlite3.connect(db_path)

    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def parse_run_time(run_time_str=None):
    """
    --run-time parametresini ISO 8601 UTC datetime nesnesine ve standart metne çevirir.
    Parametre verilmezse o anın UTC saatini döndürür.
    """
    if not run_time_str:
        dt = datetime.now(timezone.utc)
    else:
        clean_str = run_time_str.strip()
        if clean_str.endswith("Z"):
            clean_str = clean_str[:-1] + "+00:00"
        dt = datetime.fromisoformat(clean_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
    iso_str = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return dt, iso_str


def wall_clock_utc():
    """Sadece log zaman damgaları (started_at, finished_at) için wall clock UTC üretir."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")