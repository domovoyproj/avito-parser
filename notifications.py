"""One notification presentation shared by web monitoring and Telegram."""
import html
from urllib.parse import urlsplit
from database import db
from models import AvitoItem

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
    if item.price is not None and item.search_query_id:
        market_avg = await db.get_search_market_price(item.search_query_id)
        if market_avg and market_avg > item.price:
            savings_rub = market_avg - item.price
            pct_below = round((savings_rub / market_avg) * 100)
            if pct_below >= 10:
                market_info_line = f"\n📊 <b>Средняя цена:</b> {market_avg:,} ₽ <i>(выгода {savings_rub:,} ₽ / -{pct_below}%)</i>".replace(",", " ")

    price_str = f"<b>{item.price:,} ₽</b>".replace(",", " ") if item.price is not None else "Цена не указана"
    
    if notif_type == "price_drop" and item.old_price is not None and item.price is not None:
        old_str = f"{item.old_price:,} ₽".replace(",", " ")
        delta = item.old_price - item.price
        price_str += f" <i>(было {old_str}, скидка {delta:,} ₽)</i>".replace(",", " ")

    esc_title = html.escape((item.title or "Без названия")[:180])
    esc_search = html.escape((search_name or "Поиск")[:80])
    esc_address = html.escape((item.address or "Не указана")[:150])
    esc_seller = f"\n👤 <b>Продавец:</b> {html.escape(item.seller.name[:80])}" if item.seller and item.seller.name else ""
    delivery_badge = " | 🚚 Авито Доставка" if item.delivery_available else ""

    reasons_block = ""
    if item.deal_reasons:
        reasons_block = "\n" + "\n".join([f"  ✅ {html.escape(r[:120])}" for r in item.deal_reasons[:3]])

    flaws_block = ""
    if item.detected_flaws:
        flaws_block = "\n" + "\n".join([f"  ⚠️ <b>Внимание:</b> {html.escape(f[:120])}" for f in item.detected_flaws[:2]])

    ai_summary_block = ""
    if item.ai_summary:
        ai_summary_block = f"\n\n🤖 <b>AI-Вердикт:</b> <i>{html.escape(item.ai_summary[:500])}</i>"

    try:
        safe_url = item.url if urlsplit(item.url).scheme.lower() in ("http", "https") and len(item.url) <= 1000 else "https://www.avito.ru/"
    except ValueError:
        safe_url = "https://www.avito.ru/"
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
        f"🔗 <a href='{html.escape(safe_url, quote=True)}'>Открыть объявление на Авито</a>"
    )
    return card


