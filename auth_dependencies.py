from fastapi import Depends, HTTPException, Request
from database import db
from models import User, UserRole
from typing import Optional

SESSION_COOKIE_NAME = "avito_session"
CSRF_COOKIE_NAME = "avito_csrf"

async def get_optional_user(request: Request) -> Optional[User]:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        auth_hdr = request.headers.get("Authorization")
        if auth_hdr and auth_hdr.startswith("Bearer "):
            token = auth_hdr[7:].strip()
    if not token:
        return None
    return await db.get_user_by_session(token)

async def require_auth(request: Request) -> User:
    user = await get_optional_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    return user


async def require_admin(user: User = Depends(require_auth)) -> User:
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Доступ разрешен только администраторам")
    return user

