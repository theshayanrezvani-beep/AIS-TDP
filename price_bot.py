# -*- coding: utf-8 -*-
"""
ربات قیمت بازار تلگرام — نرخ ارز، طلا و سکه
دو بار در روز (۱۲ ظهر و ۱۲ شب به وقت تهران) یک پیام تمیز توی کانال می‌گذارد.
اجرا روی GitHub Actions (رایگان، خارج از ایران).

منبع داده: BrsApi.ir  (همه چیز را یکجا و به تومان می‌دهد: ارز، تتر، طلا، سکه، انس جهانی)
نکته‌ی حیاتی: BrsApi پشتِ فایروالِ 6G است و User-Agent پیش‌فرضِ پایتون را بلاک می‌کند،
پس حتماً با یک User-Agent شبیهِ مرورگرِ واقعی درخواست می‌دهیم.
"""

import os
import sys
import html
from datetime import datetime

import requests

try:
    from zoneinfo import ZoneInfo
except Exception:  # noqa
    ZoneInfo = None

try:
    import jdatetime
except Exception:  # noqa
    jdatetime = None


# ─────────────────────────── تنظیمات (از env) ───────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHANNEL_ID         = os.getenv("TELEGRAM_CHANNEL_ID", "@yourchannel").strip()
SIGNATURE          = os.getenv("SIGNATURE", "📊 @yourchannel | کانال نرخ بازار").strip()
BRSAPI_KEY         = os.getenv("BRSAPI_KEY", "").strip()

# اندپوینتِ درست و در دسترس از خارج (نه api.brsapi.ir که فقط داخل ایران جواب می‌دهد)
BRSAPI_URL = "https://BrsApi.ir/Api/Market/Gold_Currency.php"

PERSIAN_DIGITS = os.getenv("PERSIAN_DIGITS", "1") not in ("0", "false", "False")
HTTP_TIMEOUT   = 25

# User-Agent معتبرِ مرورگر — کلیدِ عبور از فایروالِ BrsApi
BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fa-IR,fa;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://brsapi.ir/",
}

# ترتیب نمایش.  (کلید, برچسب فارسی, واحد پیش‌فرض)
CURRENCY_ITEMS = [
    ("USD",  "🇺🇸 دلار آمریکا", "تومان"),
    ("EUR",  "🇪🇺 یورو",        "تومان"),
    ("AED",  "🇦🇪 درهم امارات", "تومان"),
    ("GBP",  "🇬🇧 پوند انگلیس", "تومان"),
    ("TRY",  "🇹🇷 لیر ترکیه",   "تومان"),
    ("CNY",  "🇨🇳 یوان چین",    "تومان"),
    ("USDT", "₮ تتر",           "تومان"),
]
GOLD_ITEMS = [
    ("GOLD18",     "🟡 طلای ۱۸ عیار (گرم)", "تومان"),
    ("COIN_EMAMI", "🏅 سکه امامی",          "تومان"),
    ("COIN_BAHAR", "🏅 سکه بهار آزادی",     "تومان"),
    ("OUNCE",      "🌍 انس جهانی طلا",       "دلار"),
]

# نگاشت: کلید → (نمادهای ممکن, بخشی از نام فارسی)
BRS_MATCH = {
    "USD":        (["USD"],                          ["دلار آمریکا", "دلار"]),
    "EUR":        (["EUR"],                          ["یورو"]),
    "AED":        (["AED"],                          ["درهم"]),
    "GBP":        (["GBP"],                          ["پوند"]),
    "TRY":        (["TRY"],                          ["لیر"]),
    "CNY":        (["CNY"],                          ["یوان"]),
    "USDT":       (["USDT"],                         ["تتر"]),
    "GOLD18":     (["IR_GOLD_18K", "18K", "GOLD18"], ["18 عیار", "۱۸ عیار", "طلای 18"]),
    "COIN_EMAMI": (["IR_COIN_EMAMI", "SEKE_EMAMI"],  ["امامی"]),
    "COIN_BAHAR": (["IR_COIN_BAHAR", "SEKE_BAHAR"],  ["بهار آزادی", "بهار"]),
    "OUNCE":      (["XAUUSD", "XAU", "ONS"],         ["انس طلا"]),
}


# ─────────────────────────── کمک‌تابع‌ها ───────────────────────────
def to_persian_digits(s):
    if not PERSIAN_DIGITS:
        return s
    return str(s).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def fmt_price(value, unit):
    try:
        num = float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return None
    if num == 0:
        return None
    if unit == "دلار" and num != int(num):
        txt = "{:,.2f}".format(num)
    else:
        txt = "{:,}".format(int(round(num)))
    return to_persian_digits(txt)


def parse_change(value):
    if value is None:
        return None
    try:
        return float(str(value).replace("%", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def change_badge(pct):
    if pct is None:
        return ""
    if pct > 0:
        return "  🔺" + to_persian_digits("{:.2f}".format(abs(pct))) + "٪"
    if pct < 0:
        return "  🔻" + to_persian_digits("{:.2f}".format(abs(pct))) + "٪"
    return "  ➖"


# ─────────────────────────── دریافت داده ───────────────────────────
def _flatten(data):
    """همه‌ی آیتم‌های دیکشنری را از لیست‌های تو‌در‌توی پاسخ جمع می‌کند."""
    items = []
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list):
                items.extend([x for x in v if isinstance(x, dict)])
            elif isinstance(v, dict):
                items.append(v)
    elif isinstance(data, list):
        items = [x for x in data if isinstance(x, dict)]
    return items


def fetch_brsapi():
    if not BRSAPI_KEY:
        print("خطا: BRSAPI_KEY تنظیم نشده. کلید رایگان را از brsapi.ir بگیر و در Secrets بگذار.")
        return {}
    try:
        r = requests.get(BRSAPI_URL, params={"key": BRSAPI_KEY},
                         headers=BROWSER_HEADERS, timeout=HTTP_TIMEOUT)
        print(f"[BrsApi] status = {r.status_code}")
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[BrsApi] خطا در دریافت: {e}")
        return {}

    items = _flatten(data)
    print(f"[BrsApi] {len(items)} آیتم در پاسخ بود.")
    if items:
        # نمونه‌ی چند آیتم اول برای اطمینان از ساختار
        print(f"[BrsApi] نمونه: {str(items[:3])[:400]}")

    result = {}
    for key, (symbols, names) in BRS_MATCH.items():
        syms = [s.upper() for s in symbols]
        found = None
        for it in items:                       # اول با نماد
            if str(it.get("symbol", "")).upper() in syms:
                found = it
                break
        if found is None:                       # بعد با نام
            for it in items:
                nm = str(it.get("name", ""))
                if any(n in nm for n in names):
                    found = it
                    break
        if found is None:
            print(f"[BrsApi] {key} پیدا نشد.")
            continue
        result[key] = {
            "price": found.get("price"),
            "pct": parse_change(found.get("change_percent", found.get("change_percentage"))),
            "unit": (str(found.get("unit", "")).strip() or None),
        }
        print(f"[BrsApi] {key} ✓ = {found.get('price')} {found.get('unit','')}")
    return result


# ─────────────────────────── ساخت پیام ───────────────────────────
def build_header():
    now = datetime.utcnow()
    if ZoneInfo:
        try:
            now = datetime.now(ZoneInfo("Asia/Tehran"))
        except Exception:
            pass
    if jdatetime:
        j = jdatetime.datetime.fromgregorian(datetime=now.replace(tzinfo=None))
        date_line = to_persian_digits(j.strftime("%A %d %B %Y"))
    else:
        date_line = to_persian_digits(now.strftime("%Y/%m/%d"))
    hour = now.hour
    time_str = to_persian_digits(now.strftime("%H:%M"))
    period = "🌞 گزارش ظهر" if 6 <= hour < 18 else "🌙 گزارش شب"
    return (
        "💰 <b>نرخ لحظه‌ای بازار ایران</b>\n"
        f"🗓 {date_line}\n"
        f"🕛 ساعت {time_str} به وقت تهران — {period}"
    )


def render_section(title, items, prices):
    lines = [f"<b>{title}</b>"]
    any_row = False
    for key, label, default_unit in items:
        info = prices.get(key)
        if not info:
            continue
        unit = info.get("unit") or default_unit
        price_txt = fmt_price(info.get("price"), unit)
        if price_txt is None:
            continue
        badge = change_badge(info.get("pct"))
        lines.append(f"{label}: <b>{price_txt}</b> {unit}{badge}")
        any_row = True
    return "\n".join(lines) if any_row else None


def build_message(prices):
    sep = "━━━━━━━━━━━━━━━━━"
    parts = [build_header(), sep]
    cur = render_section("💵 ارز", CURRENCY_ITEMS, prices)
    gold = render_section("🪙 طلا و سکه", GOLD_ITEMS, prices)
    if cur:
        parts.append(cur)
    if gold:
        parts += [sep, gold]
    parts += [sep, html.escape(SIGNATURE)]
    return "\n".join(parts)


# ─────────────────────────── ارسال به تلگرام ───────────────────────────
def send_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    r = requests.post(url, json=payload, timeout=HTTP_TIMEOUT)
    if r.status_code != 200:
        print(f"[تلگرام] خطا {r.status_code}: {r.text}")
        r.raise_for_status()
    print("[تلگرام] پیام با موفقیت ارسال شد ✅")


# ─────────────────────────── main ───────────────────────────
def main():
    if not TELEGRAM_BOT_TOKEN:
        print("خطا: TELEGRAM_BOT_TOKEN تنظیم نشده.")
        sys.exit(1)

    prices = fetch_brsapi()
    if not prices:
        print("هیچ داده‌ای گرفته نشد؛ برای جلوگیری از پست خالی خارج می‌شویم.")
        sys.exit(1)
    if not any(k in prices for k in ("USD", "USDT", "GOLD18")):
        print("داده‌ی کلیدی (دلار/تتر/طلا) موجود نیست؛ پست منتشر نمی‌شود.")
        sys.exit(1)

    message = build_message(prices)
    print("─── پیش‌نمایش پیام ───")
    print(message)
    print("──────────────────────")
    send_message(message)


if __name__ == "__main__":
    main()
