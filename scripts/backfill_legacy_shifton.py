"""One-off backfill of shifts from the legacy Shifton API."""

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta
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
    employees = request_json(
        "GET",
        f"https://api2.shifton.com/work/1.0.0/companies/{COMPANY_ID}/employees",
        headers=headers,
    )
    if not isinstance(employees, list):
        raise RuntimeError("Unexpected response format from legacy Shifton API")

    start_dt, end_dt = validate_period(start, end)
    shifts = []
    chunk_start = start_dt
    while chunk_start <= end_dt:
        chunk_end = min(chunk_start + timedelta(days=30), end_dt)
        range_params = {
            "start": f"{chunk_start:%Y-%m-%d} 00:00:00",
            "end": f"{chunk_end:%Y-%m-%d} 23:59:59",
        }
        period = json.dumps(range_params)
        chunk = request_json(
            "GET",
            f"https://api.shifton.com/work/1.0.0/projects/{PROJECT_ID}/shifts",
            headers=headers,
            data=period,
        )
        if not isinstance(chunk, list):
            raise RuntimeError("Unexpected response format from legacy Shifton API")
        shifts.extend(chunk)
        try:
            deleted_employees = request_json(
                "GET",
                f"https://api.shifton.com/work/1.0.0/companies/{COMPANY_ID}/"
                f"projects/{PROJECT_ID}/employees/deleted",
                headers=headers,
                params=range_params,
            )
        except (requests.RequestException, RuntimeError):
            deleted_employees = []
        known_ids = {employee.get("id") for employee in employees}
        for employee in deleted_employees if isinstance(deleted_employees, list) else []:
            if employee.get("id") not in known_ids:
                employees.append(employee)
                known_ids.add(employee.get("id"))
        chunk_start = chunk_end + timedelta(days=1)

    known_employee_ids = {employee.get("id") for employee in employees}
    missing_employee_ids = sorted({
        shift.get("employee_id")
        for shift in shifts
        if shift.get("employee_id")
        and shift.get("employee_id") not in known_employee_ids
    })
    for employee_id in missing_employee_ids:
        try:
            employee = request_json(
                "GET",
                f"https://api.shifton.com/work/1.0.0/companies/"
                f"{COMPANY_ID}/employees/{employee_id}",
                headers=headers,
            )
        except (requests.RequestException, RuntimeError):
            continue
        if isinstance(employee, dict) and employee.get("full_name"):
            employees.append(employee)
    return shifts, employees


def prepare_rows(shifts, employees, start, end):
    employee_names = {employee["id"]: employee.get("full_name", "") for employee in employees}
    rows = []
    seen_shifts = set()
    skipped = 0
    for shift in shifts:
        shift_key = shift.get("id") or (
            shift.get("employee_id"),
            shift.get("planned_from"),
            shift.get("planned_to"),
            (shift.get("location") or {}).get("title"),
        )
        if shift_key in seen_shifts:
            continue
        seen_shifts.add(shift_key)
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
    return rows, skipped


def ensure_source_column(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS shifts ("
        "shift_second_name varchar(50), shift_first_name varchar(50), dt_shift date, "
        "club varchar(50), dur REAL, source varchar(30), shift_login varchar(50))"
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(shifts)")}
    if "source" not in columns:
        conn.execute("ALTER TABLE shifts ADD COLUMN source varchar(30)")
    if "shift_login" not in columns:
        conn.execute("ALTER TABLE shifts ADD COLUMN shift_login varchar(50)")


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
                "AND date(dt_shift) = ? AND club = ? AND dur = ? "
                "AND COALESCE(source, '') <> ? LIMIT 1",
                (*row[:5], SOURCE),
            ).fetchone()
            if not duplicate:
                conn.execute(
                    "INSERT INTO shifts "
                    "(shift_second_name, shift_first_name, dt_shift, club, dur, source) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    row,
                )
                inserted += 1
        users_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users_new'"
        ).fetchone()
        conn.execute(
            """UPDATE shifts AS target
               SET shift_login = (
                   SELECT linked.shift_login FROM shifts AS linked
                   WHERE linked.shift_second_name = target.shift_second_name
                     AND linked.shift_first_name = target.shift_first_name
                     AND linked.shift_login IS NOT NULL
                   LIMIT 1
               )
               WHERE target.source = ? AND target.shift_login IS NULL""",
            (SOURCE,),
        )
        if users_table:
            conn.execute(
                """UPDATE shifts
                   SET shift_login = (
                       SELECT login FROM users_new
                       WHERE second_name = shifts.shift_second_name
                         AND first_name = shifts.shift_first_name
                       LIMIT 1
                   )
                   WHERE shift_login IS NULL"""
            )
        conn.commit()
        return inserted
    finally:
        conn.close()


def sync_sheets():
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    import kpi
    import sql_scripts

    kpi.write_data(kpi.sql_select(sql_scripts.sheets_shifts_ext), "KPI helper", "shifts")
    kpi.write_data(kpi.sql_select(sql_scripts.sheets_union), "KPI OMG VR", "data")
    kpi.write_data(kpi.sql_select(sql_scripts.sheets_shifts), "KPI OMG VR", "shifts")
    kpi.write_data(kpi.sql_select(sql_scripts.sheets_records), "KPI OMG VR", "raw")


def get_unlinked_legacy_shifts(db_path, start, end):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            """SELECT shift_second_name, shift_first_name, COUNT(*)
               FROM shifts
               WHERE source = ? AND shift_login IS NULL
                 AND date(substr(dt_shift, 1, 10)) BETWEEN date(?) AND date(?)
               GROUP BY shift_second_name, shift_first_name
               ORDER BY COUNT(*) DESC, shift_second_name, shift_first_name""",
            (SOURCE, start, end),
        ).fetchall()
    finally:
        conn.close()


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
    if counts:
        days = sorted(counts)
        print(f"Dates with shifts: {len(days)}; first={days[0]}; last={days[-1]}.")
        if len(days) <= 31:
            for day, count in sorted(counts.items()):
                print(f"  {day}: {count}")

    if not args.apply:
        print("Dry-run only: database and Google Sheets were not changed.")
        return

    inserted = apply_rows(args.db, rows, args.start, args.end)
    print(f"Inserted {inserted} shifts into {args.db} with source={SOURCE}.")
    unlinked = get_unlinked_legacy_shifts(args.db, args.start, args.end)
    if unlinked:
        print(f"Warning: {sum(row[2] for row in unlinked)} shifts are not linked to users_new:")
        for second_name, first_name, count in unlinked:
            print(f"  {second_name} {first_name}: {count}")
    else:
        print("All imported shifts are linked to users_new.")
    if args.sync_sheets:
        sync_sheets()
        print("KPI Google Sheets refreshed.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Backfill failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
