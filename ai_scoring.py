"""
Модуль интеллектуальной оценки выгоды и надежности объявлений (AI Deal Scoring & Evaluation Engine).
Включает:
1. Детерминированный многофакторный математический скоринг (0-100 баллов).
2. Выявление дефектов, рисков, подделок и стоп-слов в заголовках и описании.
3. Опциональный нейросетевой анализ (LLM) через DeepSeek / OpenAI / OpenRouter / Ollama.
"""

import json
import logging
import re
import hashlib
import time
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple
import httpx

from models import AISettings, AvitoItem

logger = logging.getLogger("AvitoScoring")

# Словарь стоп-слов и паттернов риска для детекции дефектов/подделок
DEFECT_PATTERNS = [
    (r"\bна\s+запчасти\b", "Товар продается на запчасти"),
    (r"\bна\s+разбор\b|\bна\s+детали\b", "Товар предназначен на разборку/детали"),
    (r"\bпод\s+восстановление\b", "Требуется ремонт/восстановление"),
    (r"\bне\s+включается\b|\bне\s+реагирует\b|\bкирпич\b", "Устройство не включается или окирпичено"),
    (r"\bне\s+работает\b|\bне\s+пашет\b|\bне\s+пашет\b", "Заявлена неработоспособность части функций"),
    (r"\bразбит\w*\b|\bбитый\b|\bтрещин\w*\b|\bтреснут\w*\b", "Механические повреждения / трещины экрана или корпуса"),
    (r"\bскол\w*\b|\bвмятин\w*\b|\bполос\w*\s+на\s+экран\w*\b", "Дефекты экрана или повреждения корпуса"),
    (r"\bface\s*id\s+не\s+работает\b|\bфейс\s*айди\s+не\s+работает\b|\bбез\s+face\s*id\b", "Неисправен модуль Face ID"),
    (r"\btouch\s*id\s+не\s+работает\b|\bтач\s*айди\s+не\s+работает\b|\bбез\s+touch\s*id\b", "Неисправен сканер отпечатков Touch ID"),
    (r"\btrue\s*tone\s+нет\b|\bбез\s+true\s*tone\b|\bтрутон\s+нет\b", "Отсутствует True Tone (вероятно менялся экран)"),
    (r"\b(icloud|айклауд)\b.*\b(заблокирован|пароль|lock|залочен)\b", "Блокировка по iCloud / Apple ID"),
    (r"\b(mdm|байпас|bypass)\b", "Корпоративный профиль MDM или обход блокировки"),
    (r"\bкопия\b|\bреплика\b|\bне\s+оригинал\b|\bлюкс\s+копия\b|\b1\s*:\s*1\b|\bnon[\s\-_]?original\b|\breplika\b|\baaa\+\b", "Указана копия/реплика (не оригинальный товар)"),
    (r"\b(утопленник|залит\w*|после\s+воды)\b", "Попадание влаги / следы залития жидкостью"),
    (r"\b(паянный|после\s+ремонта\s+платы|грели\s+чип)\b", "Сложный компонентный ремонт платы"),
    (r"\bбез\s+проверок\b|\bбез\s+претензий\b", "Отказ продавца от проверки при передаче"),
    (r"\bзаблокирован\w*\s+аккаунт\b", "Проблема с аккаунтом или учетной записью"),
]


class DealScoringEngine:
    """Двухуровневый движок оценки выгодности и надежности сделки"""

    # Пользовательские правила скоринга (загружаются из БД)
    _custom_penalties: List[Tuple[str, str]] = []
    _custom_boosters: List[Tuple[str, str, int]] = []
    _verdict_cache = OrderedDict()
    _budget_day = ''
    _budget_calls = 0

    @classmethod
    def detect_flaws(cls, text: str) -> List[str]:
        """Поиск маркеров дефектов и рисков в тексте"""
        if not text:
            return []
        
        lower_text = text.lower()
        flaws: List[str] = []
        
        for pattern, label in DEFECT_PATTERNS:
            if re.search(pattern, lower_text, re.IGNORECASE):
                if label not in flaws:
                    flaws.append(label)
        
        return flaws

    @classmethod
    def evaluate_item(
        cls,
        item: AvitoItem,
        market_median: Optional[int] = None,
        market_avg: Optional[int] = None
    ) -> Tuple[int, str, List[str], List[str]]:
        """
        Многофакторный математический расчет скоринга лота (0-100 баллов).
        Возвращает кортеж: (deal_score, deal_grade, deal_reasons, detected_flaws).
        """
        score = 0
        reasons: List[str] = []
        flaws: List[str] = []

        # 1. Анализ текста на стоп-слова и риски
        combined_text = f"{item.title} {item.description or ''}"
        flaws = cls.detect_flaws(combined_text)

        if getattr(item, "is_reserved", False):
            flaws.append("Товар зарезервирован другим покупателем (в доставке)")
        if getattr(item, "is_closed", False):
            flaws.append("Товар продан или объявление снято с публикации")
        # 2. Ценовой фактор (до 45 баллов)
        benchmark_price = market_median if market_median and market_median > 0 else market_avg
        if benchmark_price and item.price and item.price > 0:
            diff_pct = ((benchmark_price - item.price) / benchmark_price) * 100
            
            if diff_pct >= 30:
                score += 45
                reasons.append(f"Цена значительно ниже рынка (-{diff_pct:.0f}%)")
            elif diff_pct >= 20:
                score += 35
                reasons.append(f"Цена существенно выгоднее рынка (-{diff_pct:.0f}%)")
            elif diff_pct >= 10:
                score += 25
                reasons.append(f"Цена ниже средней на {diff_pct:.0f}%")
            elif diff_pct >= -10:
                score += 15
                reasons.append("Цена соответствует среднерыночной")
            else:
                score += 0
                # Цена выше рынка - 0 баллов ценового фактора
        elif item.price and item.price > 0:
            # Нет рыночного бенчмарка для этого поиска
            score += 20
            reasons.append("Базовая рыночная оценка")
        else:
            score += 5
        # 3. Репутация и надежность продавца (до 20 баллов)
        if item.seller:
            seller = item.seller
            if seller.is_verified:
                score += 7
                reasons.append("Проверенный профиль (Паспорт / Госуслуги)")
            
            if seller.rating is not None and seller.rating > 0:
                if seller.rating >= 4.8 and (seller.reviews_count or 0) > 0:
                    score += 8
                    reasons.append(f"Высокий рейтинг продавца ({seller.rating}★)")
                elif seller.rating >= 4.5:
                    score += 4
                    reasons.append(f"Хороший рейтинг продавца ({seller.rating}★)")
                elif seller.rating < 4.0:
                    flaws.append(f"Низкий рейтинг продавца ({seller.rating}★)")
            else:
                score += 2  # Нейтральный продавец без рейтинга
            
            if seller.reviews_count:
                if seller.reviews_count >= 10:
                    score += 5
                    reasons.append(f"Большое число отзывов ({seller.reviews_count})")
                elif seller.reviews_count >= 3:
                    score += 2
                    reasons.append(f"Есть подтвержденные отзывы ({seller.reviews_count})")
        else:
            score += 5  # Базовый балл частного лица

        # 4. Качество и прозрачность объявления (до 15 баллов)
        img_count = len(item.images) if item.images else (1 if item.main_image else 0)
        if img_count >= 4:
            score += 6
            reasons.append(f"Подробная фотогалерея ({img_count} фото)")
        elif img_count >= 1:
            score += 2

        if item.params and len(item.params) >= 3:
            score += 5
            reasons.append("Заполнены детальные характеристики")
        elif item.params and len(item.params) > 0:
            score += 2

        desc_len = len(item.description.strip()) if item.description else 0
        if desc_len >= 120:
            score += 4
            reasons.append("Подробное информативное описание")
        elif desc_len >= 30:
            score += 2

        # 5. Безопасность сделки и локация (до 10 баллов)
        if item.delivery_available:
            score += 7
            reasons.append("Доступна безопасная Авито Доставка")
        
        if item.metro or (item.address and len(item.address) > 5):
            score += 3
            reasons.append("Указана точная локация/метро")

        # 6. Динамика цены (до 10 баллов)
        if item.old_price and item.price and item.old_price > item.price:
            drop_pct = round(((item.old_price - item.price) / item.old_price) * 100)
            score += 10
            reasons.append(f"Продавец снизил цену на {drop_pct}%")

        # 6.5. Фактор свежести объявления (до +5 / -5 баллов)
        if item.published_at:
            age_hours = cls._parse_listing_age_hours(item.published_at)
            if age_hours is not None:
                if age_hours < 3 and score > 50:
                    score += 5
                    reasons.append("⚡ Свежее объявление (< 3 часов)")
                elif age_hours > 7 * 24:
                    # Лот висит больше недели при цене ниже рынка — подозрительно
                    if benchmark_price and item.price and item.price < benchmark_price:
                        flaws.append("Подозрительно долго висит при низкой цене")
                if age_hours > 14 * 24:
                    score -= 3

        # 6.6. Детекция ценовых аномалий
        anomaly = cls.detect_price_anomaly(item.price, market_median, market_avg)
        if anomaly:
            reasons.append(anomaly)

        # 6.7. Пользовательские правила скоринга
        if cls._custom_penalties or cls._custom_boosters:
            combined_lower = combined_text.lower()
            for pattern, label in cls._custom_penalties:
                if re.search(pattern, combined_lower, re.IGNORECASE):
                    flaws.append(label)
            for pattern, label, delta in cls._custom_boosters:
                if re.search(pattern, combined_lower, re.IGNORECASE):
                    score += delta
                    reasons.append(label)

        # 7. Штрафы за обнаруженные риски и дефекты (до -30 баллов)
        if flaws:
            defect_count = len(flaws)
            penalty = min(30, defect_count * 25)
            score -= penalty

        # Ограничение диапазона 0-100
        final_score = max(0, min(100, score))

        # Присвоение градации сделки
        if final_score >= 85:
            grade = "GEM"
        elif final_score >= 70:
            grade = "HOT"
        elif final_score >= 50:
            grade = "FAIR"
        else:
            grade = "CAUTION"

        return final_score, grade, reasons, flaws

    @staticmethod
    def _parse_listing_age_hours(published_at: str) -> Optional[float]:
        """Парсинг русских относительных дат ('2 часа назад', 'Вчера', '3 дня назад') в часы"""
        if not published_at:
            return None

        text = published_at.lower().strip()

        # 'N минут назад' / 'N минуту назад'
        m = re.search(r"(\d+)\s*минут\w*\s+назад", text)
        if m:
            return int(m.group(1)) / 60.0

        # 'N часов/часа назад'
        m = re.search(r"(\d+)\s*час\w*\s+назад", text)
        if m:
            return float(m.group(1))

        # 'N дней/дня назад'
        m = re.search(r"(\d+)\s*(?:день|дня|дней)\s+назад", text)
        if m:
            return int(m.group(1)) * 24.0

        # 'N недель/неделю назад'
        m = re.search(r"(\d+)\s*недел\w*\s+назад", text)
        if m:
            return int(m.group(1)) * 7 * 24.0

        # 'неделю назад' (без числа)
        if re.search(r"недел\w+\s+назад", text):
            return 7 * 24.0

        # 'N месяцев/месяц назад'
        m = re.search(r"(\d+)\s*месяц\w*\s+назад", text)
        if m:
            return int(m.group(1)) * 30 * 24.0

        # 'месяц назад' (без числа)
        if re.search(r"месяц\w*\s+назад", text):
            return 30 * 24.0

        # 'Сегодня в HH:MM' — считаем ~1 час для безопасности
        if "сегодня" in text:
            return 1.0

        # 'Вчера'
        if "вчера" in text:
            return 24.0

        # 'только что' / 'несколько минут назад'
        if "только что" in text or "несколько минут" in text:
            return 0.1

        return None

    @classmethod
    def detect_price_anomaly(
        cls,
        price: Optional[int],
        market_median: Optional[int],
        market_avg: Optional[int],
    ) -> Optional[str]:
        """Детекция аномально низких цен (возможный скам или исключительная сделка)"""
        if not price or price <= 0:
            return None
        ref = market_median if market_median and market_median > 0 else market_avg
        if not ref or ref <= 0:
            return None

        ratio = price / ref
        if ratio < 0.4:
            return "⚡ Аномально низкая цена (возможен скам или невероятная сделка)"
        if ratio < 0.5:
            return "⚡ Экстремально выгодная цена — требует проверки"
        return None

    @classmethod
    def load_custom_rules(cls, rules: List[dict]) -> None:
        """
        Загрузка пользовательских правил скоринга из БД.
        Каждый словарь: {pattern: str, label: str, score_delta: int}.
        Отрицательный score_delta — штраф (penalty), положительный — бонус (booster).
        """
        cls._custom_penalties = []
        cls._custom_boosters = []
        for rule in rules:
            pattern = rule.get("pattern", "")
            label = rule.get("label", "")
            delta = int(rule.get("score_delta", 0))
            if not pattern or not label:
                continue
            if delta < 0:
                cls._custom_penalties.append((pattern, label))
            elif delta > 0:
                cls._custom_boosters.append((pattern, label, delta))

    @classmethod
    async def generate_ai_verdict(
        cls,
        item: AvitoItem,
        market_median: Optional[int] = None,
        market_avg: Optional[int] = None,
        ai_settings: Optional[AISettings] = None
    ) -> Optional[str]:
        """
        Генерация экспертного вердикта ИИ (OpenAI / DeepSeek / OpenRouter / Ollama).
        Возвращает краткое аналитическое резюме из 2-3 предложений.
        """
        if not ai_settings or not ai_settings.enabled:
            return None

        provider = (ai_settings.provider or "deepseek").lower()
        api_key = ai_settings.api_key or ""
        model = ai_settings.model or "deepseek-chat"
        api_base = ai_settings.api_base.rstrip("/") if ai_settings.api_base else ""

        # Установка эндпоинтов по умолчанию для провайдеров
        if not api_base:
            if provider == "deepseek":
                api_base = "https://api.deepseek.com"
            elif provider == "openai":
                api_base = "https://api.openai.com/v1"
            elif provider == "openrouter":
                api_base = "https://openrouter.ai/api/v1"
            elif provider == "ollama":
                api_base = "http://localhost:11434/v1"
            else:
                api_base = "https://api.deepseek.com"

        # Формирование контекста товара для нейросети
        benchmark = market_median or market_avg
        market_context = f"Среднерыночная цена: {benchmark:,} руб." if benchmark else "Рыночная цена не определена"
        seller_info = f"Продавец: {item.seller.name if item.seller else 'Частное лицо'}, рейтинг: {item.seller.rating if item.seller else 'нет'}"
        
        prompt_content = f"""Проанализируй объявление на Авито и дай краткий экспертный вердикт (2-3 емких предложения на русском):
- Товар: {item.title}
- Цена: {format(item.price, ',') if item.price is not None else 'не указана'} руб. ({market_context})
- Старая цена: {item.old_price} руб.
- Локация: {item.address or item.metro or 'Не указана'}
- Авито Доставка: {'Да' if item.delivery_available else 'Нет'}
- {seller_info}
- Характеристики: {json.dumps(item.params, ensure_ascii=False)}
- Описание: {item.description or 'Описание отсутствует'}

Сформулируй:
1. Выгода цены и маржинальность для покупки/перепродажи.
2. Скрытые риски, дефекты или подозрительные моменты (если есть).
3. Окончательная рекомендация (Брать / Торговаться / Пропустить)."""

        system_instruction = (
            "Ты — строгий профессиональный эксперт-байер и ресейлер площадки Авито. "
            "Давай краткие, бескомпромиссные, полезные выводы без воды в 2-3 предложениях."
        )

        headers = {
            "Content-Type": "application/json"
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": prompt_content}
            ],
            "temperature": 0.3,
            "max_tokens": 300
        }

        url = f"{api_base}/chat/completions"
        cache_key = hashlib.sha256(json.dumps([url, model, payload, hashlib.sha256(api_key.encode()).hexdigest()], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        cached = cls._verdict_cache.get(cache_key)
        if cached and time.time() - cached[0] < 86400:
            cls._verdict_cache.move_to_end(cache_key)
            return cached[1]
        day = time.strftime('%Y-%m-%d')
        if cls._budget_day != day:
            cls._budget_day, cls._budget_calls = day, 0
        if cls._budget_calls >= 100:
            logger.warning('LLM daily process budget exhausted')
            return None
        cls._budget_calls += 1

        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                response = await client.post(url, headers=headers, json=payload)
                if response.status_code == 200:
                    data = response.json()
                    choices = data.get("choices", [])
                    if choices and len(choices) > 0:
                        verdict = choices[0].get("message", {}).get("content", "").strip()
                        cls._verdict_cache[cache_key] = (time.time(), verdict[:2000])
                        while len(cls._verdict_cache) > 1024:
                            cls._verdict_cache.popitem(last=False)
                        return verdict[:2000]
                else:
                    logger.warning('LLM request failed: HTTP %s', response.status_code)
        except Exception as e:
            logger.error('LLM request failed: %s', type(e).__name__)

        return None


deal_scoring_engine = DealScoringEngine()
