import os
import json
import httpx
from fastapi import APIRouter
from database import get_db

router = APIRouter()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"

@router.get("/ai/verdict/{user_id}")
async def get_verdict(user_id: str, min_attendance: int = 75):
    db = get_db()
    subjects = db.table("subjects").select("*").eq("user_id", user_id).execute().data

    if not subjects:
        return {"overall_verdict": "Add subjects to get your verdict.", "overall_score": 5, "advice": ""}

    subject_summaries = []
    for subject in subjects:
        records = db.table("attendance_records").select("*").eq("subject_id", subject["id"]).execute().data
        attended = len([r for r in records if r["status"] == "attended"])
        total = len([r for r in records if r["status"] != "cancelled"])
        pct = round((attended / total * 100), 1) if total > 0 else 0
        subject_summaries.append(f"{subject['name']}: {pct}% ({attended}/{total})")

    prompt = f"""You are a brutally honest attendance advisor for Indian college students. Min attendance required: {min_attendance}%.

Subjects:
{chr(10).join(subject_summaries)}

Reply ONLY in this exact JSON format, no markdown, no extra text:
{{"overall_verdict": "one punchy sentence about their situation", "overall_score": 7, "advice": "two sentences on what they should do this week"}}"""

    async with httpx.AsyncClient() as client:
        res = await client.post(
            GEMINI_URL,
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.7, "maxOutputTokens": 500}
            },
            timeout=30
        )
        data = res.json()

    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except Exception as e:
        return {"overall_verdict": "Could not get verdict right now.", "overall_score": 5, "advice": "Check back later."}