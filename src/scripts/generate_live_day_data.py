import csv
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
MARKER = "MCH-9000001"


def append_rows(filename, fieldnames, rows):
    path = DATA_DIR / filename
    with path.open(newline="", encoding="utf-8") as csv_file:
        existing = list(csv.DictReader(csv_file))
    if filename == "his_admissions_discharges.csv" and any(row["patient_id"] == MARKER for row in existing):
        return 0
    with path.open("a", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writerows(rows)
    return len(rows)


his_fields = ["patient_id", "admission_datetime", "discharge_datetime", "ward", "admitting_department", "age", "gender"]
wards = [("I.C.U.", "Critical Care"), ("Medical ICU", "Pulmonology"), ("General Ward - A", "General Medicine"), ("General Ward - B", "Cardiology"), ("Paediatrics", "Paediatrics")]
his_rows = []
start = datetime(2026, 8, 28)
for index in range(240):
    admitted = start + timedelta(minutes=(index * 6) % (24 * 60))
    ward, department = wards[index % len(wards)]
    discharged = admitted + timedelta(hours=5 + (index % 8)) if index < 160 else None
    his_rows.append({
        "patient_id": f"MCH-{9000001 + index:07d}",
        "admission_datetime": admitted.strftime("%Y-%m-%d %H:%M:%S"),
        "discharge_datetime": discharged.strftime("%Y-%m-%d %H:%M:%S") if discharged else "",
        "ward": ward,
        "admitting_department": department,
        "age": str(18 + (index % 70)),
        "gender": ["Male", "Female"][index % 2],
    })

lab_fields = ["order_id", "patient_id", "test_name", "ordered_at", "collected_at", "resulted_at", "priority", "department"]
lab_rows = []
for index in range(480):
    ordered = start + timedelta(minutes=(index * 3) % (24 * 60))
    collected = ordered + timedelta(minutes=15 + (index % 30))
    resulted = collected + timedelta(minutes=45 + (index % 240)) if index % 17 else None
    lab_rows.append({
        "order_id": f"LIVE{900001 + index}",
        "patient_id": f"{9000001 + (index % 240)}",
        "test_name": ["CBC", "KFT", "CRP", "ABG"][index % 4],
        "ordered_at": ordered.strftime("%d/%m/%Y %H:%M"),
        "collected_at": collected.strftime("%d/%m/%Y %H:%M"),
        "resulted_at": resulted.strftime("%d/%m/%Y %H:%M") if resulted else "",
        "priority": ["Routine", "Urgent", "Stat"][index % 3],
        "department": ["Critical Care", "Pulmonology", "General Medicine", "Cardiology", "Paediatrics"][index % 5],
    })

bed_fields = ["Date", "Ward", "Total Beds", "Occupied", "Available", "Remarks"]
bed_rows = [{"Date": "28-Aug-26", "Ward": ward, "Total Beds": str(total), "Occupied": str(occupied), "Available": str(total - occupied), "Remarks": "Synthetic live-day extension; replace with connected source"} for ward, total, occupied in [("I.C.U.", 12, 11), ("Medical ICU", 10, 9), ("General Ward - A", 30, 25), ("General Ward - B", 30, 24), ("Paediatrics", 16, 13)]]

print("HIS rows appended:", append_rows("his_admissions_discharges.csv", his_fields, his_rows))
print("Lab rows appended:", append_rows("lab_order_to_result.csv", lab_fields, lab_rows))
print("Bed rows appended:", append_rows("bed_occupancy_manual.csv", bed_fields, bed_rows))
