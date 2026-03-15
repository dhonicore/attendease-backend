import os
from fastapi import APIRouter
from database import get_db
from anthropic import Anthropic

router = APIRouter()
client = Anthropic()

@router.get("/ai/verdict/{user_id}")
def get_verdict(user_id: str, min_attendance: int = 75):
    db = get_db()
    subjects = db.table("subjects").select("*").eq("user_id", user_id).execute().data

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

Reply ONLY in this JSON format, no markdown:
{{
  "overall_verdict": "one punchy sentence about their situation",
  "overall_score": <1-10>,
  "advice": "two sentences on what they should do this week"
}}"""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )

    import json
    text = response.content[0].text.strip()
    return json.loads(text)