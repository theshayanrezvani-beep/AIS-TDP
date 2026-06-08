# -*- coding: utf-8 -*-
"""
ربات قیمت بازار تلگرام — نرخ ارز، طلا و سکه
دو بار در روز (۱۲ ظهر و ۱۲ شب به وقت تهران) یک پیام تمیز توی کانال می‌گذارد.
اجرا روی GitHub Actions (رایگان، خارج از ایران، مقاوم به فیلترینگ).
منبع اصلی داده: BrsApi.ir (رایگان) — منبع پشتیبان: tgju.org
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


# ─────────────────────────── تنظیمات (از env خوانده می‌شود) ───────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHANNEL_ID         = os.getenv("TELEGRAM_CHANNEL_ID", "@yourchannel").strip()
SIGNATURE          = os.getenv("SIGNATURE", "📊 @yourchannel | کانال نرخ بازار").strip()

# کلید رایگان BrsApi — از brsapi.ir/free-api-gold-currency-webservice بگیرید
BRSAPI_KEY  = os.getenv("BRSAPI_KEY", "").strip()
BRSAPI_URL  = os.getenv("BRSAPI_URL", "https://BrsApi.ir/Api/Market/Gold_Currency.php")
TGJU_URL    = os.getenv("TGJU_URL", "https://call1.tgju.org/ajax.json")

PERSIAN_DIGITS = os.getenv("PERSIAN_DIGITS", "1") not in ("0", "false", "False")
HTTP_TIMEOUT   = 20
UA = {"User-Agent": "Mozilla/5.0 (price-bot; +https://github.com)"}

# اقلام درخواستی و ترتیب نمایش.  هر آیتم: (کلید نرمال, برچسب فارسی, واحد پیش‌فرض)
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

# نگاشت کلید نرمال → (نمادهای ممکن، بخشی از نام فارسی) برای پیدا کردن در پاسخ BrsApi
BRS_MATCH = {
    "USD":        (["USD"],                         ["دلار آمریکا", "دلار"]),
    "EUR":        (["EUR"],                          ["یورو"]),
    "AED":        (["AED"],                          ["درهم"]),
    "GBP":        (["GBP"],                          ["پوند"]),
    "TRY":        (["TRY"],                          ["لیر"]),
    "CNY":        (["CNY"],                          ["یوان"]),
    "USDT":       (["USDT"],                         ["تتر"]),
    "GOLD18":     (["IR_GOLD_18K", "18K", "GOLD18"], ["18 عیار", "۱۸ عیار", "طلای 18"]),
    "COIN_EMAMI": (["IR_COIN_EMAMI", "SEKE_EMAMI"],  ["امامی"]),
    "COIN_BAHAR": (["IR_COIN_BAHAR", "SEKE_BAHAR"],  ["بهار آزادی", "بهار"]),
    "OUNCE":      (["XAUUSD", "XAU", "ONS"],          ["انس طلا", "انس"]),
}

# کلیدهای احتمالی tgju (منبع پشتیبان)
TGJU_MATCH = {
    "USD":        ["price_dollar_rl"],
    "EUR":        ["price_eur"],
    "AED":        ["price_aed", "price_dirham_dubai"],
    "GBP":        ["price_gbp"],
    "TRY":        ["price_try"],
    "CNY":        ["price_cny"],
    "USDT":       ["crypto-tether-irr", "usdt"],
    "GOLD18":     ["geram18"],
    "COIN_EMAMI": ["sekee_emami", "emami"],
    "COIN_BAHAR": ["sekee", "sekeb"],
    "OUNCE":      ["ons"],
}


# ─────────────────────────── کمک‌تابع‌ها ───────────────────────────
def to_persian_digits(s):
    if not PERSIAN_DIGITS:
        return s
    table = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
    return str(s).translate(table)


def fmt_price(value, unit):
    """عدد را با جداکننده‌ی هزارگان و رقم فارسی برمی‌گرداند."""
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
    """درصد تغییر را به float برمی‌گرداند (یا None)."""
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
def _flatten_brs(data):
    """همه‌ی آیتم‌های لیست‌های تو‌در‌توی پاسخ BrsApi را در یک لیست جمع می‌کند."""
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
    """منبع اصلی. خروجی: dict[key] = {'price','pct','unit'} — ممکن است ناقص باشد."""
    if not BRSAPI_KEY:
        return {}
    try:
        r = requests.get(BRSAPI_URL, params={"key": BRSAPI_KEY},
                         headers=UA, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[BrsApi] خطا در دریافت: {e}")
        return {}

    items = _flatten_brs(data)
    if not items:
        print("[BrsApi] پاسخ خالی یا ساختار ناشناخته بود.")
        return {}

    result = {}
    for key, (symbols, names) in BRS_MATCH.items():
        found = None
        syms = [s.upper() for s in symbols]
        # اول با نماد (دقیق)
        for it in items:
            sym = str(it.get("symbol", "")).upper()
            if sym and sym in syms:
                found = it
                break
        # بعد با نام
        if found is None:
            for it in items:
                nm = str(it.get("name", ""))
                if any(n in nm for n in names):
                    found = it
                    break
        if found is None:
            continue
        price = found.get("price")
        pct = parse_change(found.get("change_percent", found.get("change_percentage")))
        unit = str(found.get("unit", "")).strip() or None
        result[key] = {"price": price, "pct": pct, "unit": unit}

    if result:
        print(f"[BrsApi] {len(result)} قلم دریافت شد.")
    return result


def fetch_tgju():
    """منبع پشتیبان (بدون کلید). دفاعی نوشته شده؛ هر قلمی که پیدا نشد رد می‌شود."""
    try:
        r = requests.get(TGJU_URL, headers=UA, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[tgju] خطا در دریافت: {e}")
        return {}

    current = data.get("current", data) if isinstance(data, dict) else {}
    result = {}
    for key, candidates in TGJU_MATCH.items():
        for c in candidates:
            node = current.get(c)
            if isinstance(node, dict) and node.get("p"):
                price = node.get("p")
                pct = parse_change(node.get("dp"))
                # جهت تغییر بر اساس فیلد dt (low/high)
                if pct is not None and str(node.get("dt", "")).lower() in ("low", "down"):
                    pct = -abs(pct)
                result[key] = {"price": price, "pct": pct, "unit": None}
                break
    if result:
        print(f"[tgju] {len(result)} قلم دریافت شد.")
    return result


def get_prices():
    """ترکیب منابع: اول BrsApi، خلأها را با tgju پر می‌کند."""
    prices = fetch_brsapi()
    needed = [k for k, *_ in CURRENCY_ITEMS + GOLD_ITEMS]
    if any(k not in prices for k in needed):
        backup = fetch_tgju()
        for k, v in backup.items():
            prices.setdefault(k, v)
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

    prices = get_prices()
    if not prices:
        print("هیچ داده‌ای از منابع گرفته نشد؛ برای جلوگیری از پست خالی، خارج می‌شویم.")
        sys.exit(1)

    # حداقل یکی از ارزهای اصلی باید موجود باشد تا پست منتشر شود
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
