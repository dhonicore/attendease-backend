from fastapi import APIRouter
from database import get_db

router = APIRouter()

@router.get("/users/{user_id}")
def get_user(user_id: str):
    db = get_db()
    result = db.table("users").select("*").eq("id", user_id).execute()
    if not result.data:
        return {"error": "User not found"}
    return {"user": result.data[0]}
