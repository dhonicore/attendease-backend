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
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(contents))
        for page in reader.pages:
            extracted = page.extract_text()
            if extracted:
                pdf_text += extracted + "\n"
    except Exception:
        pdf_text = ""

    if len(pdf_text.strip()) >= 100:
        parts = [{"text": f"""From this timetable, extract section names and subject names only.
Return ONLY this JSON, no markdown:
{{
  "sections": ["A", "B", "C"],
  "subjects_by_section": {{
    "A": ["Applied Mathematics II", "Applied Chemistry", "Introduction to AI and Applications"]
  }}
}}
Use the SAME subjects for ALL sections since they share the same curriculum.
Only list unique section letters found in the document.

Timetable text:
{pdf_text[:6000]}"""}]
    else:
        b64 = base64.b64encode(contents).decode()
        mime = file.content_type or "application/pdf"
        parts = [
            {"text": "Extract all sections and subjects from this timetable. Return ONLY JSON no markdown: {\"sections\": [\"A\", \"B\"], \"subjects_by_section\": {\"A\": [\"Maths\", \"Physics\", \"Chemistry\"]}}"},
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
        return {"error": str(e), "pdf_length": len(pdf_text), "raw": str(data)[:300]}

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
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(contents))
        for page in reader.pages:
            extracted = page.extract_text()
            if extracted:
                pdf_text += extracted + "\n"
    except Exception:
        pdf_text = ""

    if len(pdf_text.strip()) >= 100:
        parts = [{"text": f"""Extract all holidays from this college academic calendar.
Return ONLY valid JSON, no markdown:
{{
  "holidays": [{{"date": "2026-01-26", "name": "Republic Day"}}],
  "semester_end": "2026-05-15"
}}
Use YYYY-MM-DD format for all dates.

Calendar text:
{pdf_text[:8000]}"""}]
    else:
        b64 = base64.b64encode(contents).decode()
        mime = file.content_type or "application/pdf"
        parts = [
            {"text": "Extract all holidays from this academic calendar. Return ONLY JSON: {\"holidays\": [{\"date\": \"2026-01-26\", \"name\": \"Republic Day\"}], \"semester_end\": \"2026-05-15\"}"},
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
                "date": holiday["date"],
                "name": holiday["name"]
            }).execute()
        return {"message": "COE parsed", "holidays_added": len(result.get("holidays", [])), "data": result}
    except Exception as e:
        return {"error": str(e)}


# ── SCREENSHOT HELPER ──────────────────────────────────────────────────────────

def clean_subject_name(raw_name: str) -> str:
    """
    Convert portal subject names to clean display names.
    - Strips subject codes like [ 1BPLC205B ] or [ 1BPLC205B (P) ]
    - If code ends with (P) → append ' Lab' to the name
    - Strips extra whitespace
    Examples:
      'Python Programming [ 1BPLC205B ]'      → 'Python Programming'
      'Python Programming [ 1BPLC205B (P) ]'  → 'Python Programming Lab'
      'Applied Chemistry [ 1BCHES202 (P) ]'   → 'Applied Chemistry Lab'
    """
    import re
    is_lab = bool(re.search(r'\(\s*P\s*\)', raw_name, re.IGNORECASE))
    # Remove the bracketed code entirely
    name = re.sub(r'\[.*?\]', '', raw_name).strip()
    # Remove any leftover parens like (P) outside brackets
    name = re.sub(r'\(\s*P\s*\)', '', name, flags=re.IGNORECASE).strip()
    # Remove trailing punctuation/dashes
    name = name.strip(' -–—')
    if is_lab:
        name = name + " Lab"
    return name


async def parse_single_screenshot(image_bytes: bytes, mime_type: str) -> list:
    """
    Send one screenshot to Gemini and get back a list of subjects with attendance.
    Returns list of dicts: {name, code, attended, total, percentage, is_lab}
    """
    b64 = base64.b64encode(image_bytes).decode()
    parts = [
        {
            "text": """This is a screenshot from a college attendance portal.
Extract attendance for every subject listed.
Subject names may have a code in brackets like [ 1BPLC205B ] or [ 1BPLC205B (P) ].
The (P) means it is a practical/lab subject.

Return ONLY valid JSON, no markdown:
{
  "subjects": [
    {"name": "Python Programming [ 1BPLC205B ]", "attended": 9, "total": 11, "percentage": 81.82},
    {"name": "Python Programming [ 1BPLC205B (P) ]", "attended": 1, "total": 2, "percentage": 50.0}
  ]
}

Include ALL subjects visible, both theory and lab (P) ones."""
        },
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
        data = res.json()

    text = data["candidates"][0]["content"]["parts"][0]["text"]
    text = text.replace("```json", "").replace("```", "").strip()
    result = json.loads(text)
    return result.get("subjects", [])


def merge_screenshots(list1: list, list2: list) -> list:
    """
    Merge two subject lists from two screenshots.
    Deduplicates by cleaned name — keeps first occurrence.
    """
    seen = {}
    for item in list1 + list2:
        clean = clean_subject_name(item["name"])
        if clean not in seen:
            item["_clean_name"] = clean
            seen[clean] = item
    return list(seen.values())


# ── SCREENSHOT ENDPOINT ────────────────────────────────────────────────────────

@router.post("/onboarding/screenshot/{user_id}")
async def parse_screenshot(user_id: str, files: List[UploadFile] = File(...)):
    """
    Accepts 1 or 2 screenshots. Merges results and saves to DB.
    Theory subjects: saved as-is (e.g. 'Python Programming')
    Lab subjects:    saved with 'Lab' suffix (e.g. 'Python Programming Lab')
    """
    all_subjects = []

    for upload in files:
        contents = await upload.read()
        mime = upload.content_type or "image/jpeg"
        try:
            subjects = await parse_single_screenshot(contents, mime)
            all_subjects.extend(subjects)
        except Exception as e:
            # If one screenshot fails, continue with the other
            continue

    if not all_subjects:
        return {"error": "Could not read any subjects from the screenshots"}

    # Merge & deduplicate
    merged = merge_screenshots(all_subjects, [])

    # Save to DB
    db = get_db()
    user_subjects = db.table("subjects").select("*").eq("user_id", user_id).execute().data

    updated = 0
    created = 0

    for item in merged:
        clean_name = item["_clean_name"]
        attended   = int(item.get("attended", 0))
        total      = int(item.get("total", 0))

        if total == 0:
            continue

        # Find matching existing subject (fuzzy: check if names overlap)
        matching = next(
            (s for s in user_subjects
             if s["name"].lower() == clean_name.lower()
             or clean_name.lower() in s["name"].lower()
             or s["name"].lower() in clean_name.lower()),
            None
        )

        if not matching:
            # Create the subject first
            new_sub = db.table("subjects").insert({
                "user_id": user_id,
                "name": clean_name,
                "color": "#00ff88"
            }).execute()
            if new_sub.data:
                matching = new_sub.data[0]
                user_subjects.append(matching)
                created += 1

        if matching:
            sub_id = matching["id"]
            # Insert attendance records
            # Attended classes → status: attended
            for i in range(attended):
                db.table("attendance_records").insert({
                    "subject_id": sub_id,
                    "date": f"2026-01-{(i % 28) + 1:02d}",
                    "status": "attended"
                }).execute()
            # Bunked classes → status: bunked
            bunked = total - attended
            for i in range(bunked):
                db.table("attendance_records").insert({
                    "subject_id": sub_id,
                    "date": f"2026-02-{(i % 28) + 1:02d}",
                    "status": "bunked"
                }).execute()
            updated += 1

    return {
        "message": f"Imported attendance for {updated} subjects ({created} new subjects created)",
        "subjects_found": len(merged),
        "subjects": [{"name": s["_clean_name"], "attended": s.get("attended"), "total": s.get("total")} for s in merged]
    }