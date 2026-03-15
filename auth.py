import os
import httpx
from fastapi import APIRouter
from fastapi.responses import RedirectResponse
from database import get_db

router = APIRouter()

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = "http://127.0.0.1:8000/auth/callback"

@router.get("/auth/google")
def google_login():
    url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={GOOGLE_CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        "&response_type=code"
        "&scope=openid email profile"
        "&prompt=select_account"
    )
    return RedirectResponse(url)

@router.get("/auth/callback")
async def google_callback(code: str):
    async with httpx.AsyncClient() as client:
        token_res = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": REDIRECT_URI,
                "grant_type": "authorization_code",
            }
        )
        tokens = token_res.json()
        user_res = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {tokens['access_token']}"}
        )
        user_info = user_res.json()

    db = get_db()
    existing = db.table("users").select("*").eq("email", user_info["email"]).execute()

    if not existing.data:
        db.table("users").insert({
            "email": user_info["email"],
            "name": user_info.get("name", ""),
        }).execute()
        user = db.table("users").select("*").eq("email", user_info["email"]).execute().data[0]
    else:
        user = existing.data[0]

    name = user.get('name', '').replace(' ', '%20')
    return RedirectResponse(
        f"http://127.0.0.1:5500/index.html?user_id={user['id']}&name={name}&email={user['email']}"
    )
