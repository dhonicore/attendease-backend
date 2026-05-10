# NoBunk Backend

FastAPI backend for NoBunk. Handles Google OAuth, onboarding persistence, attendance operations, dashboard calculations, and AI verdict generation.

## Tech Stack

- FastAPI
- Uvicorn
- Supabase Python client
- httpx
- Gemini REST integration

## Project Layout

- `main.py`: App bootstrap, CORS, router registration, health routes
- `auth.py`: Google OAuth endpoints and redirect logic
- `database.py`: Supabase client initialization
- `routes/subjects.py`: Subject CRUD routes
- `routes/attendance.py`: Attendance routes
- `routes/dashboard.py`: Attendance aggregation + risk metrics
- `routes/ai_verdict.py`: AI and holiday-aware verdict logic
- `routes/onboarding.py`: Profile and timetable onboarding endpoints
- `routes/users.py`: User fetch routes
- `routes/cleanup.py`: Maintenance/cleanup helpers

## Setup

```bash
cd attendease-backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Environment Variables

Create environment variables before running:

- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `SUPABASE_URL`
- `SUPABASE_KEY`
- `GEMINI_API_KEY`

## Run

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Health:

- `GET /health`

## API Surface (High Level)

Auth:

- `GET /auth/google`
- `GET /auth/callback`

Users:

- `GET /users/{user_id}`

Subjects:

- `POST /subjects`
- `DELETE /subjects/{id}`

Attendance:

- `POST /attendance`
- `GET /attendance/{subject_id}`

Onboarding:

- `POST /onboarding/profile`
- `POST /onboarding/save-timetable`

Dashboard and AI:

- `GET /dashboard/{user_id}`
- `GET /ai/verdict/{user_id}`
- `GET /ai/holidays/{user_id}`

## Behavior Notes

- OAuth callback redirects users based on `users.onboarded`.
- Dashboard reads `min_attendance` from user profile and computes:
  - subject percentage
  - safety status (`safe`, `borderline`, `danger`)
  - `can_bunk` and recovery `needs`
- Data access is direct Supabase table access (no ORM).

## Troubleshooting

- OAuth issues: verify Google credentials and callback URL.
- Empty dashboard: confirm subject and attendance rows exist for user.
- Persistent onboarding redirect: verify `onboarded` updates during onboarding.
- DB errors: check `SUPABASE_URL` and `SUPABASE_KEY`.
