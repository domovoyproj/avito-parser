from datetime import datetime
import asyncio
import hashlib
import hmac
import json
import logging
import time
from typing import List, Optional
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator
from config import config, ScraperConfig
from database import db
from proxy_manager import ProxyManager, proxy_manager
from browser_engine import browser_engine
from http_engine import http_engine
from models import AISettings, WebhookSettings
logger = logging.getLogger("AvitoSettings")
router = APIRouter()

@router.get("/api/proxies")
async def api_get_proxies():
    proxies = proxy_manager.proxies
    return {
        "enabled": config.proxy.enabled,
        "total_count": len(proxies),
        "proxies": proxies,
        "default_proxy": config.proxy.default_proxy,
        "proxies_file": str(proxy_manager.proxies_file)
    }

class AddProxyRequest(BaseModel):
    proxy_text: str  # может быть 1 прокси или список через перенос строки

@router.post("/api/proxies")
async def api_add_proxies(req: AddProxyRequest):
    added = 0
    lines = req.proxy_text.strip().splitlines()
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#"):
            if proxy_manager.add_proxy(line):
                added += 1
    logger.info(f"🌐 Добавлено {added} новых прокси в пул")
    return {"status": "success", "added_count": added, "total": len(proxy_manager.proxies)}

class DeleteProxyRequest(BaseModel):
    proxy: str

@router.delete("/api/proxies")
async def api_delete_proxy(req: DeleteProxyRequest):
    if req.proxy in proxy_manager.proxies:
        proxy_manager.proxies.remove(req.proxy)
        # Перезаписываем файл
        if proxy_manager.proxies_file.exists():
            with open(proxy_manager.proxies_file, "w", encoding="utf-8") as f:
                for p in proxy_manager.proxies:
                    f.write(f"{p}\n")
        logger.info(f"Удален прокси {req.proxy}")
        return {"status": "success"}
    raise HTTPException(status_code=404, detail="Прокси не найден")

@router.post("/api/proxies/check-all")
async def api_check_all_proxies():
    results = []
    
    async def check_one(p: str):
        start_t = time.time()
        ok = await proxy_manager.check_proxy(p, timeout_sec=6)
        latency = round((time.time() - start_t) * 1000)
        return {
            "proxy": p,
            "alive": ok,
            "latency_ms": latency if ok else None
        }

    tasks = [check_one(p) for p in proxy_manager.proxies]
    if tasks:
        results = await asyncio.gather(*tasks)

    alive_count = sum(1 for r in results if r["alive"])
    return {
        "total": len(results),
        "alive_count": alive_count,
        "dead_count": len(results) - alive_count,
        "results": results
    }


# ==============================================================================
# REST API: НАСТРОЙКИ И ТЕЛЕГРАМ
# ==============================================================================

@router.get("/api/settings")
async def api_get_settings():
    return {
        "scraper": {
            "headless": config.scraper.headless,
            "timeout_ms": config.scraper.timeout_ms,
            "page_delay_min": config.scraper.page_delay_min,
            "page_delay_max": config.scraper.page_delay_max,
            "max_pages": config.scraper.max_pages,
            "user_agent": config.scraper.user_agent,
            "save_cookies": config.scraper.save_cookies,
            "cookies_exist": config.scraper.cookies_file.exists()
        },
        "proxy": {
            "enabled": config.proxy.enabled,
            "default_proxy": config.proxy.default_proxy or "",
            "rotate": config.proxy.rotate,
            "count": len(proxy_manager.proxies)
        },
        "telegram": {
            "bot_token": config.telegram.bot_token,
            "admin_chat_ids": config.telegram.admin_chat_ids,
            "notification_interval_min": config.telegram.notification_interval_min,
            "send_photos": config.telegram.send_photos
        },
        "web": {
            "host": config.web.host,
            "port": config.web.port,
            "auto_open_browser": config.web.auto_open_browser
        }
    }

class SaveSettingsRequest(BaseModel):
    headless: bool
    timeout_ms: int
    page_delay_min: float
    page_delay_max: float
    proxy_enabled: bool
    default_proxy: Optional[str] = None
    bot_token: Optional[str] = None
    admin_chat_ids: List[int] = Field(default_factory=list)
    notification_interval_min: int = Field(default=10, gt=0)
    send_photos: bool = True

    @model_validator(mode="after")
    def validate_scraper(self):
        ScraperConfig(timeout_ms=self.timeout_ms, page_delay_min=self.page_delay_min,
                      page_delay_max=self.page_delay_max)
        return self

@router.post("/api/settings")
async def api_save_settings(req: SaveSettingsRequest):
    # Complete fallible work before publishing settings to running services.
    candidate = config.model_copy(deep=True)
    candidate.scraper.headless = req.headless
    candidate.scraper.timeout_ms = req.timeout_ms
    candidate.scraper.page_delay_min = req.page_delay_min
    candidate.scraper.page_delay_max = req.page_delay_max
    
    candidate.proxy.enabled = req.proxy_enabled
    candidate.proxy.default_proxy = (req.default_proxy or "").strip() or None
    
    candidate.telegram.bot_token = req.bot_token or ""
    candidate.telegram.admin_chat_ids = req.admin_chat_ids
    candidate.telegram.notification_interval_min = req.notification_interval_min
    candidate.telegram.send_photos = req.send_photos

    try:
        staged_proxies = ProxyManager(proxies_file=candidate.proxy.proxies_file,
                                      default_proxy=candidate.proxy.default_proxy or "")
        candidate.save_to_env()
    except OSError:
        raise HTTPException(status_code=500, detail="Не удалось сохранить настройки. Проверьте доступ к файлам конфигурации.") from None

    config.scraper = candidate.scraper
    config.proxy = candidate.proxy
    config.telegram = candidate.telegram
    proxy_manager.default_proxy = candidate.proxy.default_proxy
    proxy_manager.proxies = staged_proxies.proxies
    proxy_manager._current_index = 0
    active_proxy = proxy_manager.get_proxy() if config.proxy.enabled else None
    browser_engine.headless = config.scraper.headless
    browser_engine.proxy_str = active_proxy
    http_engine.proxy = active_proxy
    logger.info("⚙️ Настройки успешно обновлены и сохранены в .env")
    return {"status": "success", "message": "Настройки сохранены"}

class TestTelegramRequest(BaseModel):
    bot_token: Optional[str] = None
    chat_id: int

@router.post("/api/telegram/test")
async def api_test_telegram(req: TestTelegramRequest):
    token = req.bot_token or config.telegram.bot_token
    if not token:
        raise HTTPException(status_code=400, detail="Токен Telegram бота не указан")

    text = (
        "🤖 <b>Тестовое уведомление Avito Parser Pro</b>\n\n"
        "✅ Подключение к веб-панели работает отлично!\n"
        f"📅 Время проверки: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
    )

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": req.chat_id,
        "text": text,
        "parse_mode": "HTML"
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, data=payload)
            data = resp.json()
            if data.get("ok"):
                return {"status": "success", "message": "Тестовое сообщение успешно отправлено!"}
            else:
                desc = data.get("description", "Неизвестная ошибка Telegram API")
                raise HTTPException(status_code=400, detail=f"Ошибка Telegram: {desc}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Не удалось связаться с Telegram: {e}")

@router.post("/api/settings/clear-cookies")
async def api_clear_cookies():
    if config.scraper.cookies_file.exists():
        config.scraper.cookies_file.unlink()
        logger.info("🍪 Файл cookies.json успешно очищен")
        return {"status": "success", "message": "Куки очищены"}
    return {"status": "info", "message": "Файл кук не был создан"}

# ==============================================================================
# REST API: НАСТРОЙКИ AI И LLM ЭКСПЕРТИЗЫ
# ==============================================================================

class SaveAISettingsRequest(BaseModel):
    enabled: bool = False
    provider: str = "deepseek"
    api_key: str = ""
    clear_api_key: bool = False
    model: str = "deepseek-chat"
    api_base: str = "https://api.deepseek.com"
    prompt_template: Optional[str] = None

class TestAIRequest(BaseModel):
    provider: str = "deepseek"
    api_key: str = ""
    model: str = "deepseek-chat"
    api_base: str = "https://api.deepseek.com"

@router.get("/api/ai/settings")
async def api_get_ai_settings():
    settings = await db.get_ai_settings()
    data = settings.model_dump(mode='json')
    data['has_api_key'] = bool(data['api_key'])
    data['api_key'] = ''
    return {"settings": data}

@router.post("/api/ai/settings")
async def api_save_ai_settings(req: SaveAISettingsRequest):
    existing = await db.get_ai_settings()
    settings = AISettings(
        enabled=req.enabled,
        provider=req.provider,
        api_key='' if req.clear_api_key else (req.api_key.strip() or existing.api_key),
        model=req.model.strip(),
        api_base=req.api_base.strip(),
        prompt_template=req.prompt_template
    )
    saved = await db.save_ai_settings(settings)
    logger.info(f"🧠 Настройки AI обновлены: провайдер={saved.provider}, модель={saved.model}, enabled={saved.enabled}")
    return {"status": "success", **(await api_get_ai_settings()), "message": "Настройки AI успешно сохранены"}

@router.post("/api/ai/test")
async def api_test_ai_connection(req: TestAIRequest):
    provider = (req.provider or "deepseek").lower()
    api_key = req.api_key.strip() or (await db.get_ai_settings()).api_key
    model = req.model.strip() or "deepseek-chat"
    api_base = req.api_base.strip().rstrip("/")

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

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Ты тестовый помощник. Ответь одним кратким предложением 'Подключение к AI успешно установлено!'."},
            {"role": "user", "content": "Тест подключения"}
        ],
        "max_tokens": 50,
        "temperature": 0.1
    }

    url = f"{api_base}/chat/completions"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                reply = data.get("choices", [{}])[0].get("message", {}).get("content", "OK")
                return {
                    "status": "success",
                    "message": "Соединение с AI успешно установлено!",
                    "provider": provider,
                    "model": model,
                    "response": reply
                }
            else:
                error_msg = f"HTTP {resp.status_code}: {resp.text[:200]}"
                raise HTTPException(status_code=400, detail=f"Ошибка AI-провайдера: {error_msg}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Не удалось подключиться к {url}: {e}")


# ==============================================================================
# REST API: ПОЛЬЗОВАТЕЛЬСКИЕ ПРАВИЛА СКОРИНГА
# ==============================================================================

class CustomRuleCreateRequest(BaseModel):
    pattern: str
    label: str
    score_delta: int = -25

@router.get("/api/scoring/rules")
async def api_get_scoring_rules(active_only: bool = False):
    rules = await db.get_custom_scoring_rules(active_only=active_only)
    return {"rules": rules}

@router.post("/api/scoring/rules")
async def api_add_scoring_rule(req: CustomRuleCreateRequest):
    if not req.pattern.strip():
        raise HTTPException(status_code=400, detail="Паттерн не может быть пустым")
    if not req.label.strip():
        raise HTTPException(status_code=400, detail="Описание правила не может быть пустым")
    rule_id = await db.add_custom_scoring_rule(req.pattern, req.label, req.score_delta)
    logger.info(f"📐 Добавлено правило скоринга #{rule_id}: '{req.label}' ({req.score_delta:+d} баллов)")
    return {"status": "success", "id": rule_id}

@router.delete("/api/scoring/rules/{rule_id}")
async def api_delete_scoring_rule(rule_id: int):
    ok = await db.delete_custom_scoring_rule(rule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Правило не найдено")
    logger.info(f"🗑 Удалено правило скоринга #{rule_id}")
    return {"status": "success"}

@router.patch("/api/scoring/rules/{rule_id}/toggle")
async def api_toggle_scoring_rule(rule_id: int):
    ok = await db.toggle_custom_scoring_rule(rule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Правило не найдено")
    return {"status": "success"}


# ==============================================================================
# REST API: НАСТРОЙКИ WEBHOOK
# ==============================================================================

class SaveWebhookSettingsRequest(BaseModel):
    url: str = ""
    enabled: bool = False
    send_new: bool = True
    send_drops: bool = True
    min_deal_score: int = 0
    secret: str = ""

@router.get("/api/webhook/settings")
async def api_get_webhook_settings():
    settings = await db.get_webhook_settings()
    return {"settings": settings.model_dump(mode="json")}

@router.post("/api/webhook/settings")
async def api_save_webhook_settings(req: SaveWebhookSettingsRequest):
    from models import WebhookSettings
    settings = WebhookSettings(
        url=req.url.strip(),
        enabled=req.enabled,
        send_new=req.send_new,
        send_drops=req.send_drops,
        min_deal_score=req.min_deal_score,
        secret=req.secret.strip()
    )
    saved = await db.save_webhook_settings(settings)
    logger.info(f"🔗 Настройки Webhook сохранены: URL={saved.url}, enabled={saved.enabled}")
    return {"status": "success", "settings": saved.model_dump(mode="json"), "message": "Настройки Webhook успешно сохранены"}

@router.post("/api/webhook/test")
async def api_test_webhook():
    settings = await db.get_webhook_settings()
    if not settings.url:
        raise HTTPException(status_code=400, detail="Webhook URL не указан")

    test_payload = {
        "event": "test",
        "message": "Тестовое уведомление от Avito Parser Pro",
        "timestamp": datetime.now().isoformat()
    }
    headers = {"Content-Type": "application/json"}
    body_bytes = json.dumps(test_payload, ensure_ascii=False).encode("utf-8")
    if settings.secret:
        sig = hmac.new(settings.secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
        headers["X-Webhook-Signature"] = sig

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(settings.url, content=body_bytes, headers=headers)
            return {
                "status": "success",
                "status_code": resp.status_code,
                "response": resp.text[:200]
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Не удалось отправить тестовый Webhook: {e}")

# ==============================================================================
# REST API: ЛОГИ
# ==============================================================================


