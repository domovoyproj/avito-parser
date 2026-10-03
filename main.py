import asyncio
import os
import sys
from pathlib import Path
from rich import print
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table

from browser_engine import browser_engine
from config import config
from database import db
from exporter import exporter
from http_engine import http_engine
from models import AvitoItem, SearchQuery
from proxy_manager import proxy_manager

console = Console()


def print_banner():
    banner = """[bold cyan]
    ╔═══════════════════════════════════════════════════════════════╗
    ║                 🚀 AVITO PARSER & MONITOR                    ║
    ║   Веб-Панель | Парсинг выдачи | Карточки | Telegram-бот       ║
    ╚═══════════════════════════════════════════════════════════════╝
    [/bold cyan]"""
    console.print(banner)


def show_menu():
    table = Table(show_header=False, border_style="dim cyan", box=None)
    table.add_column("Key", style="bold yellow", width=4)
    table.add_column("Action", style="bold white")

    table.add_row("[1]", "🌐 Запустить Веб-Панель управления (FastAPI Web UI)")
    table.add_row("[2]", "🔍 Спарсить поисковую выдачу (в Excel / CSV / JSON)")
    table.add_row("[3]", "📦 Спарсить детальную карточку объявления (фото, характеристики)")
    table.add_row("[4]", "🤖 Запустить Telegram-бота мониторинга")
    table.add_row("[5]", "🔄 Запустить консольный мониторинг (поиск новинок и скидок)")
    table.add_row("[6]", "📊 Выгрузить базу данных в Excel")
    table.add_row("[7]", "🌐 Управление и проверка прокси")
    table.add_row("[8]", "⚙️  Просмотр настроек и статистики")
    table.add_row("[0]", "🚪 Выход")
    
    console.print(Panel(table, title="[bold green]ГЛАВНОЕ МЕНЮ[/bold green]", border_style="cyan"))


def action_run_web():
    console.print("\n[bold cyan]--- 🌐 Запуск Веб-Панели управления ---[/bold cyan]")
    console.print(f"[green]Веб-интерфейс будет доступен по адресу:[/green] [bold yellow]http://{config.web.host}:{config.web.port}[/bold yellow]\n")
    from web_server import run_server
    run_server(open_browser=True)


async def action_parse_search():
    console.print("\n[bold cyan]--- 🔍 Парсинг поисковой выдачи Авито ---[/bold cyan]")
    url = Prompt.ask("Введите ссылку на поисковую выдачу Авито (со всеми фильтрами)")
    if not url.strip() or "avito.ru" not in url:
        console.print("[red]Некорректная ссылка на Авито![/red]")
        return

    pages = IntPrompt.ask("Сколько страниц выдачи собрать?", default=1)
    engine_choice = Prompt.ask("Выберите движок", choices=["browser", "http"], default="browser")

    console.print(f"\n[yellow]⏳ Запуск парсинга ({engine_choice})...[/yellow]")
    if engine_choice == "browser":
        result = await browser_engine.parse_search(url, max_pages=pages)
    else:
        result = await http_engine.parse_search(url, max_pages=pages)

    if not result.items:
        console.print(f"[red]Объявления не найдены или доступ ограничен.[/red]")
        if result.errors:
            for err in result.errors:
                console.print(f"[dim red]• {err}[/dim red]")
        return

    console.print(f"\n[bold green]✓ Успешно собрано {len(result.items)} объявлений за {result.elapsed_seconds} сек.![/bold green]")

    # Сохранение в БД
    save_to_db = Confirm.ask("Сохранить объявления в базу данных SQLite?", default=True)
    if save_to_db:
        save_stats = await db.save_items(result.items)
        console.print(f"[green]✓ Сохранено в БД: новых {save_stats['new_count']}, изменений цен: {save_stats['price_drop_count']}[/green]")

    # Экспорт
    export_choice = Prompt.ask("В какой формат экспортировать?", choices=["excel", "csv", "json", "all", "none"], default="excel")
    if export_choice == "excel":
        path = exporter.export_to_excel(result.items)
        console.print(f"[bold green]✓ Excel файл создан:[/bold green] [cyan]{path}[/cyan]")
    elif export_choice == "csv":
        path = exporter.export_to_csv(result.items)
        console.print(f"[bold green]✓ CSV файл создан:[/bold green] [cyan]{path}[/cyan]")
    elif export_choice == "json":
        path = exporter.export_to_json(result.items)
        console.print(f"[bold green]✓ JSON файл создан:[/bold green] [cyan]{path}[/cyan]")
    elif export_choice == "all":
        paths = exporter.export_all_formats(result.items)
        console.print(f"[bold green]✓ Файлы успешно созданы во всех форматах в папке exports![/bold green]")


async def action_parse_detail():
    console.print("\n[bold cyan]--- 📦 Детальный парсинг карточки объявления ---[/bold cyan]")
    url = Prompt.ask("Введите прямую ссылку на объявление Авито")
    if not url.strip() or "avito.ru" not in url:
        console.print("[red]Некорректная ссылка на Авито![/red]")
        return

    console.print("[yellow]⏳ Загрузка страницы и извлечение данных...[/yellow]")
    item = await browser_engine.parse_item_detail(url)
    if not item:
        console.print("[red]Не удалось получить данные объявления.[/red]")
        return

    # Вывод карточки в консоль
    console.print(Panel(
        f"[bold white]{item.title}[/bold white]\n"
        f"[bold green]Цена:[/bold green] {item.price_string or (str(item.price) + ' ₽')}\n"
        f"[bold yellow]Адрес:[/bold yellow] {item.address or 'Не указан'}\n"
        f"[bold cyan]Продавец:[/bold cyan] {item.seller.name if item.seller else 'Не указан'} (Рейтинг: {item.seller.rating if item.seller else '-'})\n"
        f"[bold magenta]Фотографий найдено:[/bold magenta] {len(item.images)}\n"
        f"[bold blue]Характеристики:[/bold blue] {len(item.params)} шт.\n\n"
        f"[dim]{item.description[:300]}...[/dim]" if item.description else "[dim]Описание отсутствует[/dim]",
        title=f"[bold]Карточка ID: {item.id}[/bold]",
        border_style="green"
    ))

    save = Confirm.ask("Сохранить в базу данных?", default=True)
    if save:
        await db.save_item(item)
        console.print("[green]✓ Сохранено в БД[/green]")


async def action_console_monitor():
    console.print("\n[bold cyan]--- 🔄 Консольный мониторинг в реальном времени ---[/bold cyan]")
    searches = await db.get_searches(enabled_only=True)
    if not searches:
        console.print("[yellow]В базе нет активных поисковых ссылок для мониторинга.[/yellow]")
        add_now = Confirm.ask("Хотите добавить поиск сейчас?", default=True)
        if add_now:
            name = Prompt.ask("Название поиска")
            url = Prompt.ask("Ссылка на Авито")
            await db.add_search(SearchQuery(name=name, url=url))
            searches = await db.get_searches(enabled_only=True)
        else:
            return

    from monitoring import monitor_service
    from outbox import outbox_worker
    console.print("[green]Общий мониторинг запущен. Ctrl+C — остановка.[/green]")
    delivery_task = asyncio.create_task(outbox_worker.run())
    await monitor_service.start()
    try:
        await asyncio.Event().wait()
    finally:
        delivery_task.cancel()
        try:
            await delivery_task
        except asyncio.CancelledError:
            pass
        await monitor_service.stop()


async def action_export_db():
    console.print("\n[bold cyan]--- 📊 Экспорт всей базы данных в Excel ---[/bold cyan]")
    items = await db.get_items(limit=10000)
    if not items:
        console.print("[yellow]База данных пуста.[/yellow]")
        return

    path = exporter.export_to_excel(items)
    console.print(f"[bold green]✓ Успешно экспортировано {len(items)} записей в файл:[/bold green]")
    console.print(f"[cyan]{path}[/cyan]")


async def action_proxy_manager():
    console.print("\n[bold cyan]--- 🌐 Управление прокси ---[/bold cyan]")
    console.print(f"Всего загружено прокси: [bold]{proxy_manager.total_count}[/bold]")
    console.print(f"Файл прокси: [cyan]{proxy_manager.proxies_file}[/cyan]")

    if proxy_manager.proxies:
        table = Table(title="Список прокси", border_style="dim")
        table.add_column("#", style="dim", width=4)
        table.add_column("Прокси URL", style="white")
        for idx, p in enumerate(proxy_manager.proxies[:10], start=1):
            table.add_row(str(idx), p)
        console.print(table)

    choice = Prompt.ask(
        "Действие",
        choices=["add", "check", "back"],
        default="back"
    )

    if choice == "add":
        new_proxy = Prompt.ask("Введите прокси (например http://user:pass@host:port или host:port)")
        if proxy_manager.add_proxy(new_proxy):
            console.print("[green]✓ Прокси успешно добавлен![/green]")
        else:
            console.print("[yellow]Прокси уже существует или имеет некорректный формат.[/yellow]")
    elif choice == "check":
        if not proxy_manager.proxies:
            console.print("[yellow]Список прокси пуст.[/yellow]")
            return
        console.print("[yellow]⏳ Проверка доступности первого прокси...[/yellow]")
        ok = await proxy_manager.check_proxy(proxy_manager.proxies[0])
        if ok:
            console.print("[bold green]✓ Прокси работает корректно![/bold green]")
        else:
            console.print("[bold red]✗ Ошибка соединения через прокси.[/bold red]")


async def action_settings_and_stats():
    stats = await db.get_stats()
    table = Table(title="Статистика базы данных и параметры", border_style="cyan")
    table.add_column("Параметр", style="bold white")
    table.add_column("Значение", style="bold yellow")

    table.add_row("Всего объявлений в БД", str(stats["total_items"]))
    table.add_row("Отслеживаемых поисков", f"{stats['active_searches']} из {stats['total_searches']}")
    table.add_row("Зафиксировано скидок/падений цен", str(stats["total_price_drops"]))
    table.add_row("Режим браузера Playwright", "Скрытый (Headless)" if config.scraper.headless else "Видимое окно")
    table.add_row("Таймаут страницы", f"{config.scraper.timeout_ms // 1000} сек.")
    table.add_row("Интервал мониторинга", f"{config.telegram.notification_interval_min} мин.")
    table.add_row("Telegram Бот", "Настроен" if config.telegram.bot_token else "[red]Не указан токен в .env[/red]")
    table.add_row("Прокси", f"Включено ({proxy_manager.total_count} шт.)" if config.proxy.enabled else "Отключено")

    console.print(table)


async def main():
    await db.init_db()
    print_banner()

    while True:
        show_menu()
        choice = Prompt.ask("Выберите действие", choices=["0", "1", "2", "3", "4", "5", "6", "7", "8"], default="1")

        if choice == "0":
            console.print("\n[cyan]До свидания![/cyan]")
            break
        elif choice == "1":
            action_run_web()
        elif choice == "2":
            await action_parse_search()
        elif choice == "3":
            await action_parse_detail()
        elif choice == "4":
            console.print("\n[bold cyan]Запуск Telegram бота...[/bold cyan]")
            from telegram_bot import run_bot
            await run_bot()
        elif choice == "5":
            await action_console_monitor()
        elif choice == "6":
            await action_export_db()
        elif choice == "7":
            await action_proxy_manager()
        elif choice == "8":
            await action_settings_and_stats()

        console.print("\n" + "─" * 60 + "\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Завершение работы.[/yellow]")
