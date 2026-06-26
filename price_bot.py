# -*- coding: utf-8 -*-
"""
ربات قیمت بازار تلگرام (نسخه ۸ — ۶ قلم + تغییرِ یک‌ساله)
هر قلم: قیمتِ فعلی، قیمتِ پارسال در همین تاریخ، و درصدِ تغییرِ یک‌ساله.
منبع: tgju از طریقِ Cloudflare Worker (RELAY_URL).
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

VERSION = "8.0 (6 items + yearly change)"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHANNEL_ID         = os.getenv("TELEGRAM_CHANNEL_ID", "@yourchannel").strip()
SIGNATURE          = os.getenv("SIGNATURE", "📊 @yourchannel | کانال نرخ بازار").strip()
RELAY_URL          = os.getenv("RELAY_URL", "").strip()

PERSIAN_DIGITS = os.getenv("PERSIAN_DIGITS", "0") not in ("0", "false", "False")
HTTP_TIMEOUT   = 35

PERSIAN_WEEKDAYS = ["شنبه", "یک‌شنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنج‌شنبه", "جمعه"]
PERSIAN_MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
                  "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]

# (کلید, ایموجی, نام, واحد, تومانی؟)  — ترتیب نمایش
ITEMS = [
    ("USD",    "💵", "دلار",     "تومان", True),
    ("GOLD18", "🪙", "گرم طلا",  "تومان", True),
    ("BTC",    "₿",  "بیت‌کوین", "دلار",  False),
]


def to_persian_digits(s):
    if not PERSIAN_DIGITS:
        return s
    return str(s).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def fmt_num(value, unit):
    if value is None:
        return None
    try:
        num = float(value)
    except (ValueError, TypeError):
        return None
    txt = "{:,}".format(int(round(num)))
    return f"{to_persian_digits(txt)} {unit}"


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
    if payload.get("missing"):
        print(f"[Relay] اقلامِ پیدانشده: {payload['missing']}")
    if payload.get("sample"):
        print(f"[Relay] نمونه‌ی سطرِ تاریخی: {str(payload['sample'])[:300]}")

    items = payload.get("items", {})
    result = {}
    for key, _, _, _, toman in ITEMS:
        info = items.get(key)
        if not info:
            continue
        cur = info.get("current")
        ya = info.get("yearAgo")
        try:
            cur = float(cur) if cur is not None else None
            ya = float(ya) if ya is not None else None
        except (ValueError, TypeError):
            cur, ya = None, None
        if toman:  # tgju ریال می‌دهد → تومان
            if cur is not None:
                cur /= 10
            if ya is not None:
                ya /= 10
        pct = None
        if cur is not None and ya not in (None, 0):
            pct = (cur - ya) / ya * 100
        result[key] = {"current": cur, "yearAgo": ya, "pct": pct,
                       "yearAgoDate": info.get("yearAgoDate")}
        print(f"[Relay] {key}: فعلی={cur} پارسال={ya} تغییر={pct}")
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


def change_sentence(pct):
    if pct is None:
        return "تغییرِ یک‌ساله نامشخص است"
    p = to_persian_digits("{:.1f}".format(abs(pct)))
    if pct > 0:
        return f"یعنی از پارسال تا الان {p}٪ افزایش داشته"
    if pct < 0:
        return f"یعنی از پارسال تا الان {p}٪ کاهش داشته"
    return "یعنی از پارسال تا الان تغییری نداشته"


def render_item(key, emoji, name, unit):
    return None  # placeholder (replaced below)


def build_message(prices):
    sep = "━━━━━━━━━━━━━━━━━"
    blocks = []
    for key, emoji, name, unit, _ in ITEMS:
        info = prices.get(key)
        if not info:
            continue
        cur = fmt_num(info.get("current"), unit)
        if cur is None:
            continue
        ya = fmt_num(info.get("yearAgo"), unit)
        ya_txt = ya if ya else "نامشخص"
        lines = [
            f"{emoji} <b>قیمت {name}: {cur}</b>",
            f"قیمت {name} در پارسال همین لحظه: {ya_txt}",
            f"<blockquote>{change_sentence(info.get('pct'))}</blockquote>",
        ]
        blocks.append("\n".join(lines))

    parts = [build_header(), sep]
    parts.append("\n\n".join(blocks))
    parts += [sep, "", html.escape(SIGNATURE)]
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
        print("داده‌ی کلیدی نیست؛ پست منتشر نمی‌شود.")
        sys.exit(1)
    message = build_message(prices)
    print("─── پیش‌نمایش ───")
    print(message)
    print("────────────────")
    send_message(message)


if __name__ == "__main__":
    main()
