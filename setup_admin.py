"""Local one-time setup; never accepts passwords on argv."""
import asyncio
import getpass
import argparse
from database import db
from models import UserRole


async def setup():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reset-password',action='store_true',help='Локально сменить пароль существующего администратора')
    args=parser.parse_args()
    await db.init_db()
    username = input("Имя первого администратора [admin]: ").strip() or "admin"
    password = getpass.getpass("Пароль (не менее 12 символов): ")
    if password != getpass.getpass("Повторите пароль: "):
        raise SystemExit("Пароли не совпадают")
    if args.reset_password:
        users=await db.get_all_users()
        user=next((user for user in users if user.username==username),None)
        if not user:
            raise SystemExit('Пользователь не найден')
        ok,message=await db.update_user(user.id,new_password=password,is_active=True,role=UserRole.ADMIN)
        if not ok:
            raise SystemExit(message)
    else:
        await db.bootstrap_admin(username, password)
    print("Администратор готов. Теперь можно войти в панель.")


if __name__ == "__main__":
    asyncio.run(setup())
