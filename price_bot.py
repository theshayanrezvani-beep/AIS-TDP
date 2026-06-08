# -*- coding: utf-8 -*-
"""
ربات قیمت بازار تلگرام — نرخ ارز، طلا و سکه
دو بار در روز (۱۲ ظهر و ۱۲ شب به وقت تهران) یک پیام تمیز توی کانال می‌گذارد.
اجرا روی GitHub Actions (رایگان، خارج از ایران، مقاوم به فیلترینگ).

نکته‌ی کلیدی: چون ربات بیرون ایران اجرا می‌شود، منبع داده هم باید بیرون ایران میزبانی
شود وگرنه IP خارجی بلاک می‌شود. پس:
  - قیمت‌های تومانی (ارز/سکه/طلا) از priceto.day  (میزبان: Netlify — جهانی)
  - انس جهانی طلا از goldprice.org                 (جهانی)
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

# اگر مقادیر priceto به ریال آمد (۱۰ برابر) این را ۱۰ بگذار تا تومان شود
IRR_DIVISOR = float(os.getenv("IRR_DIVISOR", "1"))

PRICETO_BASE  = "https://api.priceto.day/v1/latest/irr"
GOLDPRICE_URL = "https://data-asg.goldprice.org/dbXRates/USD"

PERSIAN_DIGITS = os.getenv("PERSIAN_DIGITS", "1") not in ("0", "false", "False")
HTTP_TIMEOUT   = 20
UA = {"User-Agent": "Mozilla/5.0 (price-bot)", "Accept": "application/json"}

# ترتیب نمایش.  هر آیتم: (کلید, برچسب فارسی, واحد)
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

# نمادهای کاندید برای priceto.day (اولین نمادی که جواب دهد استفاده می‌شود)
PRICETO_SYMBOLS = {
    "USD":        ["usd"],
    "EUR":        ["euro", "eur"],
    "AED":        ["aed", "derham", "dirham"],
    "GBP":        ["gbp", "pound"],
    "TRY":        ["try", "lira", "turkish-lira"],
    "CNY":        ["cny", "yuan"],
    "USDT":       ["tether", "usdt"],
    "GOLD18":     ["gold-18ayar", "gold-gram18", "geram18", "gold18", "18ayar"],
    "COIN_EMAMI": ["coin-emami", "emami"],
    "COIN_BAHAR": ["coin-baharazadi", "baharazadi"],
}

PRICE_KEYS  = ("price", "value", "rate", "p", "amount", "irr", "toman")
CHANGE_KEYS = ("change_percent", "changepercent", "change", "dp", "percent", "pc")

_raw_logged = False  # فقط یک‌بار پاسخ خام را چاپ می‌کنیم


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


def _to_float(v):
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except (ValueError, TypeError):
        return None


def _looks_like_price(n):
    if n is None:
        return False
    a = abs(n)
    if 1_000_000_000 <= a <= 2_000_000_000:   # احتمالاً timestamp
        return False
    if 1900 <= a <= 2100 and float(n).is_integer():  # احتمالاً سال
        return False
    return 0 < a < 1e12


def extract_price(obj):
    """قیمت را از هر شکلِ JSON پیدا می‌کند (دفاعی)."""
    # ۱) کلید نام‌دار
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in PRICE_KEYS:
                n = _to_float(v)
                if _looks_like_price(n):
                    return n
        for v in obj.values():
            r = extract_price(v)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = extract_price(v)
            if r is not None:
                return r
    else:
        n = _to_float(obj)
        if _looks_like_price(n):
            return n
    return None


def extract_change(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in CHANGE_KEYS:
                n = _to_float(v)
                if n is not None and abs(n) < 100:
                    return n
        for v in obj.values():
            r = extract_change(v)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = extract_change(v)
            if r is not None:
                return r
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
def fetch_priceto():
    """قیمت‌های تومانی از priceto.day. خروجی dict ممکن است ناقص باشد."""
    global _raw_logged
    result = {}
    for key, candidates in PRICETO_SYMBOLS.items():
        for sym in candidates:
            url = f"{PRICETO_BASE}/{sym}"
            try:
                r = requests.get(url, headers=UA, timeout=HTTP_TIMEOUT)
                if r.status_code != 200:
                    continue
                data = r.json()
            except Exception as e:
                print(f"[priceto] {key}/{sym} خطا: {e}")
                continue

            if not _raw_logged:
                print(f"[priceto] نمونه پاسخ خام برای {sym}: {str(data)[:500]}")
                _raw_logged = True

            price = extract_price(data)
            if price is None:
                continue
            price = price / IRR_DIVISOR
            result[key] = {"price": price, "pct": extract_change(data)}
            print(f"[priceto] {key} ← {sym} = {int(price):,}")
            break
        else:
            print(f"[priceto] {key} پیدا نشد (نمادها: {candidates})")
    return result


def fetch_ounce():
    """انس جهانی طلا به دلار از goldprice.org"""
    try:
        r = requests.get(GOLDPRICE_URL, headers=UA, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        item = data["items"][0]
        price = item.get("xauPrice")
        pct = item.get("pcXau")
        if price:
            print(f"[goldprice] انس = {price}")
            return {"OUNCE": {"price": price, "pct": pct}}
    except Exception as e:
        print(f"[goldprice] خطا: {e}")
    return {}


def get_prices():
    prices = fetch_priceto()
    prices.update(fetch_ounce())
    return prices


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
    for key, label, unit in items:
        info = prices.get(key)
        if not info:
            continue
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

    prices = get_prices()
    if not prices:
        print("هیچ داده‌ای از منابع گرفته نشد؛ برای جلوگیری از پست خالی خارج می‌شویم.")
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
