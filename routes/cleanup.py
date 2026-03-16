from fastapi import APIRouter
from database import get_db

router = APIRouter()

@router.delete("/subjects/all/{user_id}")
def delete_all_subjects(user_id: str):
    db = get_db()
    subjects = db.table("subjects").select("id").eq("user_id", user_id).execute().data
    for s in subjects:
        db.table("attendance_records").delete().eq("subject_id", s["id"]).execute()
    db.table("subjects").delete().eq("user_id", user_id).execute()
    return {"message": "all subjects deleted"}

@router.put("/subjects/{subject_id}/rename")
def rename_subject(subject_id: str, request: dict):
    db = get_db()
    db.table("subjects").update({"name": request["name"]}).eq("id", subject_id).execute()
    return {"message": "renamed"}

@router.delete("/subjects/{subject_id}")
def delete_subject(subject_id: str):
    db = get_db()
    db.table("attendance_records").delete().eq("subject_id", subject_id).execute()
    db.table("subjects").delete().eq("id", subject_id).execute()
    return {"message": "deleted"}

@router.delete("/attendance/{subject_id}/{date}")
def delete_attendance(subject_id: str, date: str):
    db = get_db()
    db.table("attendance_records").delete().eq("subject_id", subject_id).eq("date", date).execute()
    return {"message": "attendance removed"}