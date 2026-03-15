from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from database import get_db
from auth import router as auth_router
from routes.subjects import router as subjects_router
from routes.attendance import router as attendance_router
from routes.dashboard import router as dashboard_router
from routes.ai_verdict import router as ai_router

load_dotenv()

app = FastAPI(title="AttendEase API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(subjects_router)
app.include_router(attendance_router)
app.include_router(dashboard_router)
app.include_router(ai_router)

@app.get("/")
def root():
    return {"message": "AttendEase API is running"}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/test-db")
def test_db():
    try:
        db = get_db()
        result = db.table("users").select("*").limit(1).execute()
        return {"status": "database connected", "data": result.data}
    except Exception as e:
        return {"status": "error", "message": str(e)}
