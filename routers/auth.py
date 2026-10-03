from fastapi import APIRouter, Depends, HTTPException, Request, Response
from database import Database
from dependencies import get_database
from config import config
from auth_dependencies import (
    SESSION_COOKIE_NAME,
    CSRF_COOKIE_NAME,
    require_auth,
    require_admin,
)
from security import login_limiter
from models import LoginRequest, ChangePasswordRequest, User, UserCreate, UserUpdate
import secrets
import logging

logger = logging.getLogger("AvitoAuth")
router = APIRouter()


@router.post("/api/auth/login")
async def api_auth_login(req: LoginRequest, response: Response, request: Request, db: Database=Depends(get_database)):
    address = request.client.host if request.client else "unknown"
    if not login_limiter.allow(address):
        raise HTTPException(
            status_code=429,
            detail="Слишком много попыток входа. Повторите через минуту.",
            headers={"Retry-After": "60"},
        )
    user = await db.authenticate_user(req.username, req.password)
    if not user:
        raise HTTPException(
            status_code=401, detail="Неверное имя пользователя или пароль"
        )
    token = await db.create_session(user.id, days_valid=14)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=14 * 86400,
        httponly=True,
        secure=config.web.secure_cookies,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE_NAME,
        secrets.token_urlsafe(32),
        max_age=14 * 86400,
        secure=config.web.secure_cookies,
        samesite="strict",
        path="/",
    )
    logger.info(
        f"🔑 Успешный вход пользователя '{user.username}' (роль: {user.role.value})"
    )
    return {"status": "success", "user": user.model_dump(mode="json"), "token": token}


@router.post("/api/auth/logout")
async def api_auth_logout(request: Request, response: Response, db: Database=Depends(get_database)):
    token = request.cookies.get(SESSION_COOKIE_NAME) or request.headers.get(
        "authorization", ""
    ).removeprefix("Bearer ")
    if token:
        await db.delete_session(token)
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    response.delete_cookie(key=CSRF_COOKIE_NAME, path="/")
    return {"status": "success"}


@router.get("/api/auth/me")
async def api_auth_me(user: User = Depends(require_auth)):
    return {"user": user.model_dump(mode="json")}


@router.post("/api/auth/change-password")
async def api_auth_change_password(req: ChangePasswordRequest, user: User=Depends(require_auth), db: Database=Depends(get_database)):
    ok, msg = await db.change_password(user.id, req.old_password, req.new_password)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "success", "message": msg}


# ==============================================================================
# REST API: АДМИНИСТРИРОВАНИЕ ПОЛЬЗОВАТЕЛЕЙ
# ==============================================================================


@router.get("/api/admin/users")
async def api_admin_list_users(admin: User=Depends(require_admin), db: Database=Depends(get_database)):
    users = await db.get_all_users()
    return {"users": [u.model_dump(mode="json") for u in users]}


@router.post("/api/admin/users")
async def api_admin_create_user(req: UserCreate, admin: User=Depends(require_admin), db: Database=Depends(get_database)):
    ok, msg, user_id = await db.create_user(req.username, req.password, req.role)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    logger.info(
        f"👤 Админ '{admin.username}' создал пользователя '{req.username}' (роль: {req.role.value})"
    )
    return {"status": "success", "message": msg, "user_id": user_id}


@router.put("/api/admin/users/{user_id}")
async def api_admin_update_user(user_id: int, req: UserUpdate, admin: User=Depends(require_admin), db: Database=Depends(get_database)):
    ok, msg = await db.update_user(
        user_id=user_id,
        username=req.username,
        role=req.role,
        is_active=req.is_active,
        new_password=req.password,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    logger.info(f"👤 Админ '{admin.username}' обновил пользователя ID {user_id}")
    return {"status": "success", "message": msg}


@router.delete("/api/admin/users/{user_id}")
async def api_admin_delete_user(user_id: int, admin: User=Depends(require_admin), db: Database=Depends(get_database)):
    ok, msg = await db.delete_user(user_id)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    logger.info(f"👤 Админ '{admin.username}' удалил пользователя ID {user_id}")
    return {"status": "success", "message": msg}
