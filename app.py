import os
from datetime import datetime

from flask import Flask, jsonify, render_template, request

from database import detect_conflicts, initialize_database


def create_app():
    app = Flask(__name__, static_folder="static", template_folder="templates")
    db = initialize_database()
    app.config["DB"] = db
    return app


app = create_app()
db = app.config["DB"]

conflicts = [
    {
        "id": "admission-004821",
        "type": "ADMISSIONS",
        "title": "J. Smith · MRN 004821",
        "detail": "Admission date is different between the HIS export and the manual occupancy sheet.",
        "sources": ["HIS: 13 Nov, 19:42", "BED: 14 Nov, 07:00"],
        "value": "13 Nov, 19:42",
        "note": "HIS timestamp takes precedence",
        "confidence": "MEDIUM",
        "confidenceClass": "medium",
        "reviewed": False,
    },
    {
        "id": "discharge-003109",
        "type": "DISCHARGES",
        "title": "R. Ndlovu · MRN 003109",
        "detail": "Manual sheet shows a discharge that is not present in the HIS event export.",
        "sources": ["HIS: No event", "BED: 14 Nov, 06:30"],
        "value": "Pending confirmation",
        "note": "Held out of discharge count",
        "confidence": "LOW",
        "confidenceClass": "low",
        "reviewed": False,
    },
    {
        "id": "lab-006774",
        "type": "LAB RESULTS",
        "title": "K. Lee · MRN 006774",
        "detail": "Two results share one order ID. Both rows remain visible for audit.",
        "sources": ["LAB: 07:54 unverified", "LAB: 08:16 verified"],
        "value": "08:16 verified result",
        "note": "Latest verified result retained",
        "confidence": "MEDIUM",
        "confidenceClass": "medium",
        "reviewed": False,
    },
    {
        "id": "occupancy-medical",
        "type": "OCCUPANCY",
        "title": "Medical ward · 8 beds",
        "detail": "The occupancy sheet is one day behind the movement export for this unit.",
        "sources": ["HIS: 74 occupied", "BED: 72 occupied"],
        "value": "74 occupied",
        "note": "Latest movement data used; sheet flagged stale",
        "confidence": "LOW",
        "confidenceClass": "low",
        "reviewed": False,
    },
]

sources = [
    {"id": "lab", "name": "Lab turnaround log", "status": "Healthy", "rows": 642, "freshness": "08:38", "completeness": "99.4% complete"},
    {"id": "bed", "name": "Manual bed occupancy sheet", "status": "Watch", "rows": 42, "freshness": "Yesterday", "completeness": "1 day behind"},
]
def public_conflict(item):
    return dict(item)


def conflict_reviewed(conflict_id):
    row = db.execute("SELECT reviewed FROM conflict_reviews WHERE conflict_id = ?", (conflict_id,)).fetchone()
    return bool(row["reviewed"]) if row else False


def latest_snapshot_date():
    return db.execute("SELECT MAX(snapshot_date) AS snapshot_date FROM bed_snapshots").fetchone()["snapshot_date"]


def snapshot_categories(snapshot_date):
    rows = db.execute(
        "SELECT ward_normalized, SUM(total_beds) AS total, SUM(occupied) AS occupied, "
        "SUM(COALESCE(available, total_beds - occupied)) AS available "
        "FROM bed_snapshots WHERE snapshot_date = ? GROUP BY ward_normalized ORDER BY ward_normalized",
        (snapshot_date,),
    ).fetchall()
    class_names = {"ICU": "icu", "MICU": "icu", "Pediatrics": "peds", "General Ward A": "general", "General Ward B": "general"}
    return [
        {"name": row["ward_normalized"], "total": row["total"], "vacant": row["available"] or 0, "className": class_names.get(row["ward_normalized"], "general")}
        for row in rows
    ]


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "sourcesLoaded": 3})


@app.get("/api/import-summary")
def import_summary():
    imports = db.execute("SELECT source_name, file_name, imported_at, row_count FROM source_imports ORDER BY id").fetchall()
    return jsonify({
        "database": "medigrid.db",
        "sources": [dict(row) for row in imports],
        "normalization": {
            "patientIds": "HIS-prefixed and bare LAB IDs are stored as MCH-0000000",
            "wards": "Source ward names are retained and normalized for matching",
            "blankValues": "Blank source values remain NULL and are not dropped",
        },
    })


@app.get("/api/bed-types")
def get_bed_types():
    snapshot_date = latest_snapshot_date()
    totals = db.execute(
        "SELECT SUM(total_beds) AS total, SUM(occupied) AS occupied, SUM(COALESCE(available, total_beds - occupied)) AS available "
        "FROM bed_snapshots WHERE snapshot_date = ?", (snapshot_date,)
    ).fetchone()
    return jsonify({"reportingDate": snapshot_date[:10], "totalBeds": totals["total"] or 0, "occupiedBeds": totals["occupied"] or 0, "vacantBeds": totals["available"] or 0, "categories": snapshot_categories(snapshot_date)})


@app.get("/api/conflicts")
def get_conflicts():
    detected = detect_conflicts(db)
    for item in detected:
        item["reviewed"] = conflict_reviewed(item["id"])
    return jsonify({
        "openCount": sum(not item["reviewed"] for item in detected),
        "engine": "Rules-based reconciliation; source rows are preserved and working values are explicit.",
        "items": [public_conflict(item) for item in detected],
    })


@app.post("/api/conflicts/<conflict_id>/review")
def review_conflict(conflict_id):
    item = next((item for item in detect_conflicts(db) if item["id"] == conflict_id), None)
    if item is None:
        return jsonify({"error": "Conflict not found"}), 404
    payload = request.get_json(silent=True) or {}
    reviewed = bool(payload.get("reviewed", True))
    if os.getenv("DATABASE_URL"):
        db.execute(
            "INSERT INTO conflict_reviews (conflict_id, reviewed, reviewed_at) VALUES (%s, %s, CURRENT_TIMESTAMP) ON CONFLICT (conflict_id) DO UPDATE SET reviewed = EXCLUDED.reviewed, reviewed_at = EXCLUDED.reviewed_at",
            (conflict_id, int(reviewed)),
        )
    else:
        db.execute(
            "INSERT INTO conflict_reviews (conflict_id, reviewed, reviewed_at) VALUES (?, ?, datetime('now')) ON CONFLICT(conflict_id) DO UPDATE SET reviewed = excluded.reviewed, reviewed_at = excluded.reviewed_at",
            (conflict_id, int(reviewed)),
        )
    db.commit()
    item["reviewed"] = reviewed
    return jsonify(public_conflict(item))


@app.get("/api/sources")
def get_sources():
    imports = db.execute("SELECT source_name, row_count, imported_at FROM source_imports ORDER BY id").fetchall()
    source_data = []
    for source in imports:
        source_id = "his" if source["source_name"].startswith("HIS") else "lab" if source["source_name"].startswith("Lab") else "bed"
        source_data.append({"id": source_id, "name": source["source_name"], "status": "Watch" if source_id == "bed" else "Healthy", "rows": source["row_count"], "freshness": source["imported_at"], "completeness": "Source values preserved"})
    return jsonify({"loaded": len(source_data), "sources": source_data})


def diff_minutes_sql(end_col, start_col):
    if os.getenv("DATABASE_URL"):
        return f"AVG(EXTRACT(EPOCH FROM ({end_col}::timestamp - {start_col}::timestamp)) / 60.0)"
    return f"AVG((strftime('%s', {end_col}) - strftime('%s', {start_col})) / 60.0)"


def trust_metric(name, value, source, freshness, completeness, agreement, reliability, reasons):
    score = round((freshness * 0.20) + (completeness * 0.20) + (agreement * 0.35) + (reliability * 0.25))
    return {
        "name": name,
        "value": value,
        "score": score,
        "rating": "HIGH" if score >= 85 else "WATCH" if score >= 70 else "LOW",
        "source": source,
        "components": {
            "freshness": freshness,
            "completeness": completeness,
            "agreement": agreement,
            "sourceReliability": reliability,
        },
        "explanation": " ".join(reasons),
    }


@app.get("/api/trust-score")
def trust_score():
    latest_snapshot = latest_snapshot_date()
    latest_day = latest_snapshot[:10]
    bed_totals = db.execute(
        "SELECT SUM(total_beds) AS capacity, SUM(occupied) AS occupied, SUM(COALESCE(available, total_beds - occupied)) AS available "
        "FROM bed_snapshots WHERE snapshot_date = ?", (latest_snapshot,)
    ).fetchone()
    bed_rows = db.execute("SELECT COUNT(*) AS total, SUM(available IS NULL) AS blanks FROM bed_snapshots").fetchone()
    census_conflicts = sum(item["id"].startswith("census-") for item in detect_conflicts(db))
    bed_agreement = max(0, round(100 - (census_conflicts / max(1, len(snapshot_categories(latest_snapshot))) * 30)))
    lab_rows = db.execute("SELECT COUNT(*) AS total, SUM(ordered_at IS NULL OR resulted_at IS NULL) AS incomplete FROM lab_orders").fetchone()
    orphan_count = db.execute("SELECT COUNT(DISTINCT l.patient_id) AS value FROM lab_orders l LEFT JOIN his_events h ON h.patient_id = l.patient_id WHERE h.patient_id IS NULL").fetchone()["value"]
    lab_completeness = round(100 * (lab_rows["total"] - (lab_rows["incomplete"] or 0)) / max(1, lab_rows["total"]))
    lab_agreement = round(100 - (orphan_count / max(1, lab_rows["total"]) * 100))
    his_rows = db.execute("SELECT COUNT(*) AS total, SUM(admission_at IS NULL AND discharge_at IS NULL) AS incomplete FROM his_events").fetchone()
    his_completeness = round(100 * (his_rows["total"] - (his_rows["incomplete"] or 0)) / max(1, his_rows["total"]))
    return jsonify({
        "reportingDate": latest_day,
        "method": "Score = 20% freshness + 20% completeness + 35% cross-source agreement + 25% source reliability.",
        "metrics": [
            trust_metric("Bed availability", f"{bed_totals['available'] or 0} available of {bed_totals['capacity'] or 0}", "Manual bed occupancy + HIS census", 100, round(100 * (bed_rows["total"] - (bed_rows["blanks"] or 0)) / max(1, bed_rows["total"])), bed_agreement, 70, [f"Latest snapshot is {latest_day}.", f"{bed_rows['blanks'] or 0} of {bed_rows['total']} bed rows have a blank Available value.", f"{census_conflicts} ward census comparisons differ from HIS."]),
            trust_metric("Admissions", db.execute("SELECT COUNT(*) FROM his_events WHERE substr(admission_at, 1, 10) = ?", (latest_day,)).fetchone()[0], "HIS admissions/discharges", 100, his_completeness, 100, 95, [f"HIS provides the source-of-record event timestamp for {latest_day}.", f"{his_completeness}% of HIS event rows contain an admission or discharge timestamp."]),
            trust_metric("Discharges", db.execute("SELECT COUNT(*) FROM his_events WHERE substr(discharge_at, 1, 10) = ?", (latest_day,)).fetchone()[0], "HIS admissions/discharges", 100, his_completeness, 100, 95, [f"Discharge count is taken directly from HIS events on {latest_day}.", "Manual-only discharge claims remain in the reconciliation queue and are excluded."]),
            trust_metric("Lab turnaround", "Calculated from completed orders", "Lab order-to-result", 100, lab_completeness, lab_agreement, 90, [f"Turnaround uses ordered and resulted timestamps.", f"{orphan_count} lab patient IDs have no matching HIS admission and remain excluded from cross-source metrics."]),
        ],
    })


@app.get("/api/dashboard")
def dashboard():
    detected = detect_conflicts(db)
    latest_snapshot = latest_snapshot_date()
    bed_totals = db.execute(
        "SELECT SUM(total_beds) AS capacity, SUM(occupied) AS occupied, SUM(COALESCE(available, total_beds - occupied)) AS available FROM bed_snapshots WHERE snapshot_date = ?",
        (latest_snapshot,),
    ).fetchone()
    latest_day = latest_snapshot[:10]
    admissions = db.execute("SELECT COUNT(*) AS value FROM his_events WHERE substr(admission_at, 1, 10) = ?", (latest_day,)).fetchone()["value"]
    discharges = db.execute("SELECT COUNT(*) AS value FROM his_events WHERE substr(discharge_at, 1, 10) = ?", (latest_day,)).fetchone()["value"]
    lab_metrics = db.execute(
        "SELECT COUNT(*) AS total, SUM(resulted_at IS NULL) AS pending, AVG((julianday(resulted_at) - julianday(ordered_at)) * 24 * 60) AS avg_minutes FROM lab_orders WHERE resulted_at IS NOT NULL"
    ).fetchone()
    average_minutes = round(lab_metrics["avg_minutes"] or 0)
    trust_data = trust_score().get_json()
    trust_by_name = {metric["name"]: metric["score"] for metric in trust_data["metrics"]}
    open_conflicts = sum(not conflict_reviewed(item["id"]) for item in detected)
    alerts = []
    if (bed_totals["available"] or 0) < 10:
        alerts.append({"severity": "HIGH", "title": "Bed reserve is critically low", "detail": f"Only {bed_totals['available'] or 0} beds are available across the latest snapshot."})
    if lab_metrics["pending"]:
        alerts.append({"severity": "WATCH", "title": "Lab results are pending", "detail": f"{lab_metrics['pending']} lab orders have no result timestamp."})
    if open_conflicts:
        alerts.append({"severity": "WATCH", "title": "Reconciliation needs review", "detail": f"{open_conflicts} source conflicts remain open; working values are still available."})
    return jsonify({
        "reportingDate": latest_day,
        "occupiedBeds": {"value": bed_totals["occupied"] or 0, "capacity": bed_totals["capacity"] or 0, "available": bed_totals["available"] or 0, "confidence": "MEDIUM"},
        "admissionsToday": {"value": admissions, "confidence": "HIGH"},
        "labTurnaround": {"value": f"{average_minutes // 60}h {average_minutes % 60:02d}m", "pending": lab_metrics["pending"] or 0, "confidence": "HIGH"},
        "dischargesDue": {"value": discharges, "confidence": "MEDIUM"},
        "openConflicts": open_conflicts,
        "trustScores": trust_by_name,
        "alerts": alerts,
        "updatedAt": latest_snapshot,  # use the latest loaded snapshot date rather than the machine clock
    })


@app.get("/api/patient-flow")
def patient_flow():
    reporting_date = request.args.get("date")
    rows = db.execute(
        "SELECT patient_id, admission_at, discharge_at, ward_normalized, department FROM his_events "
        "WHERE (? IS NULL OR substr(admission_at, 1, 10) = ? OR substr(discharge_at, 1, 10) = ?) ORDER BY admission_at DESC",
        (reporting_date, reporting_date, reporting_date),
    ).fetchall()
    events = [dict(row) for row in rows]
    return jsonify({
        "admissions": sum(bool(item["admission_at"]) for item in events),
        "discharges": sum(bool(item["discharge_at"]) for item in events),
        "inCare": sum(not item["discharge_at"] for item in events),
        "events": events,
    })


@app.get("/api/bottlenecks")
def bottlenecks():
    reporting_date = request.args.get("date") or latest_snapshot_date()[:10]
    flow_rows = db.execute(
        "SELECT department, COUNT(admission_at) AS admissions, COUNT(discharge_at) AS discharges, "
        "SUM(discharge_at IS NULL) AS in_care FROM his_events "
        "WHERE substr(admission_at, 1, 10) = ? OR substr(discharge_at, 1, 10) = ? "
        "GROUP BY department ORDER BY admissions DESC, department",
        (reporting_date, reporting_date),
    ).fetchall()
    lab_rows = db.execute(
        f"SELECT department_normalized AS department, COUNT(*) AS orders, "
        f"SUM(resulted_at IS NULL) AS pending, "
        f"ROUND({diff_minutes_sql('resulted_at', 'ordered_at')}) AS avg_minutes "
        f"FROM lab_orders GROUP BY department_normalized ORDER BY avg_minutes DESC",
    ).fetchall()
    flow_by_department = {row["department"] or "Unassigned": dict(row) for row in flow_rows}
    bottleneck_rows = []
    departments = sorted(set(flow_by_department) | {row["department"] or "Unassigned" for row in lab_rows})
    lab_by_department = {row["department"] or "Unassigned": dict(row) for row in lab_rows}
    for department in departments:
        flow = flow_by_department.get(department, {"admissions": 0, "discharges": 0, "in_care": 0})
        lab = lab_by_department.get(department, {"orders": 0, "pending": 0, "avg_minutes": 0})
        net_flow = flow["admissions"] - flow["discharges"]
        avg_minutes = int(lab["avg_minutes"] or 0)
        pending = lab["pending"] or 0
        score = (net_flow * 3) + pending + (max(0, avg_minutes - 240) // 30)
        severity = "HIGH" if score >= 8 else "WATCH" if score >= 3 else "STABLE"
        bottleneck_rows.append({
            "department": department,
            "admissions": flow["admissions"],
            "discharges": flow["discharges"],
            "inCare": flow["in_care"],
            "netFlow": net_flow,
            "labOrders": lab["orders"],
            "labPending": pending,
            "labAverageMinutes": avg_minutes,
            "labAverage": f"{avg_minutes // 60}h {avg_minutes % 60:02d}m",
            "severity": severity,
        })
    bottleneck_rows.sort(key=lambda row: (row["severity"] != "HIGH", row["severity"] != "WATCH", -row["netFlow"], -row["labAverageMinutes"]))
    return jsonify({
        "reportingDate": reporting_date,
        "bedAssignmentTimeAvailable": False,
        "bedAssignmentNote": "The supplied extracts contain no admission-to-bed timestamp; patient-flow pressure is shown as a proxy.",
        "departments": bottleneck_rows,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=os.getenv("FLASK_ENV") == "development")
