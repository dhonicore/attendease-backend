from typing import Any, Dict
from fastapi import APIRouter
from database import get_db
from pydantic import BaseModel

router = APIRouter()

@router.get("/users/{user_id}")
def get_user(user_id: str):
    db = get_db()
    result = db.table("users").select("*").eq("id", user_id).execute()
    if not result.data:
        return {"error": "User not found"}
    # Return plain user object for current frontend expectations.
    return result.data[0]


class UserPatch(BaseModel):
    name: str | None = None
    onboarded: bool | None = None
    min_attendance: int | None = None
    section: str | None = None
    batch: str | None = None
    college: str | None = None
    year: str | None = None
    semester: str | None = None


@router.patch("/users/{user_id}")
def patch_user(user_id: str, payload: UserPatch):
    db = get_db()
    updates: Dict[str, Any] = {
        k: v for k, v in payload.model_dump().items() if v is not None
    }
    if not updates:
        return {"message": "no changes"}

    result = db.table("users").update(updates).eq("id", user_id).execute()
    if not result.data:
        return {"error": "User not found"}
    return {"message": "user updated", "user": result.data[0]}
