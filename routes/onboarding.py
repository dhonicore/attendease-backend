import os
import json
import httpx
import base64
import io
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

    pdf_text = ""
    try:
        import PyPDF2
        reader = PyPDF2.PdfReader(io.BytesIO(contents))
        for page in reader.pages:
            pdf_text += page.extract_text() + "\n"
    except Exception:
        pdf_text = ""

    if len(pdf_text.strip()) < 100:
        b64 = base64.b64encode(contents).decode()
        mime = file.content_type or "application/pdf"
        parts = [{"text": f"""Extract timetable data from this text. Find all sections and their subjects.
For lab subjects like "A1(CHE)/A2(PY)" treat them as separate lab subjects.
The main subjects are the core ones that appear every day like MATHS, CHEM, AI, ELE, PYTHON, ENG.
Ignore COE, IDP, MOOC, Mentoring, Makerspace, ICP, TYL, Assignment slots.

Return ONLY valid JSON, no markdown:
{{
  "sections": ["A", "B", "C", "D", "E", "F", "G", "H"],
  "subjects_by_section": {{
    "A": ["Applied Mathematics II", "Applied Chemistry", "Introduction to AI and Applications", "Introduction to Electrical Engineering", "Python Programming", "Communication Skills", "Indian Constitution and Engineering Ethics"]
  }}
}}

Timetable text:
{pdf_text[:8000]}"""}]

    async with httpx.AsyncClient() as client:
        res = await client.post(
            GEMINI_URL,
            json={
                "contents": [{"parts": parts}],
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
        return {"error": str(e), "raw": str(data)[:300]}

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

    pdf_text = ""
    try:
        import PyPDF2
        reader = PyPDF2.PdfReader(io.BytesIO(contents))
        for page in reader.pages:
            pdf_text += page.extract_text() + "\n"
    except Exception:
        pdf_text = ""

    if len(pdf_text.strip()) < 100:
        b64 = base64.b64encode(contents).decode()
        mime = file.content_type or "application/pdf"
        parts = [
            {"text": "Extract all holidays from this academic calendar. Return ONLY JSON no markdown: {\"holidays\": [{\"date\": \"2026-01-26\", \"name\": \"Republic Day\"}], \"semester_end\": \"2026-05-15\"}"},
            {"inline_data": {"mime_type": mime, "data": b64}}
        ]
    else:
        parts = [{"text": f"""Extract all holidays from this academic calendar.
Return ONLY valid JSON, no markdown:
{{
  "holidays": [{{"date": "2026-01-26", "name": "Republic Day"}}],
  "semester_end": "2026-05-15"
}}
Use YYYY-MM-DD format.

Calendar text:
{pdf_text[:4000]}"""}]

    async with httpx.AsyncClient() as client:
        res = await client.post(
            GEMINI_URL,
            json={
                "contents": [{"parts": parts}],
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

    parts = [
        {"text": "This is a screenshot from a college app showing attendance. Extract attendance data. Return ONLY valid JSON no markdown: {\"subjects\": [{\"name\": \"Data Structures\", \"attended\": 38, \"total\": 46, \"percentage\": 82.6}]}"},
        {"inline_data": {"mime_type": mime, "data": b64}}
    ]

    async with httpx.AsyncClient() as client:
        res = await client.post(
            GEMINI_URL,
            json={
                "contents": [{"parts": parts}],
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