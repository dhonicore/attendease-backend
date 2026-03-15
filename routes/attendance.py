from fastapi import APIRouter
from database import get_db
from pydantic import BaseModel
from datetime import date

router = APIRouter()

class AttendanceCreate(BaseModel):
    subject_id: str
    date: str
    status: str

@router.post("/attendance")
def mark_attendance(record: AttendanceCreate):
    db = get_db()
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
    return {"message": "attendance marked", "data": result.data[0]}

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