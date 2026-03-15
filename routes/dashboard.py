from fastapi import APIRouter
from database import get_db

router = APIRouter()

@router.get("/dashboard/{user_id}")
def get_dashboard(user_id: str, min_attendance: int = 75):
    db = get_db()

    subjects = db.table("subjects")\
        .select("*")\
        .eq("user_id", user_id)\
        .execute().data

    dashboard_subjects = []
    total_attended = 0
    total_classes = 0
    danger_count = 0

    for subject in subjects:
        records = db.table("attendance_records")\
            .select("*")\
            .eq("subject_id", subject["id"])\
            .execute().data

        attended = len([r for r in records if r["status"] == "attended"])
        total = len([r for r in records if r["status"] != "cancelled"])
        pct = round((attended / total * 100), 1) if total > 0 else 0

        if pct >= min_attendance + 10:
            status = "safe"
        elif pct >= min_attendance:
            status = "borderline"
        else:
            status = "danger"
            danger_count += 1

        can_bunk = max(0, int(attended - (min_attendance / 100) * total))
        needs = max(0, int((min_attendance / 100) * total - attended) + 1)

        total_attended += attended
        total_classes += total

        dashboard_subjects.append({
            "id": subject["id"],
            "name": subject["name"],
            "color": subject["color"],
            "attended": attended,
            "total": total,
            "percentage": pct,
            "status": status,
            "can_bunk": can_bunk,
            "needs": needs
        })

    overall_pct = round((total_attended / total_classes * 100), 1) if total_classes > 0 else 0

    return {
        "overall_percentage": overall_pct,
        "danger_count": danger_count,
        "total_subjects": len(subjects),
        "subjects": dashboard_subjects
    }