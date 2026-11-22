import os
import sys
import uuid
import argparse
import subprocess

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, BASE_DIR)

from src.db import connect, parse_run_time, wall_clock_utc, PIPELINE_LOG_DB_PATH

LOCK_PATH = os.path.join(BASE_DIR, "data", "pipeline.lock")

# Adım bazlı varsayılan zaman aşımları (saniye)
STEP_TIMEOUTS = {
    "src/ingest.py": 120,
    "src/load.py": 180,
    "src/clean.py": 180,
    "src/validate.py": 60,
}

PIPELINE_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS pipeline_run (
    run_id TEXT NOT NULL,
    run_time TEXT NOT NULL,
    step TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    exit_code INTEGER NOT NULL,
    detail TEXT,
    PRIMARY KEY (run_id, step)
) STRICT;
"""


def init_log_db():
    """Ayrı pipeline_log.db veritabanında pipeline_run tablosunu oluşturur."""
    con = connect(PIPELINE_LOG_DB_PATH, allow_create=True)
    try:
        con.executescript(PIPELINE_LOG_SCHEMA)
    finally:
        con.close()


def record_step(run_id, run_time, step, started_at, finished_at, exit_code, detail):
    """Çalıştırılan her adımı parametreli SQL ile pipeline_run tablosuna yazar."""
    con = connect(PIPELINE_LOG_DB_PATH, allow_create=True)
    try:
        with con:
            con.execute("""
                INSERT INTO pipeline_run (run_id, run_time, step, started_at, finished_at, exit_code, detail)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (run_id, run_time, step, started_at, finished_at, exit_code, detail))
    finally:
        con.close()


def acquire_lock():
    """
    Atomik kilit dosyası (O_CREAT | O_EXCL) oluşturur.
    Başka bir çalışma devam ediyorsa mesaj basıp çıkış kodu 75 ile çıkar.
    """
    os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
    try:
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"pid={os.getpid()} started_at={wall_clock_utc()}\n")
    except FileExistsError:
        print(f"PIPELINE ALREADY RUNNING: Lock file exists at {LOCK_PATH}. Exiting with code 75.", file=sys.stderr)
        sys.exit(75)


def release_lock():
    """Kilit dosyasını güvenli biçimde kaldırır."""
    if os.path.exists(LOCK_PATH):
        try:
            os.remove(LOCK_PATH)
        except OSError:
            pass


def extract_detail(stdout_text, stderr_text, exit_code):
    """Adım çıktısından log için kısa özet satırı çıkarır."""
    lines = [ln.strip() for ln in (stdout_text or "").splitlines() if ln.strip()]
    err_lines = [ln.strip() for ln in (stderr_text or "").splitlines() if ln.strip()]

    if exit_code != 0:
        if err_lines:
            return err_lines[-1][:240]
        if lines:
            return lines[-1][:240]
        return f"Exited with code {exit_code}"

    # Başarılı adımlar için anlamlı özet satırları topla
    highlights = []
    for ln in lines:
        if any(k in ln for k in ("opensky:", "openmeteo:", "rows_added:", "elapsed_sec:", "WARN ", "VALIDATION SUCCESS")):
            highlights.append(ln)
    if highlights:
        return " | ".join(highlights)[:300]
    return lines[-1][:240] if lines else "OK"


def main():
    parser = argparse.ArgumentParser(description="M5 Automated Pipeline Runner (ingest -> load -> clean -> validate)")
    parser.add_argument("--run-time", dest="run_time", default=None, help="Pipeline clock in ISO 8601 UTC (default: now)")
    parser.add_argument("--skip-ingest", action="store_true", help="Skip ingestion and rebuild from landed raw files")
    args = parser.parse_args()

    # 1. Kilit al (başka bir çalışma varsa 75 ile çıkar ve hiçbir adım çalıştırmaz)
    acquire_lock()

    run_id = uuid.uuid4().hex[:8]
    _, run_iso = parse_run_time(args.run_time)

    steps = ["src/load.py", "src/clean.py", "src/validate.py"]
    if not args.skip_ingest:
        steps.insert(0, "src/ingest.py")

    # Opsiyonel timeout drill override'ı (varsayılan: kapalı)
    timeout_override = os.getenv("PIPELINE_STEP_TIMEOUT_SEC")

    try:
        init_log_db()
        print(f"=== PIPELINE RUN START | run_id={run_id} | run_time={run_iso} | skip_ingest={args.skip_ingest} ===")

        for step in steps:
            step_path = os.path.join(BASE_DIR, step)
            timeout_sec = float(timeout_override) if timeout_override else STEP_TIMEOUTS[step]
            cmd = [sys.executable, step_path, "--run-time", run_iso]

            print(f"\n--- Running {step} (timeout={timeout_sec}s) ---")
            started_at = wall_clock_utc()

            try:
                res = subprocess.run(
                    cmd,
                    cwd=BASE_DIR,
                    capture_output=True,
                    text=True,
                    timeout=timeout_sec
                )
                finished_at = wall_clock_utc()
                exit_code = res.returncode

                if res.stdout:
                    print(res.stdout, end="")
                if res.stderr:
                    print(res.stderr, end="", file=sys.stderr)

                detail = extract_detail(res.stdout, res.stderr, exit_code)
                record_step(run_id, run_iso, step, started_at, finished_at, exit_code, detail)

                if exit_code != 0:
                    print(f"\nPIPELINE FAILED: run_id={run_id} stopped at {step} with exit code {exit_code}.")
                    sys.exit(exit_code)

            except subprocess.TimeoutExpired as exc:
                finished_at = wall_clock_utc()
                exit_code = 124
                detail = f"TIMEOUT after {timeout_sec}s"
                if exc.stdout:
                    print(exc.stdout, end="")
                if exc.stderr:
                    print(exc.stderr, end="", file=sys.stderr)
                print(f"ERROR [{step}]: {detail}", file=sys.stderr)
                record_step(run_id, run_iso, step, started_at, finished_at, exit_code, detail)
                print(f"\nPIPELINE FAILED: run_id={run_id} stopped at {step} with exit code 124 (timeout).")
                sys.exit(124)

        print(f"\nPIPELINE SUCCEEDED: run_id={run_id} completed all {len(steps)} steps (run_time={run_iso}).")

    finally:
        release_lock()


if __name__ == "__main__":
    main()