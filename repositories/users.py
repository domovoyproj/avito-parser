import hashlib
import secrets
import os
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
import aiosqlite
from models import User, UserRole


class UsersRepository:
    @staticmethod
    def hash_password(password: str, salt_hex: Optional[str] = None) -> Tuple[str, str]:
        """Криптографическое хеширование пароля через PBKDF2-HMAC-SHA256 (100k итераций)"""
        salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
        key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100000)
        return key.hex(), salt.hex()

    @staticmethod
    def verify_password(password: str, hash_hex: str, salt_hex: str) -> bool:
        """Сверка пароля с хешем в базе"""
        salt = bytes.fromhex(salt_hex)
        check_key = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, 100000
        )
        return secrets.compare_digest(check_key.hex(), hash_hex)

    async def init_default_admin(self) -> None:
        """Bootstrap only from an explicitly configured secret."""
        async with self.connection() as db:
            row = await (await db.execute("SELECT id,password_hash,salt FROM users WHERE username='admin' AND is_active=1")).fetchone()
            if row and self.verify_password('admin123',row[1],row[2]):
                await db.execute('UPDATE users SET is_active=0 WHERE id=?',(row[0],))
                await db.execute('DELETE FROM user_sessions WHERE user_id=?',(row[0],))
                await db.commit()
        password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")
        if not password:
            return
        async with self.connection() as db:
            cursor = await db.execute("SELECT COUNT(*) FROM users")
            if (await cursor.fetchone())[0]:
                return
        await self.bootstrap_admin(
            os.getenv("BOOTSTRAP_ADMIN_USERNAME", "admin"), password
        )

    async def bootstrap_admin(self, username: str, password: str) -> None:
        if len(username.strip()) < 3 or len(password) < 12:
            raise ValueError("Имя должно содержать 3 символа, пароль — не менее 12")
        async with self.connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute("SELECT COUNT(*) FROM users")
            count_row = await cursor.fetchone()
            if count_row and count_row[0] == 0:
                pwd_hash, salt = self.hash_password(password)
                await db.execute(
                    """
                    INSERT INTO users (username, password_hash, salt, role, is_active, created_at)
                    VALUES (?, ?, ?, ?, 1, ?)
                """,
                    (
                        username.strip(),
                        pwd_hash,
                        salt,
                        UserRole.ADMIN.value,
                        datetime.now(),
                    ),
                )
                await db.commit()
            else:
                raise ValueError("Первый администратор уже настроен")

    async def authenticate_user(self, username: str, password: str) -> Optional[User]:
        """Проверка логина и пароля пользователя"""
        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM users WHERE username = ? AND is_active = 1",
                (username.strip(),),
            )
            row = await cursor.fetchone()
            if not row:
                return None

            if not self.verify_password(password, row["password_hash"], row["salt"]):
                return None

            now = datetime.now()
            await db.execute(
                "UPDATE users SET last_login_at = ? WHERE id = ?", (now, row["id"])
            )
            await db.commit()

            return User(
                id=row["id"],
                username=row["username"],
                role=UserRole(row["role"]),
                is_active=bool(row["is_active"]),
                created_at=datetime.fromisoformat(str(row["created_at"]))
                if row["created_at"]
                else None,
                last_login_at=now,
            )

    async def create_user(
        self, username: str, password: str, role: UserRole = UserRole.OPERATOR
    ) -> Tuple[bool, str, Optional[int]]:
        """Создание нового пользователя"""
        username = username.strip()
        if len(username) < 3:
            return False, "Имя пользователя должно содержать не менее 3 символов", None
        if len(password) < 12:
            return False, "Пароль должен содержать не менее 12 символов", None

        async with self.connection() as db:
            cursor = await db.execute(
                "SELECT 1 FROM users WHERE username = ?", (username,)
            )
            if await cursor.fetchone():
                return False, f"Пользователь с именем '{username}' уже существует", None

            pwd_hash, salt = self.hash_password(password)
            cur_ins = await db.execute(
                """
                INSERT INTO users (username, password_hash, salt, role, is_active, created_at)
                VALUES (?, ?, ?, ?, 1, ?)
            """,
                (username, pwd_hash, salt, role.value, datetime.now()),
            )
            await db.commit()
            return True, "Пользователь успешно создан", cur_ins.lastrowid

    async def get_all_users(self) -> List[User]:
        """Получение списка всех пользователей"""
        users: List[User] = []
        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, username, role, is_active, created_at, last_login_at FROM users ORDER BY id ASC"
            )
            rows = await cursor.fetchall()
            for r in rows:
                users.append(
                    User(
                        id=r["id"],
                        username=r["username"],
                        role=UserRole(r["role"]),
                        is_active=bool(r["is_active"]),
                        created_at=datetime.fromisoformat(str(r["created_at"]))
                        if r["created_at"]
                        else None,
                        last_login_at=datetime.fromisoformat(str(r["last_login_at"]))
                        if r["last_login_at"]
                        else None,
                    )
                )
        return users

    async def get_user_by_id(self, user_id: int) -> Optional[User]:
        """Поиск пользователя по ID"""
        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, username, role, is_active, created_at, last_login_at FROM users WHERE id = ?",
                (user_id,),
            )
            r = await cursor.fetchone()
            if r:
                return User(
                    id=r["id"],
                    username=r["username"],
                    role=UserRole(r["role"]),
                    is_active=bool(r["is_active"]),
                    created_at=datetime.fromisoformat(str(r["created_at"]))
                    if r["created_at"]
                    else None,
                    last_login_at=datetime.fromisoformat(str(r["last_login_at"]))
                    if r["last_login_at"]
                    else None,
                )
        return None

    async def update_user(
        self,
        user_id: int,
        username: Optional[str] = None,
        role: Optional[UserRole] = None,
        is_active: Optional[bool] = None,
        new_password: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Обновление данных пользователя"""
        async with self.connection() as db:
            # Проверка существования
            cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            user_row = await cursor.fetchone()
            if not user_row:
                return False, "Пользователь не найден"

            updates = []
            params = []

            if username is not None and username.strip():
                u = username.strip()
                # Проверка уникальности
                c_chk = await db.execute(
                    "SELECT 1 FROM users WHERE username = ? AND id != ?", (u, user_id)
                )
                if await c_chk.fetchone():
                    return False, f"Имя '{u}' уже занято другим пользователем"
                updates.append("username = ?")
                params.append(u)

            if role is not None:
                # Защита от снятия прав с последнего активного админа
                if user_row[4] == UserRole.ADMIN.value and role != UserRole.ADMIN:
                    c_adm = await db.execute(
                        "SELECT COUNT(*) FROM users WHERE role = ? AND is_active = 1",
                        (UserRole.ADMIN.value,),
                    )
                    cnt = (await c_adm.fetchone())[0]
                    if cnt <= 1:
                        return (
                            False,
                            "Нельзя изменить роль последнего активного администратора",
                        )
                updates.append("role = ?")
                params.append(role.value)

            if is_active is not None:
                if user_row[4] == UserRole.ADMIN.value and not is_active:
                    c_adm = await db.execute(
                        "SELECT COUNT(*) FROM users WHERE role = ? AND is_active = 1",
                        (UserRole.ADMIN.value,),
                    )
                    cnt = (await c_adm.fetchone())[0]
                    if cnt <= 1:
                        return (
                            False,
                            "Нельзя деактивировать последнего активного администратора",
                        )
                updates.append("is_active = ?")
                params.append(1 if is_active else 0)

            if new_password is not None and new_password.strip():
                if len(new_password.strip()) < 12:
                    return False, "Новый пароль должен содержать минимум 12 символов"
                pwd_hash, salt = self.hash_password(new_password.strip())
                updates.append("password_hash = ?")
                params.append(pwd_hash)
                updates.append("salt = ?")
                params.append(salt)

            if not updates:
                return True, "Нет изменений"

            params.append(user_id)
            sql = f"UPDATE users SET {', '.join(updates)} WHERE id = ?"
            await db.execute(sql, params)
            if new_password is not None or is_active is False or role is not None:
                await db.execute(
                    "DELETE FROM user_sessions WHERE user_id = ?", (user_id,)
                )
            await db.commit()
            return True, "Данные пользователя успешно обновлены"

    async def delete_user(self, user_id: int) -> Tuple[bool, str]:
        """Удаление пользователя с защитой последнего админа"""
        async with self.connection() as db:
            cursor = await db.execute("SELECT role FROM users WHERE id = ?", (user_id,))
            row = await cursor.fetchone()
            if not row:
                return False, "Пользователь не найден"

            if row[0] == UserRole.ADMIN.value:
                c_adm = await db.execute(
                    "SELECT COUNT(*) FROM users WHERE role = ? AND is_active = 1",
                    (UserRole.ADMIN.value,),
                )
                cnt = (await c_adm.fetchone())[0]
                if cnt <= 1:
                    return False, "Нельзя удалить единственного администратора системы"

            await db.execute("DELETE FROM user_sessions WHERE user_id = ?", (user_id,))
            await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
            await db.commit()
            return True, "Пользователь удален"

    async def change_password(
        self, user_id: int, old_password: str, new_password: str
    ) -> Tuple[bool, str]:
        """Смена собственного пароля пользователем"""
        if len(new_password) < 12:
            return False, "Новый пароль должен быть не менее 12 символов"

        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            user_row = await cursor.fetchone()
            if not user_row:
                return False, "Пользователь не найден"

            if not self.verify_password(
                old_password, user_row["password_hash"], user_row["salt"]
            ):
                return False, "Текущий пароль указан неверно"

            pwd_hash, salt = self.hash_password(new_password)
            await db.execute(
                "UPDATE users SET password_hash = ?, salt = ? WHERE id = ?",
                (pwd_hash, salt, user_id),
            )
            await db.execute("DELETE FROM user_sessions WHERE user_id = ?", (user_id,))
            await db.commit()
            return True, "Пароль успешно изменен"

    async def create_session(self, user_id: int, days_valid: int = 14) -> str:
        """Создание защищенного токена сессии"""
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(days=days_valid)
        async with self.connection() as db:
            await db.execute(
                """
                INSERT INTO user_sessions (token, user_id, expires_at, created_at)
                VALUES (?, ?, ?, ?)
            """,
                (token, user_id, expires_at, datetime.now()),
            )
            await db.commit()
        return token

    async def get_user_by_session(self, token: str) -> Optional[User]:
        """Получение пользователя по токену сессии"""
        if not token:
            return None
        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT u.id, u.username, u.role, u.is_active, u.created_at, u.last_login_at
                FROM user_sessions s
                JOIN users u ON s.user_id = u.id
                WHERE s.token = ? AND s.expires_at > ? AND u.is_active = 1
            """,
                (token, datetime.now()),
            )
            r = await cursor.fetchone()
            if r:
                return User(
                    id=r["id"],
                    username=r["username"],
                    role=UserRole(r["role"]),
                    is_active=bool(r["is_active"]),
                    created_at=datetime.fromisoformat(str(r["created_at"]))
                    if r["created_at"]
                    else None,
                    last_login_at=datetime.fromisoformat(str(r["last_login_at"]))
                    if r["last_login_at"]
                    else None,
                )
        return None

    async def delete_session(self, token: str) -> None:
        """Удаление сессии при выходе"""
        if not token:
            return
        async with self.connection() as db:
            await db.execute("DELETE FROM user_sessions WHERE token = ?", (token,))
            await db.commit()
