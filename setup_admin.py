"""Local one-time setup; never accepts passwords on argv."""
import asyncio
import getpass
from database import db


async def setup():
    await db.init_db()
    username = input("Имя первого администратора [admin]: ").strip() or "admin"
    password = getpass.getpass("Пароль (не менее 12 символов): ")
    if password != getpass.getpass("Повторите пароль: "):
        raise SystemExit("Пароли не совпадают")
    await db.bootstrap_admin(username, password)
    print("Администратор создан. Теперь можно войти в панель.")


if __name__ == "__main__":
    asyncio.run(setup())
