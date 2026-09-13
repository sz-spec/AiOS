"""
Auth Routes - Login and registration endpoints.
Handles user authentication with JWT tokens.
"""

import os
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr

from models.user import get_user_by_email, create_user

router = APIRouter(prefix="/api/auth", tags=["Auth"])

JWT_SECRET = os.getenv("JWT_SECRET_KEY", "change-me-in-production")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRATION = int(os.getenv("JWT_EXPIRATION_MINUTES", "60"))


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


def _create_token(user_id: str, email: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRATION),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


@router.post("/login")
async def login(data: LoginRequest):
    user = await get_user_by_email(data.email)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not bcrypt.checkpw(data.password.encode(), user["password_hash"].encode()):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = _create_token(user["id"], user["email"])
    return {"token": token, "user": {"id": user["id"], "email": user["email"]}}


@router.post("/register", status_code=201)
async def register(data: RegisterRequest):
    existing = await get_user_by_email(data.email)
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    password_hash = bcrypt.hashpw(data.password.encode(), bcrypt.gensalt()).decode()
    user = await create_user(email=data.email, password_hash=password_hash)

    token = _create_token(user["id"], user["email"])
    return {"token": token, "user": {"id": user["id"], "email": user["email"]}}
