import os
import json
import httpx
import base64
from fastapi import APIRouter, UploadFile, File
from database import get_db
from pydantic import BaseModel

router = APIRouter()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"

class ProfileUpdate(BaseModel):
    user_id: str
    college: str
    year: str
    semester: str
    section: str
    min_attendance: int = 75

@router.post("/onboarding/profile")
def update_profile(data: ProfileUpdate):
    db = get_db()
    db.table("users").update({
        "college": data.college,
        "year": data.year,
        "semester": data.semester,
        "section": data.section,
        "min_attendance": data.min_attendance,
    }).eq("id", data.user_id).execute()
    return {"message": "profile updated"}

@router.post("/onboarding/timetable/{user_id}")
async def parse_timetable(user_id: str, file: UploadFile = File(...)):
    contents = await file.read()
    b64 = base64.b64encode(contents).decode()
    mime = file.content_type or "application/pdf"

    prompt = """Extract timetable data from this document. Find all sections and their subjects.
Return ONLY this JSON, no markdown:
{
  "sections": ["A", "B"],
  "subjects_by_section": {
    "A": ["Maths", "Physics", "Chemistry"],
    "B": ["Maths", "Physics", "Chemistry"]
  }
}
If you cannot find sections, use "DEFAULT" as section name and list all subjects you find."""

    async with httpx.AsyncClient() as client:
        res = await client.post(
            GEMINI_URL,
            json={
                "contents": [{
                    "parts": [
                        {"text": prompt},
                        {"inline_data": {"mime_type": mime, "data": b64}}
                    ]
                }],
                "generationConfig": {"temperature": 0.1, "maxOutputTokens": 2048}
            },
            timeout=60
        )
        data = res.json()

    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except Exception as e:
        return {"error": str(e), "raw": str(data)[:500]}

@router.post("/onboarding/save-timetable")
async def save_timetable(request: dict):
    user_id = request["user_id"]
    section = request["section"]
    subjects = request["subjects"]
    db = get_db()
    for subject_name in subjects:
        existing = db.table("subjects").select("*").eq("user_id", user_id).eq("name", subject_name).execute()
        if not existing.data:
            db.table("subjects").insert({
                "user_id": user_id,
                "name": subject_name,
                "color": "#00ff88"
            }).execute()
    db.table("users").update({"section": section, "onboarded": True}).eq("id", user_id).execute()
    return {"message": "timetable saved", "subjects_added": len(subjects)}

@router.post("/onboarding/coe/{user_id}")
async def parse_coe(user_id: str, file: UploadFile = File(...)):
    contents = await file.read()
    b64 = base64.b64encode(contents).decode()
    mime = file.content_type or "application/pdf"

    prompt = """This is a college Calendar of Events or academic calendar.
Extract all holidays. Return ONLY valid JSON, no markdown:
{
  "holidays": [
    {"date": "2026-01-26", "name": "Republic Day"}
  ],
  "semester_end": "2026-05-15"
}
Use YYYY-MM-DD format for dates."""

    async with httpx.AsyncClient() as client:
        res = await client.post(
            GEMINI_URL,
            json={
                "contents": [{
                    "parts": [
                        {"text": prompt},
                        {"inline_data": {"mime_type": mime, "data": b64}}
                    ]
                }],
                "generationConfig": {"temperature": 0.1, "maxOutputTokens": 2048}
            },
            timeout=60
        )
        data = res.json()

    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        text = text.replace("```json", "").replace("```", "").strip()
        result = json.loads(text)
        db = get_db()
        db.table("holidays").delete().eq("user_id", user_id).execute()
        for holiday in result.get("holidays", []):
            db.table("holidays").insert({
                "user_id": user_id,
                "date": holiday["date"],
                "name": holiday["name"]
            }).execute()
        return {"message": "COE parsed", "holidays_added": len(result.get("holidays", [])), "data": result}
    except Exception as e:
        return {"error": str(e)}

@router.post("/onboarding/screenshot/{user_id}")
async def parse_screenshot(user_id: str, file: UploadFile = File(...)):
    contents = await file.read()
    b64 = base64.b64encode(contents).decode()
    mime = file.content_type or "image/jpeg"

    prompt = """This is a screenshot from a college app showing attendance.
Extract attendance data for each subject. Return ONLY valid JSON, no markdown:
{
  "subjects": [
    {"name": "Data Structures", "attended": 38, "total": 46, "percentage": 82.6}
  ]
}"""

    async with httpx.AsyncClient() as client:
        res = await client.post(
            GEMINI_URL,
            json={
                "contents": [{
                    "parts": [
                        {"text": prompt},
                        {"inline_data": {"mime_type": mime, "data": b64}}
                    ]
                }],
                "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1024}
            },
            timeout=30
        )
        data = res.json()

    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        text = text.replace("```json", "").replace("```", "").strip()
        result = json.loads(text)
        db = get_db()
        user_subjects = db.table("subjects").select("*").eq("user_id", user_id).execute().data
        updated = 0
        for item in result.get("subjects", []):
            matching = next((s for s in user_subjects if s["name"].lower() in item["name"].lower() or item["name"].lower() in s["name"].lower()), None)
            if matching:
                for i in range(item["attended"]):
                    db.table("attendance_records").insert({
                        "subject_id": matching["id"],
                        "date": f"2026-01-{(i%28)+1:02d}",
                        "status": "attended"
                    }).execute()
                for i in range(item["total"] - item["attended"]):
                    db.table("attendance_records").insert({
                        "subject_id": matching["id"],
                        "date": f"2026-02-{(i%28)+1:02d}",
                        "status": "bunked"
                    }).execute()
                updated += 1
        return {"message": f"imported attendance for {updated} subjects", "data": result}
    except Exception as e:
        return {"error": str(e)}