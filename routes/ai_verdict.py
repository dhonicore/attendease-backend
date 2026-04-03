import os
import json
import httpx
import math
from fastapi import APIRouter
from database import get_db
from datetime import date, timedelta

router = APIRouter()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"

DEFAULT_SEMESTER_END = date(2026, 5, 16)


def get_semester_end(db, user_id: str) -> date:
    try:
        row = db.table("semester_config").select("semester_end").eq("user_id", user_id).execute()
        if row.data and row.data[0].get("semester_end"):
            return date.fromisoformat(row.data[0]["semester_end"])
    except Exception:
        pass
    return DEFAULT_SEMESTER_END


def get_user_schedule(db, user_id: str) -> dict:
    rows = db.table("timetable").select("subject_id, day_of_week").eq("user_id", user_id).execute()
    schedule = {}
    for row in (rows.data or []):
        sid = row["subject_id"]
        try:
            day = int(row["day_of_week"])
        except (ValueError, TypeError):
            continue
        if sid not in schedule:
            schedule[sid] = []
        if day not in schedule[sid]:
            schedule[sid].append(day)
    return schedule


def count_remaining_days(from_date: date, to_date: date, holidays: set) -> dict:
    counts = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    current = from_date + timedelta(days=1)
    while current <= to_date:
        wd = current.weekday()
        if wd < 6 and current not in holidays:
            counts[wd] = counts.get(wd, 0) + 1
        current += timedelta(days=1)
    return counts


def calculate_subject_advice(
    subject_id, subject_name, attended, total,
    min_attendance, schedule_days, remaining_by_day, holidays, today
) -> dict:
    current_pct = round((attended / total * 100), 1) if total > 0 else 0
    remaining_classes = sum(remaining_by_day.get(d, 0) for d in schedule_days)
    total_by_end = total + remaining_classes
    min_att = min_attendance / 100

    # FIX: correct safe bunks formula
    safe_bunks_left = max(0, math.floor((attended - min_att * total_by_end) / 1))
    # More precise: how many can I miss from remaining and still hit min_att at semester end
    # attended must be >= min_att * (total + remaining - bunks_taken)
    # => bunks_taken <= attended/min_att - total - remaining + bunks_taken ... solve properly:
    # max_total_misses = total_by_end - ceil(attended / min_att) but cap at remaining
    if min_att > 0:
        max_absences_allowed = math.floor(attended / min_att * (1 - min_att))
        absences_so_far = total - attended
        safe_bunks_left = max(0, max_absences_allowed - absences_so_far)
    else:
        safe_bunks_left = remaining_classes

    needed = 0
    if current_pct < min_attendance and total > 0:
        denom = 1 - min_att
        if denom > 0:
            needed = max(0, math.ceil((min_att * total - attended) / denom))

    if current_pct >= min_attendance + 10:
        status = "safe"
    elif current_pct >= min_attendance:
        status = "borderline"
    else:
        status = "danger"

    today_wd = today.weekday()
    has_class_today = today_wd in schedule_days and today not in holidays and today_wd < 6
    skip_safe = has_class_today and safe_bunks_left > 0 and current_pct > min_attendance + 5

    return {
        "name":              subject_name,
        "subject_id":        subject_id,
        "attended":          attended,
        "total":             total,
        "percentage":        current_pct,
        "remaining_classes": remaining_classes,
        "safe_bunks_left":   safe_bunks_left,
        "needs_to_recover":  needed,
        "has_class_today":   has_class_today,
        "skip_safe_today":   skip_safe,
        "status":            status,
    }


@router.get("/ai/verdict/{user_id}")
async def get_verdict(user_id: str, min_attendance: int = 75):
    db = get_db()

    subjects = db.table("subjects").select("*").eq("user_id", user_id).execute().data
    if not subjects:
        return {
            "overall_verdict": "Add your subjects first.",
            "overall_score":   5,
            "advice":          "Go through onboarding to set up your subjects.",
            "subject_advice":  [],
            "today_summary":   "No subjects added yet.",
            "days_left":       0,
        }

    # Get user's min_attendance from profile
    try:
        user_row = db.table("users").select("min_attendance").eq("id", user_id).execute()
        if user_row.data and user_row.data[0].get("min_attendance"):
            min_attendance = int(user_row.data[0]["min_attendance"])
    except Exception:
        pass

    schedule_by_subject = get_user_schedule(db, user_id)

    today = date.today()
    holiday_rows = db.table("holidays").select("date").eq("user_id", user_id).execute().data
    holidays = set()
    for h in (holiday_rows or []):
        try:
            holidays.add(date.fromisoformat(h["date"]))
        except Exception:
            pass

    semester_end = get_semester_end(db, user_id)
    remaining_by_day = count_remaining_days(today, semester_end, holidays)
    days_left = sum(remaining_by_day.values())

    subject_advice = []
    total_attended = total_classes = 0

    for subject in subjects:
        records  = db.table("attendance_records").select("status").eq("subject_id", subject["id"]).execute().data
        attended = len([r for r in records if r["status"] == "attended"])
        total    = len([r for r in records if r["status"] != "cancelled"])
        total_attended += attended
        total_classes  += total

        schedule_days = schedule_by_subject.get(subject["id"], [])

        if total == 0:
            subject_advice.append({
                "name":              subject["name"],
                "subject_id":        subject["id"],
                "attended":          0,
                "total":             0,
                "percentage":        0,
                "remaining_classes": sum(remaining_by_day.get(d, 0) for d in schedule_days),
                "safe_bunks_left":   0,
                "needs_to_recover":  0,
                "has_class_today":   today.weekday() in schedule_days and today not in holidays,
                "skip_safe_today":   False,
                "status":            "borderline",
            })
            continue

        advice = calculate_subject_advice(
            subject_id       = subject["id"],
            subject_name     = subject["name"],
            attended         = attended,
            total            = total,
            min_attendance   = min_attendance,
            schedule_days    = schedule_days,
            remaining_by_day = remaining_by_day,
            holidays         = holidays,
            today            = today,
        )
        subject_advice.append(advice)

    overall_pct     = round((total_attended / total_classes * 100), 1) if total_classes > 0 else 0
    danger_subjects = [s for s in subject_advice if s["status"] == "danger"]
    today_classes   = [s for s in subject_advice if s.get("has_class_today")]
    skippable       = [s for s in today_classes if s.get("skip_safe_today")]
    must_attend     = [s for s in today_classes if not s.get("skip_safe_today")]

    day_name = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"][today.weekday()]
    if today in holidays:
        today_summary = f"Today is a holiday ({day_name}). No classes!"
    elif not today_classes:
        today_summary = f"No classes on {day_name}. Enjoy the day!"
    else:
        class_names = ", ".join(s["name"].split()[0] for s in today_classes)
        if skippable:
            skip_names = ", ".join(s["name"].split()[0] for s in skippable)
            must_names = ", ".join(s["name"].split()[0] for s in must_attend) if must_attend else "none"
            today_summary = f"{day_name}: {len(today_classes)} classes ({class_names}). Safe to skip: {skip_names}. Must attend: {must_names}."
        else:
            today_summary = f"{day_name}: {len(today_classes)} classes. Attend all — {class_names}."

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

    overall_verdict = ""
    advice_text = ""
    try:
        subjects_summary = "\n".join([
            f"- {s['name']}: {s['percentage']}% | bunks left: {s['safe_bunks_left']} | status: {s['status']}"
            for s in subject_advice
        ])

        # FIX: strict JSON-only prompt, short limits so it never gets cut off
        prompt = f"""You are a brutally honest Gen-Z attendance advisor for Indian college students.

Today: {day_name}, {today.strftime('%d %b %Y')}
Overall: {overall_pct}%
Semester ends: {semester_end.strftime('%d %b %Y')} ({days_left} working days left)
Min required: {min_attendance}%

Subjects:
{subjects_summary}

Today: {today_summary}

Rules:
- overall_verdict: max 10 words, casual Indian college tone, no cringe
- advice: max 12 words, specific to today's situation
- Reply ONLY with this exact JSON, nothing before or after it:
{{"overall_verdict": "...", "advice": "..."}}"""

        async with httpx.AsyncClient() as client:
            res = await client.post(
                GEMINI_URL,
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    # FIX: was 200 — too low, caused cutoff. 300 is safe for short JSON
                    "generationConfig": {
                        "temperature": 0.7,
                        "maxOutputTokens": 300,
                        "stopSequences": ["}}\n", "}}\r"]
                    }
                },
                timeout=25
            )
            d    = res.json()
            text = d["candidates"][0]["content"]["parts"][0]["text"]
            text = text.replace("```json", "").replace("```", "").strip()

            # FIX: if response got cut mid-JSON, patch it
            if text.count('"') % 2 != 0 or not text.endswith("}"):
                text = text.rsplit(",", 1)[0] + "}"
                if not text.startswith("{"):
                    raise ValueError("Unparseable JSON from Gemini")

            parsed = json.loads(text)
            overall_verdict = parsed.get("overall_verdict", "").strip()
            advice_text     = parsed.get("advice", "").strip()

    except Exception:
        if not danger_subjects:
            overall_verdict = f"Solid at {overall_pct}% — keep it up."
        elif len(danger_subjects) == 1:
            overall_verdict = f"{danger_subjects[0]['name'].split()[0]} is cooked. Fix it."
        else:
            overall_verdict = f"{len(danger_subjects)} subjects in danger. Start attending."
        advice_text = today_summary

    return {
        "overall_verdict": overall_verdict,
        "overall_score":   score,
        "advice":          advice_text,
        "today_summary":   today_summary,
        "days_left":       days_left,
        "overall_pct":     overall_pct,
        "semester_end":    semester_end.strftime("%d %b %Y"),
        "subject_advice":  subject_advice,
    }


@router.get("/ai/holidays/{user_id}")
async def get_holidays(user_id: str):
    db    = get_db()
    today = date.today()

    rows = db.table("holidays").select("date,name").eq("user_id", user_id).execute().data
    if not rows:
        return {"this_week": [], "this_month": [], "rest_of_semester": [], "total": 0}

    holidays = []
    for h in rows:
        try:
            d = date.fromisoformat(h["date"])
            if d >= today:
                holidays.append({"date": h["date"], "name": h["name"], "day": d.strftime("%A")})
        except Exception:
            pass

    holidays.sort(key=lambda x: x["date"])

    week_end  = today + timedelta(days=7)
    month_end = today.replace(day=1) + timedelta(days=32)
    month_end = month_end.replace(day=1) - timedelta(days=1)

    this_week  = [h for h in holidays if date.fromisoformat(h["date"]) <= week_end]
    this_month = [h for h in holidays if date.fromisoformat(h["date"]) <= month_end]
    rest       = holidays

    return {
        "this_week":        this_week,
        "this_month":       this_month,
        "rest_of_semester": rest,
        "total":            len(rest)
    }