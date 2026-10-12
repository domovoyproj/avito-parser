"""Detect an actual access challenge, not the presence of a CAPTCHA SDK."""
import re
from bs4 import BeautifulSoup


def access_challenge(html: str, url: str = "", title: str = ""):
    soup = BeautifulSoup(html or "", "html.parser")
    has_feed = bool(soup.find(attrs={"data-marker": re.compile(r"^item(?:$|/|-view/)", re.I)})) or bool(re.search(r'"items"\s*:\s*\[', html or ""))
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    for tag in list(soup.find_all(attrs={"hidden": True})) + list(soup.find_all(attrs={"aria-hidden": "true"})):
        if not tag.decomposed:
            tag.decompose()
    text = soup.get_text(" ", strip=True).lower()
    heading = (title or (soup.title.get_text() if soup.title else "")).lower()
    address = url.lower()
    if any(marker in heading for marker in ("доступ ограничен", "access denied", "firewall")):
        return "blocked"
    if not has_feed and "доступ ограничен" in text:
        return "blocked"
    captcha_element = soup.find(attrs={"data-marker": re.compile(r"^captcha", re.I)})
    captcha_frame = soup.find("iframe", src=re.compile(r"smartcaptcha|recaptcha|hcaptcha", re.I))
    captcha_form = soup.find("form", action=re.compile(r"captcha", re.I))
    if captcha_element or (not has_feed and (captcha_frame or captcha_form)):
        return "captcha"
    if any(marker in address for marker in ("validate_captcha", "/captcha", "/firewall", "/blocked")):
        return "captcha" if "captcha" in address else "blocked"
    if any(marker in heading for marker in ("captcha", "проверка браузера", "подтвердите, что вы")):
        return "captcha"
    if not has_feed and any(marker in text for marker in ("подтвердите, что вы не робот", "пройдите проверку", "проверка браузера")):
        return "captcha"
    return None
