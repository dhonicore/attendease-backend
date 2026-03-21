import os
import json
import httpx
import base64
import io
import re
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

    prompt = f"""Extract the weekly class schedule for this student from their college timetable.
{section_hint} {batch_hint}

Rules:
- List subjects for each day Monday to Saturday
- For lab subjects rotating between batches (e.g. A1(CHE)/A2(PY)/A3(CHE)), only include the one matching this student's batch. If batch unknown, include all unique labs.
- Ignore: breaks, lunch, mentoring, COE, IDP, MOOC, library, assembly
- Use consistent subject names throughout

Return ONLY valid JSON, no markdown:
{{
  "subjects": ["Applied Mathematics II", "Physics", "Chemistry Lab"],
  "schedule": {{
    "monday":    ["Applied Mathematics II", "Physics"],
    "tuesday":   ["Chemistry Lab", "English"],
    "wednesday": ["Applied Mathematics II", "Chemistry"],
    "thursday":  ["Physics", "Applied Mathematics II"],
    "friday":    ["English", "Chemistry"],
    "saturday":  ["Applied Mathematics II"]
  }}
}}

Timetable text:
{pdf_text[:8000]}"""

    if len(pdf_text.strip()) >= 100:
        parts = [{"text": prompt}]
    else:
        b64  = base64.b64encode(contents).decode()
        mime = file.content_type or "application/pdf"
        parts = [{"text": prompt}, {"inline_data": {"mime_type": mime, "data": b64}}]

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
    subjects = request.get("subjects", [])
    schedule = request.get("schedule", {})
    # legacy support
    section  = request.get("section", "")
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

    # Save day-wise schedule to timetable table
    if schedule:
        db.table("timetable").delete().eq("user_id", user_id).execute()
        for day_name, day_subjects in schedule.items():
            day_num = DAY_MAP.get(day_name.lower())
            if day_num is None:
                continue
            for sub_name in day_subjects:
                subject_id = subject_id_map.get(sub_name)
                if not subject_id:
                    for saved_name, sid in subject_id_map.items():
                        if (saved_name.lower() in sub_name.lower() or
                                sub_name.lower() in saved_name.lower()):
                            subject_id = sid
                            break
                if subject_id:
                    db.table("timetable").insert({
                        "user_id":     user_id,
                        "subject_id":  subject_id,
                        "day_of_week": str(day_num),
                        "start_time":  "",
                        "end_time":    ""
                    }).execute()

    if section:
        db.table("users").update({"section": section, "onboarded": True}).eq("id", user_id).execute()
    else:
        db.table("users").update({"onboarded": True}).eq("id", user_id).execute()

    return {"message": "timetable saved", "subjects_added": len(subject_id_map)}


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
Only extract actual holidays — days marked HOLIDAY with no classes.
Do NOT include workshops, seminars, events, or working Saturdays.
Also extract semester start and end dates.

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
        parts = [{"text": prompt}, {"inline_data": {"mime_type": mime, "data": b64}}]

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
                "date":    holiday["date"],
                "name":    holiday["name"]
            }).execute()

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
            "message":        "COE parsed",
            "holidays_added": len(result.get("holidays", [])),
            "semester_start": result.get("semester_start", ""),
            "semester_end":   result.get("semester_end", ""),
        }
    except Exception as e:
        return {"error": str(e)}


def clean_subject_name(raw: str) -> str:
    """
    Clean subject name from Indian college portal format.
    e.g. "Applied Chemistry for Smart Systems [ 1BCHES202 (P) ]"
      -> "Applied Chemistry for Smart Systems Lab"
    """
    is_lab = bool(re.search(r'\(\s*P\s*\)', raw, re.IGNORECASE))
    # Remove course codes in brackets like [1BCHES202] or [1BCHES202 (P)]
    name = re.sub(r'\[.*?\]', '', raw).strip()
    # Remove standalone (P)
    name = re.sub(r'\(\s*P\s*\)', '', name, flags=re.IGNORECASE).strip()
    # Clean trailing punctuation
    name = name.strip(' -–—:')
    # Add Lab suffix for practical subjects
    if is_lab and not name.lower().endswith('lab'):
        name = name + " Lab"
    return name


@router.post("/onboarding/screenshot/{user_id}")
async def parse_screenshot(user_id: str, files: List[UploadFile] = File(...)):
    """
    Accept 1 or 2 screenshots from Indian college attendance portals.
    Handles formats like:
    - "Subject Name [ CODE ]  10 / 12 = 83.33%"
    - "Subject Name [ CODE (P) ]  4 / 4 = 100%"
    Merges results from multiple screenshots and saves to DB.
    """

    async def parse_one_screenshot(image_bytes: bytes, mime_type: str) -> list:
        b64 = base64.b64encode(image_bytes).decode()
        parts = [
            {"text": """This is a screenshot from an Indian college attendance portal (like Jspiders, VTU, CMRIT etc).

IMPORTANT INSTRUCTIONS:
1. Find every subject listed with its attendance numbers
2. Subject names often have course codes in brackets like [1BCHES202] or [1BCHES202 (P)]
   - (P) means it is a PRACTICAL/LAB subject
   - Extract the full name INCLUDING the brackets exactly as shown
3. Attendance is shown as "attended / total" like "10 / 12" or "10/12"
4. Sometimes shown as percentage like "83.33%" — if so, try to find the raw numbers too
5. Include EVERY subject — both theory and practical (P) ones
6. Do NOT skip any subject even if attendance is 0% or 100%

Return ONLY valid JSON, no markdown, no explanation:
{
  "subjects": [
    {"name": "Applied Chemistry for Smart Systems [ 1BCHES202 ]", "attended": 10, "total": 12},
    {"name": "Applied Chemistry for Smart Systems [ 1BCHES202 (P) ]", "attended": 4, "total": 4},
    {"name": "Applied Mathematics -II [ 1BMATS201 ]", "attended": 13, "total": 14},
    {"name": "Python Programming [ 1BPLC205B ]", "attended": 9, "total": 11},
    {"name": "Python Programming [ 1BPLC205B (P) ]", "attended": 1, "total": 2}
  ]
}

Extract ALL subjects visible on screen."""},
            {"inline_data": {"mime_type": mime_type, "data": b64}}
        ]

        async with httpx.AsyncClient() as client:
            res = await client.post(
                GEMINI_URL,
                json={
                    "contents": [{"parts": parts}],
                    "generationConfig": {"temperature": 0.0, "maxOutputTokens": 3000}
                },
                timeout=45
            )
            d = res.json()

        text = d["candidates"][0]["content"]["parts"][0]["text"]
        text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(text).get("subjects", [])

    # Parse all uploaded screenshots
    all_raw = []
    errors  = []
    for upload in files:
        contents = await upload.read()
        mime     = upload.content_type or "image/jpeg"
        try:
            subs = await parse_one_screenshot(contents, mime)
            all_raw.extend(subs)
        except Exception as e:
            errors.append(str(e))

    if not all_raw:
        return {"error": f"Could not read subjects from screenshots. {'; '.join(errors)}"}

    # Clean names and deduplicate
    # If same subject appears in both screenshots, keep the one with more total classes
    seen = {}
    for item in all_raw:
        clean = clean_subject_name(item["name"])
        attended = int(item.get("attended", 0))
        total    = int(item.get("total", 0))
        if total == 0:
            continue
        if clean not in seen or total > seen[clean]["total"]:
            seen[clean] = {
                "name":     clean,
                "raw_name": item["name"],
                "attended": attended,
                "total":    total
            }

    merged = list(seen.values())
    if not merged:
        return {"error": "No valid attendance data found in screenshots"}

    # Save to DB
    db            = get_db()
    user_subjects = db.table("subjects").select("*").eq("user_id", user_id).execute().data
    updated = created = 0

    for item in merged:
        clean_nm = item["name"]
        attended = item["attended"]
        total    = item["total"]

        # Find matching subject in DB (exact → partial)
        matching = None
        for s in user_subjects:
            if s["name"].lower() == clean_nm.lower():
                matching = s
                break
        if not matching:
            for s in user_subjects:
                s_words = s["name"].lower().split()
                c_words = clean_nm.lower().split()
                # Match if first 2 meaningful words overlap
                if (clean_nm.lower() in s["name"].lower() or
                        s["name"].lower() in clean_nm.lower() or
                        (len(s_words) > 1 and len(c_words) > 1 and
                         s_words[0] == c_words[0] and s_words[1] == c_words[1])):
                    matching = s
                    break

        # Create if not found
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

        if not matching:
            continue

        sub_id = matching["id"]

        # Clear old records
        db.table("attendance_records").delete().eq("subject_id", sub_id).execute()

        # Insert attended records
        for i in range(attended):
            db.table("attendance_records").insert({
                "subject_id": sub_id,
                "date":       f"2026-01-{(i % 28) + 1:02d}",
                "status":     "attended"
            }).execute()

        # Insert bunked records
        for i in range(total - attended):
            db.table("attendance_records").insert({
                "subject_id": sub_id,
                "date":       f"2026-02-{(i % 28) + 1:02d}",
                "status":     "bunked"
            }).execute()

        updated += 1

    db.table("users").update({"onboarded": True}).eq("id", user_id).execute()

    return {
        "message":        f"Imported {updated} subjects ({created} new)",
        "subjects_found": len(merged),
        "subjects": [
            {
                "name":     s["name"],
                "attended": s["attended"],
                "total":    s["total"],
                "pct":      round(s["attended"] / s["total"] * 100, 1) if s["total"] > 0 else 0
            }
            for s in merged
        ]
    }
