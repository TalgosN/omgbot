"""One-off backfill of shifts from the legacy Shifton API."""

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(_path):
        return False


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "db" / "omgbot.sql"
DEFAULT_START = "2026-07-13"
DEFAULT_END = "2026-07-19"
PROJECT_ID = 17253
COMPANY_ID = 16303
SOURCE = "legacy_shifton"


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=DEFAULT_START, help="First date, YYYY-MM-DD")
    parser.add_argument("--end", default=DEFAULT_END, help="Last date, YYYY-MM-DD (inclusive)")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--credentials-from-dump",
        type=Path,
        help="Read the four legacy credentials from an ignored old rasp.py file",
    )
    parser.add_argument("--apply", action="store_true", help="Write rows to SQLite; default is dry-run")
    parser.add_argument("--sync-sheets", action="store_true", help="Refresh KPI sheets after --apply")
    return parser.parse_args()


def validate_period(start, end):
    start_dt = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")
    if start_dt > end_dt:
        raise ValueError("Start date must not be after end date")
    return start_dt, end_dt


def credentials_from_dump(path):
    text = path.read_text(encoding="utf-8")
    credentials = {}
    for name in ("username", "password", "client_id", "client_secret"):
        match = re.search(rf'["\']{name}["\']\s*:\s*["\']([^"\']+)["\']', text)
        if not match:
            raise RuntimeError(f"Could not find {name} in {path}")
        credentials[name] = match.group(1)
    return credentials


def get_credentials(dump_path=None):
    load_dotenv(ROOT / ".env")
    credentials = {
        "username": os.getenv("SHIFTON_USER"),
        "password": os.getenv("SHIFTON_PASS"),
        "client_id": os.getenv("SHIFTON_CLIENT_ID"),
        "client_secret": os.getenv("SHIFTON_CLIENT_SECRET"),
    }
    if all(credentials.values()):
        return credentials
    if dump_path:
        return credentials_from_dump(dump_path)
    missing = [name for name, value in credentials.items() if not value]
    raise RuntimeError(
        "Missing legacy Shifton settings: " + ", ".join(missing)
        + ". Fill SHIFTON_USER/PASS/CLIENT_ID/CLIENT_SECRET in .env or use --credentials-from-dump."
    )


def request_json(method, url, **kwargs):
    response = requests.request(method, url, timeout=20, **kwargs)
    response.raise_for_status()
    data = response.json()
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(f"Legacy Shifton API error: {data['error']}")
    return data


def fetch_legacy_shifts(start, end, credentials):
    token = request_json(
        "POST",
        "https://api2.shifton.com/oauth/token",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        data=json.dumps({**credentials, "grant_type": "password", "scope": ""}),
    )
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token['access_token']}",
        "refresh_token": token["refresh_token"],
    }
    period = json.dumps({"start": f"{start} 00:00:00", "end": f"{end} 23:59:59"})
    shifts = request_json(
        "GET",
        f"https://api.shifton.com/work/1.0.0/projects/{PROJECT_ID}/shifts",
        headers=headers,
        data=period,
    )
    employees = request_json(
        "GET",
        f"https://api2.shifton.com/work/1.0.0/companies/{COMPANY_ID}/employees",
        headers=headers,
    )
    if not isinstance(shifts, list) or not isinstance(employees, list):
        raise RuntimeError("Unexpected response format from legacy Shifton API")
    return shifts, employees


def prepare_rows(shifts, employees, start, end):
    employee_names = {employee["id"]: employee.get("full_name", "") for employee in employees}
    rows = []
    skipped = 0
    for shift in shifts:
        name = employee_names.get(shift.get("employee_id"), "").split()
        location = shift.get("location")
        if len(name) < 2 or not location or not location.get("title"):
            skipped += 1
            continue
        planned_from = datetime.strptime(shift["planned_from"], "%Y-%m-%d %H:%M:%S")
        planned_to = datetime.strptime(shift["planned_to"], "%Y-%m-%d %H:%M:%S")
        date_iso = planned_from.strftime("%Y-%m-%d")
        if not start <= date_iso <= end:
            continue
        duration = round(abs((planned_to - planned_from).total_seconds()) / 3600, 1)
        rows.append((name[0], name[1], date_iso, location["title"], duration, SOURCE))
    return list(dict.fromkeys(rows)), skipped


def ensure_source_column(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS shifts ("
        "shift_second_name varchar(50), shift_first_name varchar(50), dt_shift date, "
        "club varchar(50), dur REAL, source varchar(30))"
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(shifts)")}
    if "source" not in columns:
        conn.execute("ALTER TABLE shifts ADD COLUMN source varchar(30)")


def apply_rows(db_path, rows, start, end):
    conn = sqlite3.connect(db_path)
    try:
        ensure_source_column(conn)
        conn.execute(
            "DELETE FROM shifts WHERE date(dt_shift) BETWEEN ? AND ? AND source = ?",
            (start, end, SOURCE),
        )
        inserted = 0
        for row in rows:
            duplicate = conn.execute(
                "SELECT 1 FROM shifts WHERE shift_second_name = ? AND shift_first_name = ? "
                "AND date(dt_shift) = ? AND club = ? AND dur = ? LIMIT 1",
                row[:5],
            ).fetchone()
            if not duplicate:
                conn.execute(
                    "INSERT INTO shifts "
                    "(shift_second_name, shift_first_name, dt_shift, club, dur, source) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    row,
                )
                inserted += 1
        conn.commit()
        return inserted
    finally:
        conn.close()


def sync_sheets():
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    import kpi
    import sql_scripts

    kpi.write_data(kpi.sql_select(sql_scripts.shifts_ext), "KPI helper", "shifts")
    kpi.write_data(kpi.sql_select(sql_scripts.shifts), "KPI OMG VR", "shifts")
    kpi.write_data(kpi.sql_select(sql_scripts.records), "KPI OMG VR", "raw")


def main():
    args = parse_args()
    validate_period(args.start, args.end)
    if args.sync_sheets and not args.apply:
        raise RuntimeError("--sync-sheets requires --apply")

    credentials = get_credentials(args.credentials_from_dump)
    shifts, employees = fetch_legacy_shifts(args.start, args.end, credentials)
    rows, skipped = prepare_rows(shifts, employees, args.start, args.end)

    counts = {}
    for row in rows:
        counts[row[2]] = counts.get(row[2], 0) + 1
    print(f"Legacy Shifton returned {len(rows)} usable shifts; skipped {skipped} incomplete rows.")
    for day, count in sorted(counts.items()):
        print(f"  {day}: {count}")

    if not args.apply:
        print("Dry-run only: database and Google Sheets were not changed.")
        return

    inserted = apply_rows(args.db, rows, args.start, args.end)
    print(f"Inserted {inserted} shifts into {args.db} with source={SOURCE}.")
    if args.sync_sheets:
        sync_sheets()
        print("KPI Google Sheets refreshed.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Backfill failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
