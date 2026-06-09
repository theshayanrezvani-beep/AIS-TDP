# -*- coding: utf-8 -*-
"""
ربات قیمت بازار تلگرام (نسخه نهایی — Relay به priceto.day)
دو بار در روز (۱۲ ظهر و ۱۲ شب تهران) پیام قیمت توی کانال می‌گذارد.

معماری: Cloudflare Worker (worker.js) قیمت‌ها را از priceto.day می‌گیرد و
به شکلِ {items:{KEY:{symbol,data}}, missing:[...]} برمی‌گرداند.
ربات روی گیت‌هاب فقط با Worker حرف می‌زند (RELAY_URL).
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

VERSION = "7.0 (relay -> tgju)"

PERSIAN_WEEKDAYS = ["شنبه", "یک‌شنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنج‌شنبه", "جمعه"]
PERSIAN_MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
                  "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHANNEL_ID         = os.getenv("TELEGRAM_CHANNEL_ID", "@yourchannel").strip()
SIGNATURE          = os.getenv("SIGNATURE", "📊 @yourchannel | کانال نرخ بازار").strip()
RELAY_URL          = os.getenv("RELAY_URL", "").strip()

PERSIAN_DIGITS = os.getenv("PERSIAN_DIGITS", "1") not in ("0", "false", "False")
HTTP_TIMEOUT   = 30

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
TOMAN_KEYS = {"USD", "EUR", "AED", "GBP", "TRY", "CNY", "USDT",
              "GOLD18", "COIN_EMAMI", "COIN_BAHAR"}

PRICE_KEYS  = ("price", "value", "rate", "p", "amount", "irr", "close", "last")
CHANGE_KEYS = ("change_percent", "changepercent", "change", "dp", "percent", "pc", "cp")


def to_persian_digits(s):
    if not PERSIAN_DIGITS:
        return s
    return str(s).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def _to_float(v):
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except (ValueError, TypeError):
        return None


def _looks_like_price(n):
    if n is None:
        return False
    a = abs(n)
    if 1_000_000_000 <= a <= 2_000_000_000:   # timestamp
        return False
    if 1900 <= a <= 2100 and float(n).is_integer():  # سال
        return False
    return 0 < a < 1e13


def extract_price(obj):
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


def fmt_price(value, unit):
    if value is None:
        return None
    num = float(value)
    if num == 0:
        return None
    if unit == "دلار" and num != int(num):
        txt = "{:,.2f}".format(num)
    else:
        txt = "{:,}".format(int(round(num)))
    return to_persian_digits(txt)


def change_badge(pct):
    if pct is None:
        return ""
    if pct > 0:
        return "  🔺" + to_persian_digits("{:.2f}".format(abs(pct))) + "٪"
    if pct < 0:
        return "  🔻" + to_persian_digits("{:.2f}".format(abs(pct))) + "٪"
    return "  ➖"


def _num(v):
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except (ValueError, TypeError):
        return None


def fetch_data():
    if not RELAY_URL:
        print("خطا: RELAY_URL تنظیم نشده.")
        return {}
    try:
        r = requests.get(RELAY_URL, timeout=HTTP_TIMEOUT)
        print(f"[Relay] status {r.status_code}")
        payload = r.json()
    except Exception as e:
        print(f"[Relay] خطا: {e}")
        return {}

    if not isinstance(payload, dict):
        print(f"[Relay] پاسخ نامعتبر: {str(payload)[:200]}")
        return {}

    print(f"[Relay] منبع: {payload.get('usedSource')}")
    items = payload.get("items", {})
    missing = payload.get("missing", [])
    if missing:
        print(f"[Relay] اقلامِ پیدانشده: {missing}")
    if payload.get("debugKeys"):
        print(f"[Relay] کلیدهای محتمل برای اشکال‌زدایی: {payload['debugKeys']}")
    if not items:
        print(f"[Relay] هیچ قلمی نیامد. پاسخ: {str(payload)[:300]}")
        return {}

    result = {}
    for key, node in items.items():
        price = _num(node.get("p") if isinstance(node, dict) else node)
        if price is None or price == 0:
            continue
        pct = _num(node.get("dp")) if isinstance(node, dict) else None
        if pct is not None and str(node.get("dt", "")).lower() in ("low", "down"):
            pct = -abs(pct)
        result[key] = {"price": price, "pct": pct}
        print(f"[Relay] {key} ✓ = {price}")

    # tgju مقادیر داخلی را به ریال می‌دهد؛ به تومان تبدیل کن (انس جهانی دلار است و دست‌نخورده)
    usd = result.get("USD", {}).get("price")
    if usd and usd > 300000:
        print("[Relay] مقادیر ریالی تشخیص داده شد؛ تومانی‌ها ÷۱۰ می‌شوند.")
        for k in TOMAN_KEYS:
            if k in result and result[k]["price"]:
                result[k]["price"] = result[k]["price"] / 10
    return result


def build_header():
    now = datetime.utcnow()
    if ZoneInfo:
        try:
            now = datetime.now(ZoneInfo("Asia/Tehran"))
        except Exception:
            pass
    if jdatetime:
        j = jdatetime.datetime.fromgregorian(datetime=now.replace(tzinfo=None))
        wd = PERSIAN_WEEKDAYS[j.weekday()]
        date_line = f"{wd} {to_persian_digits(j.day)} {PERSIAN_MONTHS[j.month - 1]} {to_persian_digits(j.year)}"
    else:
        date_line = to_persian_digits(now.strftime("%Y/%m/%d"))
    time_str = to_persian_digits(now.strftime("%H:%M"))
    return ("<b>نرخ طلا و ارز در بازار ایران</b>\n"
            f"🗓 {date_line} - ساعت {time_str}")


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
        lines.append(f"{label}: <b>{price_txt}</b> {unit}")
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
    parts += ["", html.escape(SIGNATURE)]
    return "\n".join(parts)


def send_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHANNEL_ID, "text": text,
               "parse_mode": "HTML", "disable_web_page_preview": True}
    r = requests.post(url, json=payload, timeout=HTTP_TIMEOUT)
    if r.status_code != 200:
        print(f"[تلگرام] خطا {r.status_code}: {r.text}")
        r.raise_for_status()
    print("[تلگرام] پیام با موفقیت ارسال شد ✅")


def main():
    print(f"=== price_bot نسخه {VERSION} ===")
    if not TELEGRAM_BOT_TOKEN:
        print("خطا: TELEGRAM_BOT_TOKEN تنظیم نشده.")
        sys.exit(1)
    prices = fetch_data()
    if not prices:
        print("داده‌ای نیامد؛ پست منتشر نمی‌شود.")
        sys.exit(1)
    if not any(k in prices for k in ("USD", "USDT")):
        print("داده‌ی کلیدی (دلار/تتر) نیست؛ پست منتشر نمی‌شود.")
        sys.exit(1)
    message = build_message(prices)
    print("─── پیش‌نمایش ───")
    print(message)
    print("────────────────")
    send_message(message)


if __name__ == "__main__":
    main()
