import asyncio
import html
import logging
import random
from datetime import datetime
from typing import Optional
from aiogram import Bot, Dispatcher, F, types
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import config
from database import db
from exporter import exporter
from models import AvitoItem, SearchQuery, TelegramChatSettings
from browser_engine import browser_engine
logger = logging.getLogger("AvitoTelegramBot")

dp = Dispatcher()


def get_main_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔍 Мои поиски", callback_data="list_searches"),
        InlineKeyboardButton(text="➕ Добавить поиск", callback_data="add_search_help")
    )
    builder.row(
        InlineKeyboardButton(text="📊 Статистика", callback_data="bot_stats"),
        InlineKeyboardButton(text="📥 Выгрузить в Excel", callback_data="export_excel")
    )
    builder.row(
        InlineKeyboardButton(text="⚙️ Настройки уведомлений", callback_data="show_settings"),
        InlineKeyboardButton(text="⚡ Проверить сейчас", callback_data="check_all_now")
    )
    return builder.as_markup()


def get_settings_keyboard(s: TelegramChatSettings) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    
    btn_new = f"{'✅' if s.notify_new else '❌'} Новые объявления"
    btn_drops = f"{'✅' if s.notify_drops else '❌'} Снижения цен"
    btn_market = f"{'✅' if s.only_below_market else '❌'} 🔥 Только дешевле рынка"
    btn_market_pct = f"🏷 Скидка от рынка: -{s.below_market_pct}%"
    btn_score = f"🎯 Мин. AI Score: {s.min_deal_score if s.min_deal_score > 0 else 'Выкл'}"
    btn_photos = f"{'✅' if s.send_photos else '❌'} Прикреплять фото"
    btn_deliv = f"{'✅' if s.delivery_only else '❌'} Только с доставкой"
    btn_thresh = f"📉 Мин. скидка цены: {s.min_discount_pct if s.min_discount_pct > 0 else 'Любая'}"

    builder.row(InlineKeyboardButton(text=btn_new, callback_data="toggle_notif_new"))
    builder.row(InlineKeyboardButton(text=btn_drops, callback_data="toggle_notif_drops"))
    builder.row(InlineKeyboardButton(text=btn_score, callback_data="cycle_min_deal_score"))
    builder.row(InlineKeyboardButton(text=btn_market, callback_data="toggle_below_market"))
    if s.only_below_market:
        builder.row(InlineKeyboardButton(text=btn_market_pct, callback_data="cycle_below_market_pct"))
    builder.row(InlineKeyboardButton(text=btn_photos, callback_data="toggle_notif_photos"))
    builder.row(InlineKeyboardButton(text=btn_deliv, callback_data="toggle_notif_delivery"))
    builder.row(InlineKeyboardButton(text=btn_thresh, callback_data="cycle_discount_threshold"))
    builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="main_menu"))
    
    return builder.as_markup()

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    """Приветственное сообщение"""
    text = (
        "👋 <b>Добро пожаловать в бота мониторинга Авито!</b>\n\n"
        "Бот позволяет отслеживать новые объявления и снижение цен в реальном времени.\n\n"
        "<b>Команды управления:</b>\n"
        "• <code>/settings</code> — ⚙️ Настройка типов уведомлений и фильтров\n"
        "• <code>/add [название] [ссылка]</code> — Добавить поиск в мониторинг\n"
        "• <code>/list</code> — Список ваших отслеживаемых поисков\n"
        "• <code>/del [id]</code> — Удалить задачу\n"
        "• <code>/check</code> — Запустить немедленную проверку выдачи\n"
        "• <code>/export</code> — Выгрузить базу в Excel\n"
        "• <code>/status</code> — Статистика и состояние парсера\n"
    )
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=get_main_keyboard())


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await cmd_start(message)


@dp.message(Command("add"))
async def cmd_add(message: types.Message):
    """Добавление поисковой ссылки: /add iPhone 15 https://www.avito.ru/..."""
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer(
            "⚠️ <b>Формат команды:</b>\n"
            "<code>/add [Название поиска] [Ссылка на Авито]</code>\n\n"
            "<b>Пример:</b>\n"
            "<code>/add iPhone 15 Pro https://www.avito.ru/moskva/telefony/iphone_15_pro-ASgBAgICAURMyg3smjg</code>",
            parse_mode=ParseMode.HTML
        )
        return

    name = args[1]
    url = args[2]

    if "avito.ru" not in url:
        await message.answer("❌ Ссылка должна быть с домена avito.ru!")
        return

    search_id = await db.add_search(SearchQuery(
        name=name,
        url=url,
        check_interval_min=config.telegram.notification_interval_min
    ))

    await message.answer(
        f"✅ <b>Поиск успешно добавлен!</b>\n\n"
        f"<b>ID:</b> <code>{search_id}</code>\n"
        f"<b>Название:</b> {name}\n"
        f"<b>Интервал проверки:</b> каждые {config.telegram.notification_interval_min} мин.\n\n"
        f"Бот уведомит вас при появлении новых объявлений или падении цен.",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard()
    )


@dp.message(Command("list"))
@dp.callback_query(F.data == "list_searches")
async def cmd_list(event: types.Message | types.CallbackQuery):
    """Отображение списка отслеживаемых ссылок"""
    searches = await db.get_searches()
    
    if not searches:
        text = "📭 У вас пока нет добавленных поисков для мониторинга.\nДобавьте командой: <code>/add Название Ссылка</code>"
        if isinstance(event, types.CallbackQuery):
            await event.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=get_main_keyboard())
            await event.answer()
        else:
            await event.answer(text, parse_mode=ParseMode.HTML, reply_markup=get_main_keyboard())
        return

    text = f"📋 <b>Ваши отслеживаемые поиски ({len(searches)}):</b>\n\n"
    builder = InlineKeyboardBuilder()

    for s in searches:
        status_icon = "🟢" if s.enabled else "🔴"
        last_chk = s.last_checked_at.strftime("%d.%m %H:%M") if s.last_checked_at else "еще не проверялся"
        text += (
            f"{status_icon} <b>[ID: {s.id}] {s.name}</b>\n"
            f"   • Последняя проверка: {last_chk}\n"
            f"   • <a href='{s.url}'>Открыть ссылку на Авито</a>\n\n"
        )
        builder.button(text=f"🗑 Удалить #{s.id}", callback_data=f"del_search_{s.id}")

    builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="main_menu"))
    builder.adjust(2)

    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=builder.as_markup(), disable_web_page_preview=True)
        await event.answer()
    else:
        await event.answer(text, parse_mode=ParseMode.HTML, reply_markup=builder.as_markup(), disable_web_page_preview=True)


@dp.callback_query(F.data.startswith("del_search_"))
async def cb_delete_search(call: types.CallbackQuery):
    search_id = int(call.data.split("_")[2])
    success = await db.delete_search(search_id)
    if success:
        await call.answer(f"Поиск #{search_id} удален")
        await cmd_list(call)
    else:
        await call.answer("Не удалось найти или удалить поиск", show_alert=True)


@dp.message(Command("blacklist"))
async def cmd_blacklist(message: types.Message):
    """Управление черным списком продавцов: /blacklist [имя_продавца]"""
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        sellers = await db.get_blacklisted_sellers()
        if not sellers:
            await message.answer("ℹ️ Черный список продавцов пуст.\nДобавить: <code>/blacklist ИмяПродавца</code>", parse_mode=ParseMode.HTML)
        else:
            text = "🚫 <b>Черный список продавцов:</b>\n" + "\n".join([f"• <code>{s}</code>" for s in sellers])
            await message.answer(text, parse_mode=ParseMode.HTML)
        return

    seller_name = args[1].strip()
    await db.add_seller_to_blacklist(seller_name, reason="Добавлен через Telegram")
    await message.answer(f"🚫 Продавец <b>{seller_name}</b> добавлен в черный список. Уведомления от него отключены.", parse_mode=ParseMode.HTML)

@dp.message(Command("del"))
async def cmd_del(message: types.Message):
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.answer("⚠️ Укажите ID поиска: <code>/del 1</code>", parse_mode=ParseMode.HTML)
        return

    search_id = int(args[1])
    success = await db.delete_search(search_id)
    if success:
        await message.answer(f"✅ Поиск ID <b>{search_id}</b> удален из мониторинга.", parse_mode=ParseMode.HTML)
    else:
        await message.answer(f"❌ Поиск с ID <b>{search_id}</b> не найден.", parse_mode=ParseMode.HTML)


@dp.message(Command("export"))
@dp.callback_query(F.data == "export_excel")
async def cmd_export(event: types.Message | types.CallbackQuery):
    """Выгрузка всех собранных объявлений в Excel документ"""
    msg = event.message if isinstance(event, types.CallbackQuery) else event
    status_msg = await msg.answer("⏳ Формирую отчет в Excel...")

    items = await db.get_items(limit=1000)
    if not items:
        await status_msg.edit_text("📭 В базе данных пока нет объявлений для экспорта.")
        if isinstance(event, types.CallbackQuery):
            await event.answer()
        return

    file_path = exporter.export_to_excel(items)
    doc = FSInputFile(file_path, filename=file_path.name)
    await msg.answer_document(
        document=doc,
        caption=f"📊 <b>Выгрузка Авито</b>\nВсего записей в отчете: <b>{len(items)}</b>",
        parse_mode=ParseMode.HTML
    )
    await status_msg.delete()
    if isinstance(event, types.CallbackQuery):
        await event.answer()


@dp.message(Command("status"))
@dp.callback_query(F.data == "bot_stats")
async def cmd_status(event: types.Message | types.CallbackQuery):
    stats = await db.get_stats()
    text = (
        "📊 <b>Статистика системы мониторинга:</b>\n\n"
        f"• Всего объявлений в базе: <b>{stats['total_items']}</b>\n"
        f"• Активных поисков на контроле: <b>{stats['active_searches']}</b> / {stats['total_searches']}\n"
        f"• Зафиксировано снижений цен: <b>{stats['total_price_drops']}</b>\n"
        f"• Интервал сканирования: <b>{config.telegram.notification_interval_min} мин.</b>\n"
        f"• Режим браузера: <b>{'Headless' if config.scraper.headless else 'Видимый (окно)'}</b>\n"
    )
    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=get_main_keyboard())
        await event.answer()
    else:
        await event.answer(text, parse_mode=ParseMode.HTML, reply_markup=get_main_keyboard())



# ==============================================================================
# НАСТРОЙКИ УВЕДОМЛЕНИЙ В TELEGRAM
# ==============================================================================

@dp.message(Command("settings"))
@dp.callback_query(F.data == "show_settings")
async def cmd_settings(event: types.Message | types.CallbackQuery):
    """Меню настройки типов уведомлений"""
    chat_id = event.chat.id if isinstance(event, types.Message) else event.message.chat.id
    s = await db.get_chat_settings(chat_id)
    
    text = (
        "⚙️ <b>Настройки уведомлений для этого чата:</b>\n\n"
        f"• 🆕 Новые объявления: <b>{'Включены' if s.notify_new else 'Выключены'}</b>\n"
        f"• 📉 Падения цен: <b>{'Включены' if s.notify_drops else 'Выключены'}</b>\n"
        f"• 🎯 <b>Мин. AI Score:</b> <b>{f'от {s.min_deal_score}+ (Фильтр активен)' if s.min_deal_score > 0 else 'Выключен (все лоты)'}</b>\n"
        f"• 🔥 <b>Только дешевле рынка:</b> <b>{'ВКЛЮЧЕНО (от -' + str(s.below_market_pct) + '%)' if s.only_below_market else 'Выключено (все цены)'}</b>\n"
        f"• 📸 Фотографии к товарам: <b>{'Да' if s.send_photos else 'Нет'}</b>\n"
        f"• 🚚 Только с Авито Доставкой: <b>{'Да' if s.delivery_only else 'Все объявления'}</b>\n"
        f"• 📉 Мин. падение цены: <b>{f'от {s.min_discount_pct}%' if s.min_discount_pct > 0 else 'Любое снижение'}</b>\n\n"
        "<i>Нажмите на кнопки ниже, чтобы изменить параметры:</i>"
    )

    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=get_settings_keyboard(s))
        await event.answer()
    else:
        await event.answer(text, parse_mode=ParseMode.HTML, reply_markup=get_settings_keyboard(s))

@dp.callback_query(F.data == "toggle_notif_new")
async def cb_toggle_new(call: types.CallbackQuery):
    s = await db.get_chat_settings(call.message.chat.id)
    new_s = await db.update_chat_settings(call.message.chat.id, notify_new=not s.notify_new)
    await call.answer(f"Новые объявления: {'ВКЛ' if new_s.notify_new else 'ВЫКЛ'}")
    await cmd_settings(call)


@dp.callback_query(F.data == "toggle_notif_drops")
async def cb_toggle_drops(call: types.CallbackQuery):
    s = await db.get_chat_settings(call.message.chat.id)
    new_s = await db.update_chat_settings(call.message.chat.id, notify_drops=not s.notify_drops)
    await call.answer(f"Снижение цен: {'ВКЛ' if new_s.notify_drops else 'ВЫКЛ'}")
    await cmd_settings(call)


@dp.callback_query(F.data == "toggle_notif_photos")
async def cb_toggle_photos(call: types.CallbackQuery):
    s = await db.get_chat_settings(call.message.chat.id)
    new_s = await db.update_chat_settings(call.message.chat.id, send_photos=not s.send_photos)
    await call.answer(f"Прикрепление фото: {'ВКЛ' if new_s.send_photos else 'ВЫКЛ'}")
    await cmd_settings(call)


@dp.callback_query(F.data == "toggle_notif_delivery")
async def cb_toggle_delivery(call: types.CallbackQuery):
    s = await db.get_chat_settings(call.message.chat.id)
    new_s = await db.update_chat_settings(call.message.chat.id, delivery_only=not s.delivery_only)
    await call.answer(f"Только с доставкой: {'ВКЛ' if new_s.delivery_only else 'ВЫКЛ'}")
    await cmd_settings(call)


@dp.callback_query(F.data == "cycle_discount_threshold")
async def cb_cycle_discount(call: types.CallbackQuery):
    s = await db.get_chat_settings(call.message.chat.id)
    thresholds = [0, 5, 10, 15, 20, 30]
    curr_idx = thresholds.index(s.min_discount_pct) if s.min_discount_pct in thresholds else 0
    next_thresh = thresholds[(curr_idx + 1) % len(thresholds)]
    
    new_s = await db.update_chat_settings(call.message.chat.id, min_discount_pct=next_thresh)
    await call.answer(f"Порог скидки: {next_thresh}%" if next_thresh > 0 else "Любая скидка")
    await cmd_settings(call)


@dp.callback_query(F.data == "toggle_below_market")
async def cb_toggle_below_market(call: types.CallbackQuery):
    s = await db.get_chat_settings(call.message.chat.id)
    new_s = await db.update_chat_settings(call.message.chat.id, only_below_market=not s.only_below_market)
    status_txt = f"Фильтр 'Только дешевле рынка': {'ВКЛ (-' + str(new_s.below_market_pct) + '%)' if new_s.only_below_market else 'ВЫКЛ'}"
    await call.answer(status_txt)
    await cmd_settings(call)


@dp.callback_query(F.data == "cycle_below_market_pct")
async def cb_cycle_below_market_pct(call: types.CallbackQuery):
    s = await db.get_chat_settings(call.message.chat.id)
    pcts = [10, 15, 20, 25, 30, 40]
    curr_idx = pcts.index(s.below_market_pct) if s.below_market_pct in pcts else 1
    next_pct = pcts[(curr_idx + 1) % len(pcts)]
    
    new_s = await db.update_chat_settings(call.message.chat.id, below_market_pct=next_pct)
    await call.answer(f"Порог скидки от рынка: -{next_pct}%")
    await cmd_settings(call)

@dp.callback_query(F.data == "cycle_min_deal_score")
async def cb_cycle_min_deal_score(call: types.CallbackQuery):
    s = await db.get_chat_settings(call.message.chat.id)
    scores = [0, 60, 70, 80, 85]
    curr_idx = scores.index(s.min_deal_score) if s.min_deal_score in scores else 0
    next_score = scores[(curr_idx + 1) % len(scores)]
    
    new_s = await db.update_chat_settings(call.message.chat.id, min_deal_score=next_score)
    await call.answer(f"Порог AI Score: {next_score}+" if next_score > 0 else "Фильтр AI Score выключен")
    await cmd_settings(call)


@dp.message(Command("check"))
@dp.callback_query(F.data == "check_all_now")
async def cmd_check_all(event: types.Message | types.CallbackQuery):
    msg = event.message if isinstance(event, types.CallbackQuery) else event
    status = await msg.answer("🔎 Запускаю внеочередную проверку всех активных поисков...")
    
    searches = await db.get_searches(enabled_only=True)
    if not searches:
        await status.edit_text("📭 Нет активных поисков для проверки. Добавьте поиск: <code>/add Название Ссылка</code>", parse_mode=ParseMode.HTML)
        if isinstance(event, types.CallbackQuery):
            await event.answer()
        return

    total_found = 0
    total_new = 0
    total_drops = 0
    chat_id = msg.chat.id

    for s in searches:
        res = await browser_engine.parse_search(s.url, max_pages=1)
        if res.items:
            for it in res.items:
                it.search_query_id = s.id
            save_res = await db.save_items(res.items)
            total_found += len(res.items)
            total_new += save_res["new_count"]
            total_drops += save_res["price_drop_count"]

            for new_it in save_res["new_items"]:
                if not await db.is_notification_sent(new_it.id, s.id, "new"):
                    await send_item_card(msg.bot, chat_id, new_it, s.name, "new")
                    await db.mark_notification_sent(new_it.id, s.id, "new")
                    await asyncio.sleep(0.3)

            for ch in save_res["price_changes"]:
                if not await db.is_notification_sent(ch.item_id, s.id, "price_drop"):
                    item_obj = await db.get_item_by_id(ch.item_id)
                    if item_obj:
                        await send_item_card(msg.bot, chat_id, item_obj, s.name, "price_drop")
                        await db.mark_notification_sent(ch.item_id, s.id, "price_drop")
                        await asyncio.sleep(0.3)

        await db.update_search_last_checked(s.id)

    await status.edit_text(
        f"✅ <b>Проверка завершена!</b>\n\n"
        f"• Опрошено задач: <b>{len(searches)}</b>\n"
        f"• Всего объявлений: <b>{total_found}</b>\n"
        f"• Новых товаров: <b>{total_new}</b>\n"
        f"• Зафиксировано скидок: <b>{total_drops}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard()
    )
    if isinstance(event, types.CallbackQuery):
        await event.answer()

@dp.callback_query(F.data == "main_menu")
async def cb_main_menu(call: types.CallbackQuery):
    await call.message.edit_text("Главное меню:", reply_markup=get_main_keyboard())
    await call.answer()


@dp.callback_query(F.data == "add_search_help")
async def cb_add_help(call: types.CallbackQuery):
    text = (
        "💡 <b>Как добавить поиск:</b>\n\n"
        "Отправьте команду в чат в формате:\n"
        "<code>/add [Название] [Ссылка на Авито]</code>\n\n"
        "<b>Пример:</b>\n"
        "<code>/add PS5 https://www.avito.ru/moskva/pristavki_i_programmy/igrovye_pristavki/playstation_5-ASgBAgICAURSpgoU</code>"
    )
    await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=get_main_keyboard())
    await call.answer()



def build_item_keyboard(item_id: str, item_url: str) -> InlineKeyboardMarkup:
    """Построение inline-клавиатуры с быстрыми действиями для карточки товара"""
    builder = InlineKeyboardBuilder()
    builder.button(text='↗️ Открыть на Авито', url=item_url)
    builder.button(text='⭐ Избранное', callback_data=f'tg_fav_{item_id}')
    builder.button(text='🙈 Скрыть', callback_data=f'tg_hide_{item_id}')
    builder.button(text='🤖 AI-анализ', callback_data=f'tg_ai_{item_id}')
    builder.adjust(1, 3)  # первая строка: ссылка на Авито, вторая: 3 кнопки действий
    return builder.as_markup()


@dp.callback_query(F.data.startswith('tg_fav_'))
async def cb_tg_favorite(call: types.CallbackQuery):
    """Добавление/удаление товара из избранного по кнопке в уведомлении"""
    item_id = call.data.replace('tg_fav_', '')
    is_fav = await db.toggle_item_favorite(item_id)
    emoji = '⭐' if is_fav else '☆'
    await call.answer(f'{emoji} {"Добавлено в избранное" if is_fav else "Убрано из избранного"}', show_alert=False)


@dp.callback_query(F.data.startswith('tg_hide_'))
async def cb_tg_hide(call: types.CallbackQuery):
    """Скрытие объявления из каталога по кнопке в уведомлении"""
    item_id = call.data.replace('tg_hide_', '')
    await db.toggle_item_hidden(item_id, is_hidden=True)
    await call.answer('🙈 Объявление скрыто из каталога', show_alert=False)
    try:
        await call.message.delete()
    except Exception:
        pass


@dp.callback_query(F.data.startswith('tg_ai_'))
async def cb_tg_ai_verdict(call: types.CallbackQuery):
    """Запрос AI-вердикта для товара по кнопке в уведомлении"""
    item_id = call.data.replace('tg_ai_', '')
    await call.answer('🤖 Генерирую AI-анализ...', show_alert=False)

    item = await db.get_item_by_id(item_id)
    if not item:
        await call.message.reply('❌ Объявление не найдено в базе')
        return

    ai_settings = await db.get_ai_settings()
    if not ai_settings.enabled or (ai_settings.provider.lower() != "ollama" and not ai_settings.api_key):
        await call.message.reply('⚠️ AI-анализ не настроен. Включите его в настройках веб-панели (Настройки → AI Провайдер).')
        return

    from ai_scoring import deal_scoring_engine
    market_med, market_avg = await db.get_search_market_stats(item.search_query_id)
    verdict = await deal_scoring_engine.generate_ai_verdict(
        item, market_median=market_med, market_avg=market_avg, ai_settings=ai_settings
    )

    if verdict:
        await db.update_item_ai_summary(item.id, verdict)
        text = f'🤖 <b>AI-Вердикт для:</b> {item.title}\n\n<i>{verdict}</i>'
        await call.message.reply(text, parse_mode=ParseMode.HTML)
    else:
        await call.message.reply('❌ Не удалось получить вердикт от AI. Проверьте настройки и API ключ.')

async def format_item_notification(item: AvitoItem, search_name: str, notif_type: str = "new") -> str:
    """Форматирование карточки уведомления для Telegram с бейджами AI Score"""
    header = "🆕 <b>НОВОЕ ОБЪЯВЛЕНИЕ</b>" if notif_type == "new" else "📉 <b>ЦЕНА СНИЗИЛАСЬ!</b>"
    
    # Бейдж градации сделки
    score_val = item.deal_score if item.deal_score is not None else 50
    if score_val >= 85:
        grade_badge = f"💎 <b>GEM-ЛОТ (Score: {score_val}/100)</b>"
    elif score_val >= 70:
        grade_badge = f"🔥 <b>ВЫГОДНАЯ СДЕЛКА (Score: {score_val}/100)</b>"
    elif score_val >= 50:
        grade_badge = f"⚖️ <b>FAIR (Score: {score_val}/100)</b>"
    else:
        grade_badge = f"⚠️ <b>Внимание/Риск (Score: {score_val}/100)</b>"
    
    market_info_line = ""
    if item.price and item.search_query_id:
        market_avg = await db.get_search_market_price(item.search_query_id)
        if market_avg and market_avg > item.price:
            savings_rub = market_avg - item.price
            pct_below = round((savings_rub / market_avg) * 100)
            if pct_below >= 10:
                market_info_line = f"\n📊 <b>Средняя цена:</b> {market_avg:,} ₽ <i>(выгода {savings_rub:,} ₽ / -{pct_below}%)</i>".replace(",", " ")

    price_str = f"<b>{item.price:,} ₽</b>".replace(",", " ") if item.price else "Цена не указана"
    
    if notif_type == "price_drop" and item.old_price and item.price:
        old_str = f"{item.old_price:,} ₽".replace(",", " ")
        delta = item.old_price - item.price
        price_str += f" <i>(было {old_str}, скидка {delta:,} ₽)</i>".replace(",", " ")

    esc_title = html.escape(item.title or "Без названия")
    esc_search = html.escape(search_name or "Поиск")
    esc_address = html.escape(item.address or "Не указана")
    esc_seller = f"\n👤 <b>Продавец:</b> {html.escape(item.seller.name)}" if item.seller and item.seller.name else ""
    delivery_badge = " | 🚚 Авито Доставка" if item.delivery_available else ""

    reasons_block = ""
    if item.deal_reasons:
        reasons_block = "\n" + "\n".join([f"  ✅ {html.escape(r)}" for r in item.deal_reasons[:3]])

    flaws_block = ""
    if item.detected_flaws:
        flaws_block = "\n" + "\n".join([f"  ⚠️ <b>Внимание:</b> {html.escape(f)}" for f in item.detected_flaws[:2]])

    ai_summary_block = ""
    if item.ai_summary:
        ai_summary_block = f"\n\n🤖 <b>AI-Вердикт:</b> <i>{html.escape(item.ai_summary)}</i>"

    card = (
        f"{header}\n"
        f"{grade_badge}\n"
        f"🎯 <b>Поиск:</b> {esc_search}\n\n"
        f"📦 <b>{esc_title}</b>\n"
        f"💰 <b>Цена:</b> {price_str}{market_info_line}\n"
        f"📍 <b>Локация:</b> {esc_address}{delivery_badge}{esc_seller}"
        f"{reasons_block}"
        f"{flaws_block}"
        f"{ai_summary_block}\n\n"
        f"🔗 <a href='{item.url}'>Открыть объявление на Авито</a>"
    )
    return card


async def send_item_card(bot: Bot, chat_id: int, item: AvitoItem, search_name: str, notif_type: str):
    """Отправка карточки товара с учетом персональных настроек чата"""
    settings = await db.get_chat_settings(chat_id)

    # Проверка черного списка продавцов
    blacklisted = await db.get_blacklisted_sellers()
    if item.seller and item.seller.name and item.seller.name in blacklisted:
        return

    # Проверка фильтров уведомлений
    if notif_type == "new" and not settings.notify_new:
        return
    if notif_type == "price_drop" and not settings.notify_drops:
        return
    if settings.delivery_only and not item.delivery_available:
        return

    # Проверка фильтра по минимальному AI Score
    if settings.min_deal_score > 0 and (item.deal_score or 0) < settings.min_deal_score:
        return

    # Проверка фильтра "Только дешевле рынка"
    if settings.only_below_market and item.price and item.search_query_id:
        market_avg = await db.get_search_market_price(item.search_query_id)
        if market_avg and market_avg > 0:
            threshold = settings.below_market_pct / 100.0
            max_allowed = int(market_avg * (1.0 - threshold))
            if item.price > max_allowed:
                return  # Пропускаем, так как цена не ниже рынка на заданный процент

    # Проверка порога скидки
    if notif_type == "price_drop" and settings.min_discount_pct > 0 and item.old_price and item.price:
        discount_pct = round(((item.old_price - item.price) / item.old_price) * 100)
        if discount_pct < settings.min_discount_pct:
            return

    # Проверка режима тишины (Quiet Hours)
    if settings.quiet_hours_enabled:
        cur_hour = datetime.now().hour
        if settings.quiet_hours_start <= cur_hour or cur_hour < settings.quiet_hours_end:
            return

    text = await format_item_notification(item, search_name, notif_type)
    keyboard = build_item_keyboard(item.id, item.url)

    try:
        if settings.send_photos and item.main_image:
            await bot.send_photo(
                chat_id=chat_id,
                photo=item.main_image,
                caption=text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard
            )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
                disable_web_page_preview=False
            )
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления в Telegram: {e}")
async def run_bot():
    """Запуск Telegram бота для обработки команд и меню настроек"""
    if not config.telegram.bot_token:
        logger.error("Токен TELEGRAM_BOT_TOKEN не задан в .env файле!")
        return

    await db.init_db()
    bot = Bot(token=config.telegram.bot_token)
    
    logger.info("🟢 Telegram бот успешно подключен и слушает команды...")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(run_bot())
