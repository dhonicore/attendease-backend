import os
import json
import httpx
from fastapi import APIRouter
from database import get_db
from datetime import date, timedelta

router = APIRouter()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"

# ── SECTION A TIMETABLE (CMRIT 2nd Sem) ──────────────────────────────────────
# Monday=0, Tuesday=1, Wednesday=2, Thursday=3, Friday=4, Saturday=5
# Each subject mapped to which days it appears
# Based on the actual timetable PDF

SECTION_A_TIMETABLE = {
    "Applied Mathematics II":                  [0, 1, 2, 3, 5],   # Mon Tue Wed Thu Sat
    "Applied Chemistry for Smart Systems":     [0, 1, 2, 3, 4],   # Mon Tue Wed Thu Fri
    "Introduction to AI and Applications":     [1, 2, 3, 4],      # Tue Wed Thu Fri
    "Introduction to Electrical Engineering":  [0, 2, 3, 4, 5],   # Mon Wed Thu Fri Sat
    "Python Programming":                      [0, 1, 3, 4, 5],   # Mon Tue Thu Fri Sat
    "Communication Skills":                    [0, 4],             # Mon Fri
    "Indian Constitution and Engineering Ethics": [1, 2, 3],      # Tue Wed Thu
    "TYL Aptitude":                            [1],                # Tue
    "Interdisciplinary Project Based Learning":[2, 3, 4],          # Wed Thu Fri
    # Labs appear once a week in rotation — treat as 1x per week
    "Applied Chemistry Lab":                   [0],                # Mon (rotation)
    "Python Programming Lab":                  [1],                # Tue (rotation)
    "Applied Mathematics Lab":                 [2],                # Wed (rotation)
}

# Semester end date
SEMESTER_END = date(2026, 5, 16)

# Subjects that have Saturday classes
SATURDAY_SUBJECTS = {
    "Applied Mathematics II",
    "Introduction to Electrical Engineering",
    "Python Programming",
}


def get_subject_schedule(subject_name: str) -> list:
    """
    Fuzzy match subject name to timetable entry.
    Returns list of weekday numbers (0=Mon, 5=Sat).
    """
    name_lower = subject_name.lower()
    for key, days in SECTION_A_TIMETABLE.items():
        if (key.lower() in name_lower or
            name_lower in key.lower() or
            any(word in name_lower for word in key.lower().split() if len(word) > 4)):
            return days
    # Default: assume 3 classes per week if not found
    return [0, 2, 4]


def count_working_days(from_date: date, to_date: date, holidays: set) -> dict:
    """
    Count Mon-Sat working days from from_date to to_date (exclusive of today),
    minus holidays.
    Returns dict of {weekday: count} for days 0-5.
    """
    counts = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    current = from_date + timedelta(days=1)  # Start from tomorrow
    while current <= to_date:
        wd = current.weekday()
        if wd < 6 and current not in holidays:  # Mon-Sat only
            counts[wd] = counts.get(wd, 0) + 1
        current += timedelta(days=1)
    return counts


def calculate_bunk_advice(
    subject_name: str,
    attended: int,
    total: int,
    min_attendance: float,
    remaining_by_day: dict,
    holidays: set,
    today: date
) -> dict:
    """
    For a subject, calculate:
    - Current %
    - Classes remaining this semester
    - Safe bunks left (can miss N more and still be >= min_attendance)
    - Classes needed to recover (if below min)
    - Whether today is a class day and if it's safe to skip
    """
    schedule = get_subject_schedule(subject_name)

    # Count remaining classes this semester
    remaining_classes = sum(remaining_by_day.get(day, 0) for day in schedule)

    total_by_end = total + remaining_classes
    current_pct = round((attended / total * 100), 1) if total > 0 else 0

    # How many can we miss and still hit min_attendance at semester end?
    # (attended + future_attended) / total_by_end >= min_attendance/100
    # future_attended = total_by_end * (min_att/100) - attended
    min_att = min_attendance / 100
    min_needed_total = int(min_att * total_by_end) + 1  # +1 to be safe
    can_miss = max(0, (total + remaining_classes) - min_needed_total - (total - attended))
    # Simpler: max bunks = attended - ceil(min_att * total_by_end)
    import math
    safe_bunks_left = max(0, attended - math.ceil(min_att * total_by_end))

    # Classes needed to recover (if below min right now)
    if current_pct < min_attendance:
        # How many consecutive classes to hit min_attendance?
        # (attended + x) / (total + x) >= min_att
        # attended + x >= min_att * total + min_att * x
        # x(1 - min_att) >= min_att * total - attended
        needed = 0
        if (1 - min_att) > 0:
            needed = max(0, math.ceil((min_att * total - attended) / (1 - min_att)))
    else:
        needed = 0

    # Is today a class day for this subject?
    today_wd = today.weekday()
    has_class_today = today_wd in schedule and today not in holidays and today_wd < 6

    # Should skip today?
    # Safe to skip if: has class today AND safe_bunks_left > 0 AND current_pct > min_attendance + 5
    skip_safe = has_class_today and safe_bunks_left > 0 and current_pct > min_attendance + 5

    # Status
    if current_pct >= min_attendance + 10:
        status = "safe"
    elif current_pct >= min_attendance:
        status = "borderline"
    else:
        status = "danger"

    return {
        "name": subject_name,
        "attended": attended,
        "total": total,
        "percentage": current_pct,
        "remaining_classes": remaining_classes,
        "safe_bunks_left": safe_bunks_left,
        "needs_to_recover": needed,
        "has_class_today": has_class_today,
        "skip_safe_today": skip_safe,
        "status": status,
    }


# ── MAIN ENDPOINT ─────────────────────────────────────────────────────────────

@router.get("/ai/verdict/{user_id}")
async def get_verdict(user_id: str, min_attendance: int = 75):
    db = get_db()

    # 1. Get subjects
    subjects = db.table("subjects").select("*").eq("user_id", user_id).execute().data
    if not subjects:
        return {
            "overall_verdict": "Add your subjects first.",
            "overall_score": 5,
            "advice": "Go to the dashboard and add your subjects.",
            "subject_advice": [],
            "today_summary": "No subjects added yet."
        }

    # 2. Get holidays from DB
    today = date.today()
    holiday_rows = db.table("holidays").select("date").eq("user_id", user_id).execute().data
    holidays = set()
    for h in holiday_rows:
        try:
            holidays.add(date.fromisoformat(h["date"]))
        except Exception:
            pass

    # 3. Count remaining working days by weekday
    remaining_by_day = count_working_days(today, SEMESTER_END, holidays)
    days_left = sum(remaining_by_day.values())

    # 4. Calculate advice for each subject
    subject_advice = []
    total_attended = 0
    total_classes = 0

    for subject in subjects:
        records = db.table("attendance_records").select("*").eq("subject_id", subject["id"]).execute().data
        attended = len([r for r in records if r["status"] == "attended"])
        total    = len([r for r in records if r["status"] != "cancelled"])

        total_attended += attended
        total_classes  += total

        if total == 0:
            subject_advice.append({
                "name": subject["name"],
                "attended": 0,
                "total": 0,
                "percentage": 0,
                "remaining_classes": 0,
                "safe_bunks_left": 0,
                "needs_to_recover": 0,
                "has_class_today": False,
                "skip_safe_today": False,
                "status": "borderline",
                "advice_text": "No attendance recorded yet."
            })
            continue

        advice = calculate_bunk_advice(
            subject_name    = subject["name"],
            attended        = attended,
            total           = total,
            min_attendance  = min_attendance,
            remaining_by_day= remaining_by_day,
            holidays        = holidays,
            today           = today
        )
        subject_advice.append(advice)

    # 5. Overall stats
    overall_pct = round((total_attended / total_classes * 100), 1) if total_classes > 0 else 0
    danger_subjects  = [s for s in subject_advice if s["status"] == "danger"]
    safe_subjects    = [s for s in subject_advice if s["status"] == "safe"]
    today_classes    = [s for s in subject_advice if s.get("has_class_today")]
    skippable_today  = [s for s in subject_advice if s.get("skip_safe_today")]
    must_attend_today= [s for s in today_classes if not s.get("skip_safe_today")]

    # 6. Build today summary (pure math, no AI)
    today_name = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"][today.weekday()]
    if not today_classes:
        today_summary = f"No classes today ({today_name}). Enjoy the day."
    else:
        class_names = ", ".join(s["name"].split()[0] for s in today_classes)
        if skippable_today:
            skip_names = ", ".join(s["name"].split()[0] for s in skippable_today)
            must_names = ", ".join(s["name"].split()[0] for s in must_attend_today) if must_attend_today else "none"
            today_summary = f"{today_name}: {len(today_classes)} classes ({class_names}). Can skip: {skip_names}. Must attend: {must_names}."
        else:
            today_summary = f"{today_name}: {len(today_classes)} classes. Attend all — {class_names}."

    # 7. Score (pure math)
    if overall_pct >= 85:
        score = 9
    elif overall_pct >= 80:
        score = 8
    elif overall_pct >= 75:
        score = 6
    elif overall_pct >= 65:
        score = 4
    else:
        score = 2

    # 8. Try Gemini for punchy verdict text (optional — math works without it)
    overall_verdict = ""
    advice_text     = ""

    try:
        subjects_summary = "\n".join([
            f"- {s['name']}: {s['percentage']}% | bunks left: {s['safe_bunks_left']} | classes left: {s['remaining_classes']} | status: {s['status']}"
            for s in subject_advice
        ])

        prompt = f"""You are a brutally honest, Gen-Z attendance advisor for Indian college students at CMRIT Bangalore.

Today: {today_name}, {today.strftime('%d %b %Y')}
Overall attendance: {overall_pct}%
Semester ends: 16 May 2026 ({days_left} working days left)
Min required: {min_attendance}%

Subjects:
{subjects_summary}

Today's situation: {today_summary}

Write TWO short things in casual Indian college student tone (not cringe, just real):
1. overall_verdict: One punchy sentence about their overall situation (max 12 words)
2. advice: One specific actionable sentence for today (max 15 words)

Reply ONLY valid JSON, no markdown:
{{"overall_verdict": "...", "advice": "..."}}"""

        async with httpx.AsyncClient() as client:
            res = await client.post(
                GEMINI_URL,
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.8, "maxOutputTokens": 200}
                },
                timeout=20
            )
            data = res.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            text = text.replace("```json", "").replace("```", "").strip()
            parsed = json.loads(text)
            overall_verdict = parsed.get("overall_verdict", "")
            advice_text     = parsed.get("advice", "")

    except Exception:
        # Gemini failed — use math-based fallback, user still gets full data
        if len(danger_subjects) == 0:
            overall_verdict = f"Looking solid at {overall_pct}% — keep it up."
        elif len(danger_subjects) <= 2:
            names = ", ".join(s["name"].split()[0] for s in danger_subjects)
            overall_verdict = f"{names} is cooked. Fix it before May 16."
        else:
            overall_verdict = f"{len(danger_subjects)} subjects in danger — time to start attending."

        advice_text = today_summary

    return {
        "overall_verdict":  overall_verdict,
        "overall_score":    score,
        "advice":           advice_text,
        "today_summary":    today_summary,
        "days_left":        days_left,
        "overall_pct":      overall_pct,
        "subject_advice":   subject_advice,
    }
