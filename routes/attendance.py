from fastapi import APIRouter
from database import get_db
from pydantic import BaseModel
from datetime import date, timedelta

router = APIRouter()

class AttendanceCreate(BaseModel):
    subject_id: str
    date: str
    status: str
    count: int = 1

@router.post("/attendance")
def mark_attendance(record: AttendanceCreate):
    db = get_db()
    count = max(1, int(record.count or 1))

    # Backward-compatible single-record upsert behavior.
    if count == 1:
        existing = db.table("attendance_records")\
            .select("*")\
            .eq("subject_id", record.subject_id)\
            .eq("date", record.date)\
            .execute()
        if existing.data:
            result = db.table("attendance_records")\
                .update({"status": record.status})\
                .eq("id", existing.data[0]["id"])\
                .execute()
        else:
            result = db.table("attendance_records")\
                .insert({
                    "subject_id": record.subject_id,
                    "date": record.date,
                    "status": record.status
                }).execute()
        return {"message": "attendance marked", "data": result.data[0], "created": 1}

    # Batch insert mode for onboarding totals: spread across unique dates.
    # Use different ranges for attended vs bunked to avoid date conflicts
    try:
        start = date.fromisoformat(record.date)
    except ValueError:
        start = date.today()

    rows = []
    if record.status == "attended":
        for i in range(count):
            days_ago = count - i
            rows.append({
                "subject_id": record.subject_id,
                "date": (start - timedelta(days=days_ago * 7)).isoformat(),
                "status": record.status
            })
    else:
        for i in range(count):
            rows.append({
                "subject_id": record.subject_id,
                "date": (start + timedelta(days=i * 7)).isoformat(),
                "status": record.status
            })

    result = db.table("attendance_records").insert(rows).execute()
    return {
        "message": "attendance batch marked",
        "created": len(result.data or []),
        "data": (result.data or [])[:1]
    }

@router.get("/attendance/{subject_id}")
def get_attendance(subject_id: str):
    db = get_db()
    result = db.table("attendance_records")\
        .select("*")\
        .eq("subject_id", subject_id)\
        .execute()
    attended = len([r for r in result.data if r["status"] == "attended"])
    total = len([r for r in result.data if r["status"] != "cancelled"])
    pct = round((attended / total * 100), 1) if total > 0 else 0
    return {
        "records": result.data,
        "attended": attended,
        "total": total,
        "percentage": pct
    }