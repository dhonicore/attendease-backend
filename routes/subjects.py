from fastapi import APIRouter, HTTPException
from database import get_db
from pydantic import BaseModel

router = APIRouter()

class SubjectCreate(BaseModel):
    user_id: str
    name: str
    color: str = "#639922"

@router.post("/subjects")
def create_subject(subject: SubjectCreate):
    db = get_db()
    result = db.table("subjects").insert({
        "user_id": subject.user_id,
        "name": subject.name,
        "color": subject.color
    }).execute()
    return {"message": "subject created", "data": result.data[0]}

@router.get("/subjects/{user_id}")
def get_subjects(user_id: str):
    db = get_db()
    result = db.table("subjects").select("*").eq("user_id", user_id).execute()
    return {"subjects": result.data}

@router.delete("/subjects/{subject_id}")
def delete_subject(subject_id: str):
    db = get_db()
    db.table("subjects").delete().eq("id", subject_id).execute()
    return {"message": "subject deleted"}