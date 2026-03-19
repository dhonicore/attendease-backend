import os
import json
import httpx
import base64
import io
from fastapi import APIRouter, UploadFile, File
from database import get_db
from pydantic import BaseModel
from typing import List

router = APIRouter()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"

DAY_MAP = {
    "monday": 0, "tuesday": 1, "wednesday": 2,
    "thursday": 3, "friday": 4, "saturday": 5
}

class ProfileUpdate(BaseModel):
    user_id: str
    college: str
    year: str
    semester: str
    section: str
    batch: str = ""
    min_attendance: int = 75

@router.post("/onboarding/profile")
def update_profile(data: ProfileUpdate):
    db = get_db()
    db.table("users").update({
        "college": data.college,
        "year": data.year,
        "semester": data.semester,
        "section": data.section,
        "batch": data.batch,
        "min_attendance": data.min_attendance,
    }).eq("id", data.user_id).execute()
    return {"message": "profile updated"}


@router.post("/onboarding/timetable/{user_id}")
async def parse_timetable(user_id: str, file: UploadFile = File(...)):
    contents = await file.read()

    # Get user batch and section
    db = get_db()
    user_row = db.table("users").select("batch,section").eq("id", user_id).execute()
    batch = section = ""
    if user_row.data:
        batch   = user_row.data[0].get("batch", "") or ""
        section = user_row.data[0].get("section", "") or ""

    pdf_text = ""
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(contents))
        for page in reader.pages:
            extracted = page.extract_text()
            if extracted:
                pdf_text += extracted + "\n"
    except Exception:
        pdf_text = ""

    batch_hint   = f"The student is in batch {batch}." if batch else ""
    section_hint = f"The student is in section {section}." if section else ""

    schedule_example = json.dumps({
        "subjects": ["Applied Mathematics", "Physics", "Chemistry Lab"],
        "schedule": {
            "monday":    ["Applied Mathematics", "Physics", "Chemistry"],
            "tuesday":   ["Chemistry Lab", "English", "Applied Mathematics"],
            "wednesday": ["Chemistry", "Applied Mathematics"],
            "thursday":  ["Physics", "Applied Mathematics", "Chemistry"],
            "friday":    ["English", "Chemistry", "Physics"],
            "saturday":  ["Applied Mathematics"]
        }
    }, indent=2)

    prompt_text = f"""Extract the weekly timetable for this student from their college timetable document.
{section_hint} {batch_hint}

Instructions:
- For each day Monday to Saturday, list which subjects this student has class
- For lab subjects that rotate between batches (e.g. A1(CHE)/A2(PY)/A3(CHE)):
  * If batch is known, only include the subject for THAT batch
  * If batch is unknown, include all unique lab subjects
- Use consistent subject names throughout
- Only include days that actually have classes
- Do not include breaks, lunch, mentoring, COE, IDP as subjects

Return ONLY valid JSON, no markdown:
{schedule_example}

Timetable text:
{pdf_text[:8000]}"""

    if len(pdf_text.strip()) >= 100:
        parts = [{"text": prompt_text}]
    else:
        b64  = base64.b64encode(contents).decode()
        mime = file.content_type or "application/pdf"
        parts = [
            {"text": prompt_text},
            {"inline_data": {"mime_type": mime, "data": b64}}
        ]

    async with httpx.AsyncClient() as client:
        res = await client.post(
            GEMINI_URL,
            json={
                "contents": [{"parts": parts}],
                "generationConfig": {"temperature": 0.1, "maxOutputTokens": 4096}
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
    user_id  = request["user_id"]
    subjects = request["subjects"]
    schedule = request.get("schedule", {})
    db = get_db()

    # Save subjects (no duplicates)
    subject_id_map = {}
    for name in subjects:
        existing = db.table("subjects").select("id,name").eq("user_id", user_id).eq("name", name).execute()
        if existing.data:
            subject_id_map[name] = existing.data[0]["id"]
        else:
            new_sub = db.table("subjects").insert({
                "user_id": user_id,
                "name":    name,
                "color":   "#00ff88"
            }).execute()
            if new_sub.data:
                subject_id_map[name] = new_sub.data[0]["id"]

    # Clear old timetable
    db.table("timetable").delete().eq("user_id", user_id).execute()

    # Save day-wise schedule
    saved = 0
    for day_name, day_subjects in schedule.items():
        day_num = DAY_MAP.get(day_name.lower())
        if day_num is None:
            continue
        for sub_name in day_subjects:
            # Exact match first, then fuzzy
            subject_id = subject_id_map.get(sub_name)
            if not subject_id:
                for saved_name, sid in subject_id_map.items():
                    if (saved_name.lower() in sub_name.lower() or
                            sub_name.lower() in saved_name.lower()):
                        subject_id = sid
                        break
            if subject_id:
                db.table("timetable").insert({
                    "user_id":    user_id,
                    "subject_id": subject_id,
                    "day_of_week": str(day_num),
                    "start_time": "",
                    "end_time":   ""
                }).execute()
                saved += 1

    db.table("users").update({"onboarded": True}).eq("id", user_id).execute()

    return {
        "message": "timetable saved",
        "subjects_added": len(subject_id_map),
        "timetable_entries": saved
    }


@router.post("/onboarding/coe/{user_id}")
async def parse_coe(user_id: str, file: UploadFile = File(...)):
    contents = await file.read()

    pdf_text = ""
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(contents))
        for page in reader.pages:
            extracted = page.extract_text()
            if extracted:
                pdf_text += extracted + "\n"
    except Exception:
        pdf_text = ""

    prompt = f"""Extract all holidays from this college academic calendar.
Only extract actual holidays (days marked as HOLIDAY with no classes).
Do NOT include workshops, seminars, events, or working Saturdays.
Also extract semester start and end dates if visible.

Return ONLY valid JSON, no markdown:
{{
  "holidays": [{{"date": "2026-01-26", "name": "Republic Day"}}],
  "semester_start": "2026-02-24",
  "semester_end": "2026-05-16"
}}
Use YYYY-MM-DD format.

Calendar text:
{pdf_text[:10000]}"""

    if len(pdf_text.strip()) >= 100:
        parts = [{"text": prompt}]
    else:
        b64  = base64.b64encode(contents).decode()
        mime = file.content_type or "application/pdf"
        parts = [
            {"text": prompt},
            {"inline_data": {"mime_type": mime, "data": b64}}
        ]

    async with httpx.AsyncClient() as client:
        res = await client.post(
            GEMINI_URL,
            json={
                "contents": [{"parts": parts}],
                "generationConfig": {"temperature": 0.1, "maxOutputTokens": 4096}
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
                "date":    holiday["date"],
                "name":    holiday["name"]
            }).execute()

        # Save semester config
        try:
            db.table("semester_config").delete().eq("user_id", user_id).execute()
            db.table("semester_config").insert({
                "user_id":        user_id,
                "semester_start": result.get("semester_start", ""),
                "semester_end":   result.get("semester_end", "")
            }).execute()
        except Exception:
            pass

        return {
            "message": "COE parsed",
            "holidays_added": len(result.get("holidays", [])),
            "semester_start": result.get("semester_start", ""),
            "semester_end":   result.get("semester_end", ""),
        }
    except Exception as e:
        return {"error": str(e)}


@router.post("/onboarding/screenshot/{user_id}")
async def parse_screenshot(user_id: str, files: List[UploadFile] = File(...)):
    import re

    def clean_name(raw: str) -> str:
        is_lab = bool(re.search(r'\(\s*P\s*\)', raw, re.IGNORECASE))
        name   = re.sub(r'\[.*?\]', '', raw).strip()
        name   = re.sub(r'\(\s*P\s*\)', '', name, flags=re.IGNORECASE).strip()
        name   = name.strip(' -–—')
        if is_lab:
            name = name + " Lab"
        return name

    async def parse_one(image_bytes: bytes, mime_type: str) -> list:
        b64   = base64.b64encode(image_bytes).decode()
        parts = [
            {"text": """This is a college attendance portal screenshot.
Extract attendance for every subject listed.
Subject names may have codes like [1BPLC205B] or [1BPLC205B (P)].
(P) = practical/lab subject.

Return ONLY valid JSON, no markdown:
{
  "subjects": [
    {"name": "Python Programming [1BPLC205B]", "attended": 9, "total": 11},
    {"name": "Python Programming [1BPLC205B (P)]", "attended": 1, "total": 2}
  ]
}
Include ALL subjects — both theory and lab."""},
            {"inline_data": {"mime_type": mime_type, "data": b64}}
        ]
        async with httpx.AsyncClient() as client:
            res = await client.post(
                GEMINI_URL,
                json={
                    "contents": [{"parts": parts}],
                    "generationConfig": {"temperature": 0.1, "maxOutputTokens": 2048}
                },
                timeout=45
            )
            d = res.json()
        t = d["candidates"][0]["content"]["parts"][0]["text"]
        t = t.replace("```json","").replace("```","").strip()
        return json.loads(t).get("subjects", [])

    # Parse all screenshots
    all_subjects = []
    for upload in files:
        contents = await upload.read()
        mime     = upload.content_type or "image/jpeg"
        try:
            subs = await parse_one(contents, mime)
            all_subjects.extend(subs)
        except Exception:
            continue

    if not all_subjects:
        return {"error": "Could not read any subjects from the screenshots"}

    # Deduplicate by clean name
    seen = {}
    for item in all_subjects:
        clean = clean_name(item["name"])
        if clean not in seen:
            item["_clean"] = clean
            seen[clean]    = item
    merged = list(seen.values())

    # Save to DB
    db            = get_db()
    user_subjects = db.table("subjects").select("*").eq("user_id", user_id).execute().data
    updated = created = 0

    for item in merged:
        clean_nm = item["_clean"]
        attended = int(item.get("attended", 0))
        total    = int(item.get("total", 0))
        if total == 0:
            continue

        # Find or create subject
        matching = next(
            (s for s in user_subjects
             if s["name"].lower() == clean_nm.lower()
             or clean_nm.lower() in s["name"].lower()
             or s["name"].lower() in clean_nm.lower()),
            None
        )

        if not matching:
            new_sub = db.table("subjects").insert({
                "user_id": user_id,
                "name":    clean_nm,
                "color":   "#00ff88"
            }).execute()
            if new_sub.data:
                matching = new_sub.data[0]
                user_subjects.append(matching)
                created += 1

        if matching:
            sub_id = matching["id"]
            # Clear old records for this subject to avoid duplicates
            db.table("attendance_records").delete().eq("subject_id", sub_id).execute()

            # Insert attended
            for i in range(attended):
                db.table("attendance_records").insert({
                    "subject_id": sub_id,
                    "date":       f"2026-01-{(i % 28) + 1:02d}",
                    "status":     "attended"
                }).execute()
            # Insert bunked
            for i in range(total - attended):
                db.table("attendance_records").insert({
                    "subject_id": sub_id,
                    "date":       f"2026-02-{(i % 28) + 1:02d}",
                    "status":     "bunked"
                }).execute()
            updated += 1

    return {
        "message": f"Imported attendance for {updated} subjects ({created} new)",
        "subjects_found": len(merged),
        "subjects": [{"name": s["_clean"], "attended": s.get("attended"), "total": s.get("total")} for s in merged]
    }
