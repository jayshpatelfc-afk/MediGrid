import csv
import sqlite3
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "db" / "medigrid.db"
DATA_DIR = BASE_DIR / "data"

SCHEMA = """
CREATE TABLE IF NOT EXISTS source_imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name TEXT NOT NULL,
    file_name TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    row_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS his_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_import_id INTEGER NOT NULL,
    raw_row TEXT NOT NULL,
    patient_id TEXT NOT NULL,
    admission_at TEXT,
    discharge_at TEXT,
    ward_raw TEXT,
    ward_normalized TEXT,
    department TEXT,
    age INTEGER,
    gender TEXT
);
CREATE TABLE IF NOT EXISTS lab_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_import_id INTEGER NOT NULL,
    raw_row TEXT NOT NULL,
    order_id TEXT NOT NULL,
    patient_id TEXT NOT NULL,
    test_name TEXT,
    ordered_at TEXT,
    collected_at TEXT,
    resulted_at TEXT,
    priority TEXT,
    department_raw TEXT,
    department_normalized TEXT
);
CREATE TABLE IF NOT EXISTS bed_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_import_id INTEGER NOT NULL,
    raw_row TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    ward_raw TEXT,
    ward_normalized TEXT,
    total_beds INTEGER,
    occupied INTEGER,
    available INTEGER,
    remarks TEXT
);
CREATE INDEX IF NOT EXISTS idx_his_patient ON his_events(patient_id);
CREATE INDEX IF NOT EXISTS idx_lab_patient ON lab_orders(patient_id);
CREATE INDEX IF NOT EXISTS idx_bed_date ON bed_snapshots(snapshot_date);
CREATE TABLE IF NOT EXISTS conflict_reviews (
    conflict_id TEXT PRIMARY KEY,
    reviewed INTEGER NOT NULL DEFAULT 0,
    reviewed_at TEXT NOT NULL
);
"""


def clean(value):
    return (value or "").strip()


def parse_date(value, formats):
    value = clean(value)
    if not value:
        return None
    for date_format in formats:
        try:
            return datetime.strptime(value, date_format).isoformat(sep=" ")
        except ValueError:
            continue
    return value


def normalize_patient_id(value):
    value = clean(value)
    digits = value.replace("MCH-", "")
    return f"MCH-{int(digits):07d}" if digits.isdigit() else value


def normalize_ward(value):
    value = clean(value).lower().replace(".", "")
    aliases = {
        "icu": "ICU",
        "micu": "MICU",
        "medical icu": "MICU",
        "gen ward a": "General Ward A",
        "general ward - a": "General Ward A",
        "general ward a": "General Ward A",
        "gen ward b": "General Ward B",
        "general ward b": "General Ward B",
        "general ward - b": "General Ward B",
        "paediatrics": "Pediatrics",
        "pediatrics": "Pediatrics",
    }
    return aliases.get(value, clean(value))


def import_csv(conn, source_name, path, importer):
    imported_at = datetime.now().isoformat(timespec="seconds")
    with path.open(newline="", encoding="utf-8") as csv_file:
        rows = list(csv.DictReader(csv_file))
    cursor = conn.execute(
        "INSERT INTO source_imports (source_name, file_name, imported_at, row_count) VALUES (?, ?, ?, ?)",
        (source_name, path.name, imported_at, len(rows)),
    )
    importer(conn, cursor.lastrowid, rows)
    return len(rows)


def import_his(conn, import_id, rows):
    for row in rows:
        conn.execute(
            "INSERT INTO his_events (source_import_id, raw_row, patient_id, admission_at, discharge_at, ward_raw, ward_normalized, department, age, gender) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (import_id, repr(row), normalize_patient_id(row["patient_id"]), parse_date(row["admission_datetime"], ["%Y-%m-%d %H:%M:%S"]), parse_date(row["discharge_datetime"], ["%Y-%m-%d %H:%M:%S"]), clean(row["ward"]), normalize_ward(row["ward"]), clean(row["admitting_department"]), int(row["age"]) if clean(row["age"]).isdigit() else None, clean(row["gender"]).upper()),
        )


def import_lab(conn, import_id, rows):
    for row in rows:
        conn.execute(
            "INSERT INTO lab_orders (source_import_id, raw_row, order_id, patient_id, test_name, ordered_at, collected_at, resulted_at, priority, department_raw, department_normalized) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (import_id, repr(row), clean(row["order_id"]), normalize_patient_id(row["patient_id"]), clean(row["test_name"]), parse_date(row["ordered_at"], ["%d/%m/%Y %H:%M"]), parse_date(row["collected_at"], ["%d/%m/%Y %H:%M"]), parse_date(row["resulted_at"], ["%d/%m/%Y %H:%M"]), clean(row["priority"]).upper(), clean(row["department"]), normalize_ward(row["department"])),
        )


def import_beds(conn, import_id, rows):
    for row in rows:
        available = clean(row["Available"])
        conn.execute(
            "INSERT INTO bed_snapshots (source_import_id, raw_row, snapshot_date, ward_raw, ward_normalized, total_beds, occupied, available, remarks) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (import_id, repr(row), parse_date(row["Date"], ["%d-%b-%y"]), clean(row["Ward"]), normalize_ward(row["Ward"]), int(row["Total Beds"]), int(row["Occupied"]) if clean(row["Occupied"]).isdigit() else None, int(available) if available.isdigit() else None, clean(row["Remarks"])),
        )


def detect_conflicts(conn):
    """Create review items from source comparisons without deleting source rows."""
    conflicts = []
    missing_admissions = conn.execute("SELECT l.patient_id, l.order_id FROM lab_orders l LEFT JOIN his_events h ON h.patient_id = l.patient_id WHERE h.patient_id IS NULL GROUP BY l.patient_id, l.order_id ORDER BY l.order_id").fetchall()
    for row in missing_admissions:
        conflicts.append({"id": f"orphan-lab-{row['order_id']}", "type": "LAB RESULTS", "title": f"Patient {row['patient_id']} · {row['order_id']}", "detail": "Lab order has no matching patient in the HIS admissions export.", "sources": [f"LAB: {row['order_id']}", "HIS: No admission record"], "value": "Hold for patient match", "note": "Do not include in cross-source patient metrics", "confidence": "LOW", "confidenceClass": "low", "occurrences": 1})
    blank_beds = conn.execute("SELECT snapshot_date, ward_raw, total_beds, occupied FROM bed_snapshots WHERE available IS NULL ORDER BY snapshot_date, id").fetchall()
    for row in blank_beds:
        conflicts.append({"id": f"blank-bed-{row['snapshot_date']}-{row['ward_raw']}", "type": "OCCUPANCY", "title": f"{row['ward_raw']} · {row['snapshot_date'][:10]}", "detail": "The manual occupancy row has no Available value.", "sources": [f"BED: {row['occupied']} occupied", "BED: Available is blank"], "value": row['total_beds'] - row['occupied'], "note": "Derived from total beds minus occupied; source blank preserved", "confidence": "MEDIUM", "confidenceClass": "medium", "occurrences": 1})
    arithmetic_mismatches = conn.execute("SELECT snapshot_date, ward_raw, total_beds, occupied, available FROM bed_snapshots WHERE available IS NOT NULL AND available != total_beds - occupied ORDER BY snapshot_date, id").fetchall()
    for row in arithmetic_mismatches:
        conflicts.append({"id": f"bed-math-{row['snapshot_date']}-{row['ward_raw']}", "type": "OCCUPANCY", "title": f"{row['ward_raw']} · {row['snapshot_date'][:10]}", "detail": "Reported available beds do not equal total beds minus occupied beds.", "sources": [f"BED: {row['available']} available", f"CALCULATED: {row['total_beds'] - row['occupied']} available"], "value": row['total_beds'] - row['occupied'], "note": "Calculated value used; reported value remains visible", "confidence": "MEDIUM", "confidenceClass": "medium", "occurrences": 1})
    latest_date = conn.execute("SELECT MAX(snapshot_date) AS snapshot_date FROM bed_snapshots").fetchone()["snapshot_date"]
    census_mismatches = conn.execute(
        "SELECT b.ward_raw, b.ward_normalized, b.occupied, "
        "(SELECT COUNT(*) FROM his_events h WHERE h.ward_normalized = b.ward_normalized "
        "AND date(h.admission_at) <= date(b.snapshot_date) "
        "AND (h.discharge_at IS NULL OR date(h.discharge_at) > date(b.snapshot_date))) AS his_active "
        "FROM bed_snapshots b WHERE b.snapshot_date = ? "
        "AND b.occupied != his_active ORDER BY b.ward_normalized",
        (latest_date,),
    ).fetchall()
    for row in census_mismatches:
        conflicts.append({"id": f"census-{latest_date}-{row['ward_normalized']}", "type": "OCCUPANCY", "title": f"{row['ward_raw']} · occupancy reconciliation", "detail": "The latest bed sheet and HIS active census report different occupied counts for the same normalized ward.", "sources": [f"BED: {row['occupied']} occupied", f"HIS: {row['his_active']} active patients"], "value": row['his_active'], "note": "HIS active census is the working comparison; manual sheet remains visible", "confidence": "LOW", "confidenceClass": "low", "occurrences": 1})
    duplicate_orders = conn.execute("SELECT order_id, patient_id, COUNT(*) AS occurrences FROM lab_orders GROUP BY order_id, patient_id HAVING COUNT(*) > 1 ORDER BY order_id").fetchall()
    for row in duplicate_orders:
        conflicts.append({"id": f"duplicate-lab-{row['order_id']}", "type": "LAB RESULTS", "title": f"Patient {row['patient_id']} · {row['order_id']}", "detail": "Multiple lab rows share one order ID and must be resolved before reporting turnaround.", "sources": [f"LAB: {row['occurrences']} rows", f"LAB: order {row['order_id']}"], "value": "Latest verified result", "note": "Retain every source row; use the latest verified result for the working metric", "confidence": "MEDIUM", "confidenceClass": "medium", "occurrences": row['occurrences']})
    rule_map = {
        "orphan-lab-": ("LAB-01", "Unmatched lab rows are held out of cross-source metrics"),
        "blank-bed-": ("BED-01", "Derive Available as Total Beds minus Occupied"),
        "bed-math-": ("BED-02", "Calculated availability overrides an inconsistent reported value"),
        "census-": ("BED-03", "Use HIS active census as the comparison value; retain the bed sheet"),
        "duplicate-lab-": ("LAB-02", "Use the latest verified result; retain every duplicate row"),
    }
    for conflict in conflicts:
        conflict["ruleId"], conflict["ruleName"] = next(
            (rule for prefix, rule in rule_map.items() if conflict["id"].startswith(prefix)),
            ("REVIEW-01", "Keep both source values and require human confirmation"),
        )
    return conflicts


def initialize_database(force=False):
    if force and DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM source_imports").fetchone()[0] == 0:
        import_csv(conn, "HIS admissions/discharges", DATA_DIR / "his_admissions_discharges.csv", import_his)
        import_csv(conn, "Lab order-to-result", DATA_DIR / "lab_order_to_result.csv", import_lab)
        import_csv(conn, "Manual bed occupancy", DATA_DIR / "bed_occupancy_manual.csv", import_beds)
        conn.commit()
    return conn


if __name__ == "__main__":
    connection = initialize_database(force=True)
    print({
        "database": str(DB_PATH),
        "his_rows": connection.execute("SELECT COUNT(*) FROM his_events").fetchone()[0],
        "lab_rows": connection.execute("SELECT COUNT(*) FROM lab_orders").fetchone()[0],
        "bed_rows": connection.execute("SELECT COUNT(*) FROM bed_snapshots").fetchone()[0],
        "blank_bed_available": connection.execute("SELECT COUNT(*) FROM bed_snapshots WHERE available IS NULL").fetchone()[0],
    })
    connection.close()
