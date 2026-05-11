print("START FILE")

import feedparser
import requests
import time
import datetime
import hashlib
import os
import random
import urllib.parse
import warnings
import io
from bs4 import XMLParsedAsHTMLWarning
from google import genai

# matplotlib headless mode — обов'язково до import pyplot
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import cot_reports as cot

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# =========================
# 🔑 CONFIG
# =========================
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GOOGLE_API_KEY = os.environ["GOOGLE_API_KEY"]
FINNHUB_API_KEY = os.environ["FINNHUB_API_KEY"]

low_priority_news = []
last_digest_time = time.time()
posted_news = set()
posted_events = set()

# pre_event_id -> {"check_at": ts, "retries": int}
# Точкова перевірка ForexFactory після події (для отримання Actual)
pending_actual_fetches = {}

DIGEST_HOURS = [9, 13, 14, 17, 21]  # Години для відправки
last_sent_hour = -1

# === COT (Commitments of Traders) ===
# display_name → (ticker для поста, pattern для матчингу у Market_and_Exchange_Names)
COT_MARKETS = {
    "GOLD":      ("XAUUSD", "GOLD - COMMODITY EXCHANGE"),
    "BITCOIN":   ("BTCUSD", "BITCOIN - CHICAGO MERCANTILE"),
    "EURO FX":   ("EURUSD", "EURO FX - CHICAGO MERCANTILE"),
    "S&P 500":   ("SPX",    "E-MINI S&P 500 - CHICAGO MERCANTILE"),
    "CRUDE OIL": ("WTI",    "CRUDE OIL, LIGHT SWEET"),
}
last_cot_release_date = None  # дата останнього вівторкового звіту, який ми вже опублікували


FALLBACK_IMAGE_URL = "https://images.unsplash.com/photo-1611974717482-98aa003745fc"


def send_photo_to_telegram(photo, caption, parse_mode="Markdown"):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    try:
        if isinstance(photo, (bytes, bytearray)):
            files = {"photo": ("image.png", photo, "image/png")}
            data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": caption,
            }
            if parse_mode:
                data["parse_mode"] = parse_mode
            response = requests.post(url, data=data, files=files, timeout=30)
        else:
            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "photo": photo,
                "caption": caption,
            }
            if parse_mode:
                payload["parse_mode"] = parse_mode
            response = requests.post(url, json=payload, timeout=30)
    except Exception as e:
        print(f"⚠️ Telegram sendPhoto exception: {e}")
        return False

    if not response.ok or not response.json().get("ok"):
        print(f"⚠️ Telegram sendPhoto failed: {response.status_code} {response.text[:300]}")
        return False
    return True

GEMINI_MODELS = [
    'gemini-2.5-flash-lite',  # дешевший first — економимо кредити
    'gemini-2.5-flash',
    'gemini-2.0-flash',
]

# Circuit breaker — час до якого Gemini вважається недоступним
gemini_blocked_until = 0


def call_gemini_ai(prompt):
    global gemini_blocked_until

    # Якщо кредити вичерпано — пропускаємо без виклику API (економимо час і гроші)
    if time.time() < gemini_blocked_until:
        remaining = int(gemini_blocked_until - time.time())
        print(f"⏭ Gemini заблокований ще {remaining}с (квота). Пропускаємо запит.")
        return "Не вдалося згенерувати аналітику ринку."

    client = genai.Client(api_key=GOOGLE_API_KEY)
    last_err = None
    for model in GEMINI_MODELS:
        for attempt in range(2):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                )
                return response.text
            except Exception as e:
                last_err = e
                msg = str(e)
                msg_lower = msg.lower()
                is_quota_depleted = (
                    "resource_exhausted" in msg_lower
                    or "credits are depleted" in msg_lower
                    or "prepayment" in msg_lower
                )
                is_rate_limit = "429" in msg and not is_quota_depleted
                transient = any(code in msg for code in ("503", "502", "504", "UNAVAILABLE")) or is_rate_limit
                not_found = "404" in msg
                print(f"AI Error [{model}] attempt {attempt+1}: {e}")

                # Кредити вичерпано — блокуємо звернення на 30 хв і виходимо одразу
                if is_quota_depleted:
                    gemini_blocked_until = time.time() + 1800
                    print("⛔ Gemini кредити вичерпано. Блокуємо звернення на 30 хв (запобіжник).")
                    return "Не вдалося згенерувати аналітику ринку."

                if not transient and not not_found:
                    print(f"AI Error final: {last_err}")
                    return "Не вдалося згенерувати аналітику ринку."
                if transient and attempt == 0:
                    wait_time = 15
                    print(f"Waiting {wait_time}s before retry...")
                    time.sleep(wait_time)
                    continue
                break  # перехід до наступної моделі
    print(f"AI Error final: {last_err}")
    return "Не вдалося згенерувати аналітику ринку."

def generate_ai_image(prompt):
    try:
        encoded = urllib.parse.quote(prompt)
        seed = random.randint(1, 10_000_000)
        url = (
            f"https://image.pollinations.ai/prompt/{encoded}"
            f"?width=1024&height=1024&nologo=true&seed={seed}"
        )
        print(f"🎨 Pollinations URL: {url}")
        r = requests.get(url, timeout=90)
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if not ctype.startswith("image"):
            print(f"⚠️ Pollinations повернув не картинку (content-type={ctype}), fallback")
            return FALLBACK_IMAGE_URL
        print(f"✅ Pollinations завантажив {len(r.content)} байт")
        return r.content
    except Exception as e:
        print(f"Помилка генерації зображення: {e}")
        return FALLBACK_IMAGE_URL

from bs4 import BeautifulSoup

def get_direction(actual, forecast):
    if not actual or not forecast:
        return "NEUTRAL"
    try:
        def clean(val):
            return float(val.replace('%', '').replace('K', '').replace('M', '').strip())
        
        a = clean(actual)
        f = clean(forecast)
        
        if a > f: return "UP"
        if a < f: return "DOWN"
        return "NEUTRAL"
    except:
        return "NEUTRAL"


FINNHUB_COUNTRY_TO_CURRENCY = {
    "US": "USD",
    "EU": "EUR",
    "GB": "GBP",
    "JP": "JPY",
    "CA": "CAD",
    "AU": "AUD",
}

# Для цих країн постимо тільки HIGH impact (а не Medium/Low)
FINNHUB_HIGH_ONLY_COUNTRIES = {"JP", "CA", "AU"}

FINNHUB_IMPACT_MAP = {"high": "High", "medium": "Medium", "low": "Low"}


def _fmt_finnhub_value(v, unit):
    """Перетворює числове значення Finnhub у строку з одиницею (як було в FF XML)."""
    if v is None:
        return ""
    if isinstance(v, float):
        s = f"{v:g}"  # 3.20 -> 3.2; 3.0 -> 3
    else:
        s = str(v)
    if unit:
        s = s + unit
    return s


def get_forexfactory_events():
    """Тягне економічний календар з Finnhub (free tier). Назва функції залишилась
    для сумісності з рештою коду — реальне джерело тепер Finnhub, не ForexFactory."""

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    from_date = (now_utc - datetime.timedelta(days=1)).date()  # вчора (на випадок ще "свіжих" Actual)
    to_date = (now_utc + datetime.timedelta(days=2)).date()  # +2 дні наперед

    url = (
        "https://finnhub.io/api/v1/calendar/economic"
        f"?from={from_date.isoformat()}&to={to_date.isoformat()}"
        f"&token={FINNHUB_API_KEY}"
    )

    try:
        response = requests.get(url, timeout=30)
    except Exception as e:
        print(f"❌ Finnhub fetch exception: {e}")
        return []

    if not response.ok:
        print(f"❌ Finnhub HTTP {response.status_code}: {response.text[:200]}")
        return []

    try:
        data = response.json()
    except Exception as e:
        print(f"❌ Finnhub JSON parse error: {e}")
        return []

    raw_events = data.get("economicCalendar", [])
    events = []

    for item in raw_events:
        country = item.get("country", "")
        currency = FINNHUB_COUNTRY_TO_CURRENCY.get(country)
        if not currency:
            continue  # скіпаємо країни які нас не цікавлять

        impact_raw = (item.get("impact") or "").lower()

        # Для JP/CA/AU пропускаємо все крім HIGH
        if country in FINNHUB_HIGH_ONLY_COUNTRIES and impact_raw != "high":
            continue

        impact = FINNHUB_IMPACT_MAP.get(impact_raw, "Low")

        time_str = item.get("time")
        if not time_str:
            continue
        try:
            event_time = datetime.datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
            event_time = event_time.replace(tzinfo=datetime.timezone.utc)
        except Exception as e:
            print(f"⚠️ Bad time format: {time_str} ({e})")
            continue

        unit = item.get("unit", "") or ""
        events.append({
            "title": item.get("event", "") or "",
            "currency": currency,
            "impact": impact,
            "time": event_time,
            "actual": _fmt_finnhub_value(item.get("actual"), unit),
            "forecast": _fmt_finnhub_value(item.get("estimate"), unit),
            "previous": _fmt_finnhub_value(item.get("prev"), unit),
        })

    print(f"📅 Finnhub: {len(raw_events)} подій всього, {len(events)} після фільтра (US/EU/GB всі; JP/CA/AU тільки HIGH)")
    return events

def send_low_priority_digest():
    global low_priority_news, last_digest_time
    
    print(f"DEBUG: Зайшли. У списку зараз: {len(low_priority_news)} новин")

    if len(low_priority_news) > 100:
        print(f"🧹 Забагато сміття ({len(low_priority_news)}). Очищуємо список...")
        low_priority_news = low_priority_news[-50:]
    
    if not low_priority_news:
        print("DEBUG: Новин реально немає")
        return False

    summary = "Не вдалося згенерувати аналітику ринку."
    market_mood = "Neutral"
    try:
        recent_news_list = low_priority_news[-30:]
        news_text = "\n".join(recent_news_list)
        prompt = (
            "Проаналізуй ці новини для трейдерів. Поверни відповідь СУВОРО в такому форматі (дві частини):\n"
            "MOOD: <одне слово: Bullish, Bearish або Neutral>\n"
            "SUMMARY: <стислий аналітичний підсумок українською, 3-5 речень, загальний фон для ринку>\n\n"
            f"Список новин:\n{news_text}"
        )

        print("DEBUG: Запит до ШІ...")
        ai_response = call_gemini_ai(prompt)
        print(f"DEBUG: ШІ відповів (перші 80): {ai_response[:80]}")

        for line in ai_response.splitlines():
            if line.strip().upper().startswith("MOOD:"):
                mood_val = line.split(":", 1)[1].strip().rstrip(".")
                if mood_val in ("Bullish", "Bearish", "Neutral"):
                    market_mood = mood_val
                    break
        if "SUMMARY:" in ai_response:
            summary = ai_response.split("SUMMARY:", 1)[1].strip()
        elif ai_response and not ai_response.startswith("Не вдалося"):
            summary = ai_response.strip()
    except Exception as e:
        print(f"❌ Помилка на етапі ШІ: {e}")

    if summary == "Не вдалося згенерувати аналітику ринку." or not summary.strip():
        print("⚠️ ШІ не видав результат. Скасовуємо пост, щоб не слати порожнє повідомлення.")
        return False  # Новини залишаються до наступного слоту

    if market_mood == "Bullish":
        image_prompt = (
    "cinematic shot, high-angle view of a modern trading desk at sunrise. "
    "Dark-mode mechanical keyboard glowing green, multiple curved monitors displaying sleek, "
    "hyper-detailed fluorescent green Japanese candlestick charts trending strongly UP. "
    "A matte black ceramic mug with a subtle, stylized charging Bull logo. "
    "In the blurred background through a large window, a vibrant cityscape twilight "
    "with rising sun rays. Soft, golden natural lighting, shallow depth of field, "
    "professional trading environment style, 8k resolution, photorealistic, highly detailed."
        )
    else:
        image_prompt = (
    "sleek futuristic financial terminal graphics, deep void-black background with subtle "
    "dark blue and gray geometric grid overlays. Intricate, detailed neon red candlestick charts "
    "trending DOWN, contrasted with smoothness index lines in vibrant electric green and deep purple. "
    "Close-up, focused perspective, technical abstract art style, dramatic sci-fi lighting, "
    "cyberpunk aesthetics, highly detailed UI elements, professional Bloomberg terminal aesthetic, "
    "8k resolution, sharp focus, octane render."
        )

    image_url = generate_ai_image(image_prompt)

    # Telegram sendPhoto caption limit = 1024 chars
    prefix = "📊 **DAILY MARKET SUMMARY (Low Impact)**\n\n"
    suffix = "\n\n#DailyDigest #MarketUpdate"
    budget = 1024 - len(prefix) - len(suffix) - 3
    if len(summary) > budget:
        summary = summary[:budget].rstrip() + "..."
    post_text = prefix + summary + suffix
    
    print("DEBUG: Намагаємось відправити в Телеграм...")

    telegram_ok = send_photo_to_telegram(image_url, post_text)

    if not telegram_ok:
        print("❌ Telegram не доставив дайджест. Новини залишаються до наступного слоту.")
        return False

    print("✅ Дайджест успішно відправлено!")

    # Очищуємо чернетку тільки після підтвердженої доставки
    low_priority_news = []
    last_digest_time = time.time()
    return True

# 🔥 SCENARIO ENGINE
def get_scenario(title):
    if "pmi" in title or "ism" in title:
        return """📈 If ABOVE forecast:
→ USD ↑
→ Indices ↓

📉 If BELOW forecast:
→ USD ↓
→ Indices ↑"""
    
    if "cpi" in title or "inflation" in title:
        return """📈 Higher inflation:
→ USD ↑
→ Gold ↓

📉 Lower inflation:
→ USD ↓
→ Gold ↑"""
    
    return "⚠️ No clear scenario"

# =========================
# 📰 RSS SOURCE (Reuters)
# =========================
RSS_URLS = [
    "https://feeds.bbci.co.uk/news/business/rss.xml",
    "https://www.fxstreet.com/rss/news",
    "https://www.marketwatch.com/rss/topstories",
    "https://www.scmp.com/rss/91/feed",
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://www.investing.com/rss/news_25.rss"
    
]

# =========================
# 🔍 KEYWORDS FILTER
# =========================
KEYWORDS = {

    # 🏦 МАКРО
    "macro": [
        "inflation", "cpi", "ppi",
        "interest rate", "rate hike", "rate cut",
        "fed", "ecb", "central bank", "fomc",
        "nfp", "payrolls", "unemployment",
        "pmi", "gdp"
    ],

    # 📊 РИНКИ
    "market": [
        "stocks", "shares", "equities",
        "market", "index", "indices",
        "s&p", "nasdaq", "dow", "nifty",
        "bond", "yield", "treasury",
        "rally", "selloff", "higher", "lower"
    ],

    # 🏢 КОМПАНІЇ
    "corporate": [
        "tesla", "apple", "amazon", "google", "nvidia", "microsoft",
        "earnings", "revenue", "profit", "loss",
        "guidance", "forecast", "results",
        "sales", "deliveries"
    ],

    # 🪙 КРИПТА
    "crypto": [
        "bitcoin", "btc",
        "ethereum", "eth",
        "solana", "sol",
        "crypto", "cryptocurrency",
        "etf", "binance", "coinbase"
    ],

    # 🛢 ЕНЕРГІЯ
    "energy": [
        "oil", "crude", "wti", "brent",
        "gas", "lng", "opec"
    ],

    # 🌍 ГЕОПОЛІТИКА
    "geopolitics": [
        "war", "conflict", "attack",
        "sanctions", "china", "ukraine", "russia",
        "iran", "israel", "trade war"
    ]
}

# =========================
# 📤 TELEGRAM SEND
# =========================
def send_to_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
    }
    requests.post(url, json=payload)

last_post_time = 0
last_medium_time = 0  # окремий лічильник для Medium 30-хв тротлінгу
recent_titles = []

# =========================
# 🔁 MAIN LOOP
# =========================
ASSET_IMPACT = {
    "macro": {
        "USD": "↑",
        "Gold": "↓",
        "Indices": "↓"
    },
    "economy": {
        "USD": "↑",
        "Gold": "↓",
        "Indices": "↓"
    },
    "market": {
        "Indices": "↑"
    },
    "energy": {
        "Oil": "↑"
    },
    "geopolitics": {
        "Gold": "↑",
        "Oil": "↑",
        "USD": "↑"
    }
}

SIGNAL_IMPACT = {
    "hawkish": {
        "USD": "↑",
        "Gold": "↓",
        "Indices": "↓"
    },
    "dovish": {
        "USD": "↓",
        "Gold": "↑",
        "Indices": "↑"
    },
    "risk_off": {
        "Gold": "↑",
        "USD": "↑",
        "Indices": "↓"
    },
    "risk_on": {
        "Indices": "↑",
        "USD": "↓"
    },
    "neutral": {}
}

SIGNAL_EMOJI = {
    "hawkish": "📈",
    "dovish": "📉",
    "risk_on": "🟢",
    "risk_off": "🔴",
    "neutral": "⚪"
}

ASSET_EMOJI = {
    "USD": "💵",
    "Gold": "🥇",
    "Oil": "🛢️",
    "Indices": "📊"
}

ARROW_EMOJI = {
    "↑": "🟢↑",
    "↓": "🔴↓"
}

HIGH_IMPACT = [
    "cpi", "inflation", "interest rate",
    "fed", "fomc", "nfp", "payrolls", "rate", "hike", "central bank", "cut"
]

MEDIUM_IMPACT = [
    "gdp", "pmi", "consumer",
    "economy", "retail", "manufacturing", "jobs"
]


# =========================
# 📊 COT REPORT
# =========================

def _fetch_cot_legacy_df():
    """Тягне Legacy COT за поточний + минулий рік (для 52-тижневого вікна)."""
    now_year = datetime.datetime.now(datetime.timezone.utc).year
    frames = []
    for year in (now_year - 1, now_year):
        try:
            df = cot.cot_year(year=year, cot_report_type="legacy_fut")
            if df is not None and len(df) > 0:
                frames.append(df)
        except Exception as e:
            print(f"⚠️ COT fetch year={year} failed: {e}")
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


def _find_col(df, hints):
    """Знайти колонку, ім'я якої містить всі підрядки з hints (case-insensitive)."""
    for col in df.columns:
        norm = col.lower().replace(" ", "_").replace("-", "_").replace("(", "").replace(")", "")
        if all(h in norm for h in hints):
            return col
    return None


def _extract_market(df, pattern):
    """Фільтрує DataFrame по шаблону назви ринку, повертає відсортовані за датою рядки.
    Виключає MICRO/E-MICRO варіанти (це окремі контракти з іншими номіналами)."""
    name_col = _find_col(df, ["market", "exchange", "names"])
    # Спершу шукаємо колонку з YYYY-MM-DD форматом, потім fallback на YYMMDD
    date_col = (
        _find_col(df, ["report_date", "yyyy"]) or
        _find_col(df, ["as_of_date", "yyyy"]) or
        _find_col(df, ["report_date"]) or
        _find_col(df, ["as_of_date"])
    )
    if not name_col or not date_col:
        print(f"⚠️ COT: не знайдено колонок (name={name_col}, date={date_col})")
        return None

    names = df[name_col].astype(str)
    mask = names.str.contains(pattern, case=False, na=False)
    # Виключаємо MICRO/E-MICRO варіанти — це окремі контракти
    mask &= ~names.str.contains(r"\bMICRO\b|\bE-MICRO\b", case=False, na=False, regex=True)
    sub = df[mask].copy()
    if sub.empty:
        return None

    # Парсимо дату захищено: спочатку як рядок (ISO), якщо більше половини NaN — пробуємо YYMMDD
    date_str = sub[date_col].astype(str)
    parsed = pd.to_datetime(date_str, errors="coerce")
    if parsed.isna().mean() > 0.5:
        parsed = pd.to_datetime(date_str, format="%y%m%d", errors="coerce")
    sub["_date"] = parsed
    sub = sub.dropna(subset=["_date"]).sort_values("_date").reset_index(drop=True)

    if not sub.empty:
        unique_markets = sub[name_col].unique()
        if len(unique_markets) > 1:
            print(f"⚠️ COT '{pattern}': матчиться {len(unique_markets)} ринків: {list(unique_markets)[:3]}")

    return sub


def _series_long_short(market_df):
    """Витягає колонки Noncommercial Long/Short з market_df як числові Series."""
    long_col = _find_col(market_df, ["noncommercial", "long", "all"])
    short_col = _find_col(market_df, ["noncommercial", "short", "all"])
    long_chg = _find_col(market_df, ["change", "noncommercial", "long"])
    short_chg = _find_col(market_df, ["change", "noncommercial", "short"])
    if not long_col or not short_col:
        return None

    longs = pd.to_numeric(market_df[long_col], errors="coerce")
    shorts = pd.to_numeric(market_df[short_col], errors="coerce")
    lc = pd.to_numeric(market_df[long_chg], errors="coerce") if long_chg else None
    sc = pd.to_numeric(market_df[short_chg], errors="coerce") if short_chg else None
    return {"long": longs, "short": shorts, "long_change": lc, "short_change": sc, "dates": market_df["_date"]}


def _sentiment_percentile(net_series, weeks=52):
    """Перцентиль поточного Net у вікні останніх N тижнів."""
    window = net_series.tail(weeks).dropna()
    if len(window) < 4:
        return None
    current = window.iloc[-1]
    rank = (window <= current).sum() / len(window)
    return float(rank) * 100.0


def _sentiment_label(pct):
    if pct is None:
        return "N/A"
    if pct >= 80:
        return f"🔥 Extreme Bullish (топ-{100-int(pct)}% за 52 тижні)"
    if pct >= 60:
        return "🟢 Bullish"
    if pct >= 40:
        return "⚪ Neutral"
    if pct >= 20:
        return "🔴 Bearish"
    return f"🧊 Extreme Bearish (низ-{int(pct)}% за 52 тижні)"


def _generate_cot_chart(dates, net_series, market_name):
    """Лінійний графік Net Position за останні 26 тижнів — повертає bytes (PNG)."""
    last_n = 26
    d = dates.tail(last_n).reset_index(drop=True)
    n = net_series.tail(last_n).reset_index(drop=True)

    fig, ax = plt.subplots(figsize=(10, 5), facecolor="#1a1a1a")
    ax.set_facecolor("#1a1a1a")

    # Колір лінії: зелений якщо net > 0, червоний якщо < 0 (за останнім значенням)
    line_color = "#00d4aa" if n.iloc[-1] >= 0 else "#ff5566"

    ax.plot(d, n, color=line_color, linewidth=2.2, marker="o", markersize=4)
    ax.fill_between(d, n, alpha=0.15, color=line_color)
    ax.axhline(0, color="#888", linewidth=0.8, linestyle="--")

    ax.set_title(f"{market_name} — Non-Commercial Net Position (26 тижнів)",
                 color="white", fontsize=14, pad=15)
    ax.tick_params(colors="#cccccc")
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("bottom", "left"):
        ax.spines[spine].set_color("#666666")
    ax.grid(True, alpha=0.15, color="#666666")
    fig.autofmt_xdate()
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=100, facecolor="#1a1a1a")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def _sanitize_ai_text(text, max_chars=350):
    """Прибирає Markdown-шум (**, *, заголовки, bullet'и) і обрізає до max_chars.
    Telegram parse_mode=None спрощує життя — але робимо текст компактним."""
    if not text:
        return ""
    cleaned = text.replace("**", "").replace("__", "")
    lines = []
    for line in cleaned.split("\n"):
        stripped = line.lstrip("*#-•— ").strip()
        if stripped:
            lines.append(stripped)
    cleaned = " ".join(lines).strip()
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rstrip() + "..."
    return cleaned


def _build_cot_post(market_name, ticker, series, sentiment_pct, ai_comment, report_date):
    longs = int(series["long"].iloc[-1]) if pd.notna(series["long"].iloc[-1]) else 0
    shorts = int(series["short"].iloc[-1]) if pd.notna(series["short"].iloc[-1]) else 0
    net = longs - shorts

    if series["long_change"] is not None and pd.notna(series["long_change"].iloc[-1]):
        lc = int(series["long_change"].iloc[-1])
        lc_str = f"({lc:+,})"
    else:
        lc_str = ""
    if series["short_change"] is not None and pd.notna(series["short_change"].iloc[-1]):
        sc = int(series["short_change"].iloc[-1])
        sc_str = f"({sc:+,})"
    else:
        sc_str = ""

    sent_line = ""
    if sentiment_pct is not None:
        sent_line = f"🌡 Sentiment: {sentiment_pct:.0f}% — {_sentiment_label(sentiment_pct)}\n"

    ai_line = f"\n🗣 {ai_comment}\n" if ai_comment else ""

    # Без Markdown — використовуємо plain text для caption (parse_mode=None у виклику)
    return (
        f"📊 COT Report: {market_name} ({ticker})\n"
        f"Тиждень: {report_date.strftime('%Y-%m-%d')}\n\n"
        f"🟢 Longs:  {longs:,} {lc_str}\n"
        f"🔴 Shorts: {shorts:,} {sc_str}\n"
        f"⚖️ Net:    {net:+,}\n"
        f"{sent_line}"
        f"{ai_line}"
        f"\n#COT #{ticker}"
    )


def _cot_ai_commentary(market_name, ticker, series, sentiment_pct):
    """AI коментар від Gemini. Повертає '' якщо ШІ заблокований/упав."""
    longs = int(series["long"].iloc[-1])
    shorts = int(series["short"].iloc[-1])
    net = longs - shorts

    # Зміна тижневого нет
    if len(series["long"]) >= 2:
        prev_net = int(series["long"].iloc[-2] - series["short"].iloc[-2])
        net_change = net - prev_net
    else:
        net_change = 0

    prompt = (
        f"COT звіт для {market_name} ({ticker}):\n"
        f"- Longs: {longs:,}\n"
        f"- Shorts: {shorts:,}\n"
        f"- Net Position: {net:+,} (зміна {net_change:+,} за тиждень)\n"
        f"- Sentiment percentile: {sentiment_pct:.0f}% (100 = історичний максимум long-позицій за 52 тижні)\n\n"
        "Напиши РОВНО 2 короткі речення українською — суть для трейдера. "
        "БЕЗ заголовків, БЕЗ markdown зірочок, БЕЗ списків/bullet-points, "
        "БЕЗ слів 'Аналіз', 'Висновок', 'Що це означає'. "
        "Тільки 2 речення прямим текстом: куди дивляться спекулянти і чи близько до екстремуму."
    )
    result = call_gemini_ai(prompt)
    if not result or "Не вдалося" in result:
        return ""
    return _sanitize_ai_text(result)


def post_cot_reports():
    """Раз на тиждень (п'ятниця після 21:00 UTC) тягне Legacy COT і постить N ринків.

    Для одноразового тесту — встав env COT_TEST_NOW=1 у Railway, перезапусти,
    дочекайся посту і прибери змінну (інакше при кожному рестарті бот постить COT)."""
    global last_cot_release_date

    now = datetime.datetime.now(datetime.timezone.utc)
    force = os.environ.get("COT_TEST_NOW", "").lower() in ("1", "true", "yes")

    # П'ятниця = 4 (Monday=0). CFTC викладає звіт о ~20:30 UTC. Запас — постимо після 21:00 UTC.
    if not force and (now.weekday() != 4 or now.hour < 21):
        return False
    if force:
        print("⚙️ COT_TEST_NOW=1 — пропускаємо guard, постимо одразу")

    print("📊 Тягнемо COT Report з CFTC...")
    df = _fetch_cot_legacy_df()
    if df is None or df.empty:
        print("❌ COT: порожні дані, спробуємо наступного циклу")
        return False

    # Беремо найсвіжішу дату звіту — щоб не дублювати, якщо вже постили
    name_col = _find_col(df, ["market", "exchange", "names"])
    date_col = _find_col(df, ["as_of_date"]) or _find_col(df, ["report_date"])
    if not date_col:
        print("❌ COT: не знайдено колонку дати")
        return False
    latest = pd.to_datetime(df[date_col], errors="coerce", format="mixed").max().date()

    if last_cot_release_date == latest:
        print(f"COT: звіт за {latest} вже опублікований, скіп")
        return False

    print(f"📊 Найсвіжіший звіт за {latest}, постимо {len(COT_MARKETS)} ринків")

    for i, (display_name, (ticker, pattern)) in enumerate(COT_MARKETS.items()):
        try:
            market_df = _extract_market(df, pattern)
            if market_df is None or market_df.empty:
                print(f"⚠️ COT {display_name}: не знайдено даних для шаблону '{pattern}'")
                continue

            series = _series_long_short(market_df)
            if series is None:
                print(f"⚠️ COT {display_name}: бракує колонок long/short")
                continue

            net_series = series["long"] - series["short"]
            sentiment_pct = _sentiment_percentile(net_series)
            ai_comment = _cot_ai_commentary(display_name, ticker, series, sentiment_pct or 50)

            chart = _generate_cot_chart(series["dates"], net_series, display_name)
            report_date = series["dates"].iloc[-1]
            post = _build_cot_post(display_name, ticker, series, sentiment_pct, ai_comment, report_date)

            # parse_mode=None — плоский текст, бо AI може поламати Markdown
            sent = send_photo_to_telegram(chart, post, parse_mode=None)
            if sent:
                print(f"✅ COT posted: {display_name} (Net={int(net_series.iloc[-1]):,}, date={report_date.strftime('%Y-%m-%d')})")
            else:
                # Фолбек — текст без графіка
                send_to_telegram(post)
                print(f"⚠️ COT {display_name}: фото не доставлено, відправили текст")

            # 1 хв пауза між постами (крім останнього)
            if i < len(COT_MARKETS) - 1:
                time.sleep(60)

        except Exception as e:
            print(f"❌ COT {display_name} error: {e}")
            continue

    last_cot_release_date = latest
    return True


def main():
    global last_post_time, last_medium_time, low_priority_news, last_digest_time, posted_news, posted_events, last_sent_hour, pending_actual_fetches, last_cot_release_date

    last_update = 0
    events = []
    while True:

        now_ts = time.time()
        now_utc = datetime.datetime.now(datetime.timezone.utc)

        # 🟢 1. FOREX FACTORY (CALENDAR)
        # Оновлюємо у двох випадках:
        #   1) Раз на 15 хв — для свіжого календаря майбутніх подій
        #   2) Точкова перевірка після події — коли наступила запланована pending_actual_fetches[id]["check_at"]
        due_pre_ids = [pid for pid, info in pending_actual_fetches.items() if now_ts >= info["check_at"]]

        if now_ts - last_update > 900 or due_pre_ids:
            if due_pre_ids:
                print(f"🔄 Точковий фетч календаря (за Actual для {len(due_pre_ids)} подій)")
            events = get_forexfactory_events()
            last_update = now_ts

        for event in events:
            scenario = ""
            title = event["title"]
            currency = event["currency"]
            impact = event["impact"]
            actual = event.get("actual", "").strip()
            forecast = event.get("forecast", "").strip()
            previous = event.get("previous", "").strip()
            event_time = event["time"]
            now = datetime.datetime.now(datetime.timezone.utc)
            minutes_to_event = (event_time - now).total_seconds() / 60

            if currency not in ["USD", "EUR", "GBP", "XAU", "BTC", "ETH", "OIL"]:
                continue
            if impact.lower() == "low":
                continue
            if minutes_to_event > 120: # Не чіпаємо новини, що будуть через 2 години+
                continue

            title_lower = title.lower()
            if any(word in title_lower for word in ["cpi", "pce", "inflation"]):
                if currency == "USD":
                    scenario = "↑ Strong inflation → USD ↑ / Gold ↓\n↓ Weak inflation → USD ↓ / Gold ↑"
                else:
                    scenario = f"↑ Strong inflation → {currency} ↑\n↓ Weak inflation → {currency} ↓"
            elif "nfp" in title_lower or "employment" in title_lower:
                scenario = f"↑ Strong jobs → {currency} ↑\n↓ Weak jobs → {currency} ↓"
            else:
                scenario = "High volatility expected. Follow the data."

            # --- 3. 🔥 PRE-NEWS ЛОГІКА (Анонс за 5 хв) ---
            if 0 < minutes_to_event <= 5:
                event_id = (title + currency + impact + "_PRE").strip()
                if event_id not in posted_events:
                    post = f"⏳ Upcoming Event ({int(minutes_to_event)} min)\n\nEvent: {title.upper()}\nCurrency: {currency}\nImpact: {impact.upper()}\n\n🧠 Scenarios:\n{scenario}"
                    send_to_telegram(post)
                    posted_events.add(event_id)
                    # Плануємо точковий фетч на T+4 хв після події (для отримання Actual)
                    is_speech = "speak" in title.lower() or "testif" in title.lower()
                    if not is_speech:
                        pending_actual_fetches[event_id] = {
                            "check_at": event_time.timestamp() + 240,
                            "retries": 0,
                        }
                    print(f"⏳ Sent PRE event: {title} (fact check at +4min)")
                continue

            # --- 4. 🔥 MAIN NEWS ЛОГІКА (Момент виходу) ---
            # Перевіряємо в діапазоні від -20 хв до +2 хв
            if -20 <= minutes_to_event <= 2:
                is_speech = "speak" in title.lower() or "testif" in title.lower()
                
                # КРИТИЧНО: Якщо немає Actual і це не виступ — чекаємо наступного циклу
                if not actual and not is_speech:
                    continue 

                # Унікальний ID саме для посту з цифрами
                event_id = (title + currency + impact + "_MAIN_" + actual).strip()
                if event_id in posted_events:
                    continue

                # Логіка аналізу цифр
                if is_speech:
                    result = "🎙 SPEECH / TESTIMONY"
                    move = "⚖️ Watch live for market sentiment"
                else:
                    direction = get_direction(actual, forecast)
                    if direction == "UP":
                        result = "📈 ABOVE FORECAST"
                        move = "📈 USD ↑ / Gold ↓" if currency == "USD" else f"📈 {currency} ↑"
                    elif direction == "DOWN":
                        result = "📉 BELOW FORECAST"
                        move = "📉 USD ↓ / Gold ↑" if currency == "USD" else f"📉 {currency} ↓"
                    else:
                        result = "📊 IN LINE"
                        move = "⚖️ No strong move"

                post = f"🚨 Economic Release\n\nEvent: {title.upper()}\nCurrency: {currency}\n\nActual: {actual}\nForecast: {forecast}\nPrevious: {previous}\n\n{result}\n\n{move}"
                send_to_telegram(post)
                posted_events.add(event_id)
                # Прибираємо з черги — Actual отримали і опублікували
                pre_id = (title + currency + impact + "_PRE").strip()
                pending_actual_fetches.pop(pre_id, None)
                print("📅 Sent MAIN event:", title)

        # 🧹 Догляд за чергою точкових фетчів:
        #   - Якщо перевірка пройшла, але Actual ще не з'явився → 1 retry через 6 хв (~T+10)
        #   - Якщо retry вже зроблено або подія старша 30 хв → видаляємо
        for pid in list(pending_actual_fetches.keys()):
            info = pending_actual_fetches[pid]
            if now_ts >= info["check_at"]:
                if info["retries"] < 1:
                    info["retries"] += 1
                    info["check_at"] = now_ts + 360  # ще одна спроба за 6 хв
                    print(f"⏳ Actual ще не з'явився для {pid[:40]}... retry за 6 хв")
                else:
                    print(f"❌ Здаємось на Actual для {pid[:40]}")
                    del pending_actual_fetches[pid]

        # =========================
        # 🔵 2. RSS NEWS
        # =========================
        for url in RSS_URLS:
            
            response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
            feed = feedparser.parse(response.content)
            
            for entry in feed.entries[:3]:

                category = "other"
                impact = "LOW"

                clean_title = BeautifulSoup(entry.title, "html.parser").get_text()
                title = clean_title.lower()

                # 🔥 CATEGORY
                category = None

                for key, words in KEYWORDS.items():
                    if any(word in title for word in words):
                        category = key
                        break

                if category is None:
                    category = "other"
                
                if category == "other":
                    if any(word in title for word in ["tesla", "apple", "amazon"]):
                        category = "corporate"

                # 🔥 IMPACT
                title_up = title.upper()
        
                if any(word in title_up for word in ["FED", "RATE", "CPI", "INFLATION", "FOMC", "URGENT", "BREAKING"]):
                    impact = "HIGH"
            
                elif any(word in title_up for word in ["MARKET", "BANK", "REPORT", "ECONOMY", "GROWTH", "JOB", "OUTLOOK", "STOCKS", "ANALYSIS"]):
                    impact = "MEDIUM"
            
                else:
                    impact = "LOW"

                # 🔍 НОВИЙ БЛОК: ФІЛЬТР ПО КЛЮЧОВИМ СЛОВАМ 

                keywords = [
                    "inflation", "cpi", "fed", "interest rate", "powell",
                    "recession", "gdp", "jobs", "nfp", "earning", "revenue", "guidance",
                    "ecb", "boe", "central bank", "pce", "yield", "auction", 
                    "oil", "opec", "war", "ppi", "core ppi", "wholesale inflation",
                    "btc", "eth", "xau", "usd", "eur", "gbp", "meeting", "statement", "decision", "press conference",
                    "market", "analysis", "price", "crypto", "stock", "update", "forecast"
            ]
                
                is_relevant = any(word in title for word in keywords)
                if not is_relevant and impact != "HIGH":
                    low_priority_news.append(f"⚪️ {clean_title}")
                    continue

                # 🚫 АНТИ-ДУБЛІКАТИ
                news_id = hashlib.md5(title.encode()).hexdigest()

                raw_summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
                has_actual = "Actual" in raw_summary or "actual" in raw_summary.lower()

                if news_id in posted_news:
                    # Якщо ми вже постили цей ID, але зараз з'явився Actual — даємо шанс
                    if has_actual:
                        actual_id = f"{news_id}_actual"
                        # Перевіряємо, чи ми вже постили цей конкретний Actual
                        if actual_id in posted_news:
                            continue 
                        else:
                            # Це нові дані! Міняємо ID на 'actual' версію і йдемо далі
                            news_id = actual_id
                    else:
                        # Це звичайний дублікат без нових цифр — скипаємо
                        continue
                
                # 🔥 СИГНАЛ
                signal_score = 0

                # 🔴 risk_off
                if any(word in title for word in [
                    "war", "conflict", "attack", "sanctions", "crisis", "recession"
                ]):
                    signal_score -= 2

                # 🟢 risk_on
                if any(word in title for word in [
                    "growth", "earnings", "revenue", "profit", "stocks higher", "rally"
                ]):
                    signal_score += 2

                # 🏦 hawkish
                if any(word in title for word in [
                    "inflation", "cpi", "rate hike"
                ]):
                    signal_score += 1

                # 🕊 dovish
                if any(word in title for word in [
                    "rate cut", "stimulus"
                ]):
                    signal_score -= 1

                # 🪙 CRYPTO SIGNALS
                if "bitcoin" in title or "btc" in title:
                    signal_score += 1

                if "etf" in title and "bitcoin" in title:
                    signal_score += 2

                if signal_score >= 2:
                    signal = "risk_on"
                elif signal_score <= -2:
                    signal = "risk_off"
                elif signal_score == 1:
                    signal = "hawkish"
                elif signal_score == -1:
                    signal = "dovish"
                else:
                    signal = "neutral"

                # 🔥 CONFIDENCE
                confidence = 50  # база

                if signal in ["hawkish", "dovish"]: confidence += 20
                if impact == "HIGH": confidence += 25
                elif impact == "MEDIUM": confidence += 15

                confidence += abs(signal_score) * 5

                if signal_score >= 3:
                    confidence += 5

                strong_words = ["inflation", "cpi", "fed", "rate", "war", "crisis"]
                if any(word in title for word in strong_words):
                    confidence += 10

                # 🔥 CONFIDENCE LABEL (для відображення в пості)
                if confidence >= 80:
                    confidence_label = "🔥 STRONG"
                elif confidence >= 65:
                    confidence_label = "⚡ MEDIUM"
                else:
                    confidence_label = "⚪ WEAK"

                clean_summary = BeautifulSoup(raw_summary, "html.parser").get_text()
                clean_summary = clean_summary.split("http")[0]

                news_text = clean_title + ". " + clean_summary[:150]
                post_text = news_text

                # ⏱ TIME CONTROL — окремий лічильник для Medium
                current_time = time.time()
                time_since_medium = current_time - last_medium_time

                # 🎯 РОЗПОДІЛ ПО IMPACT (без врахування confidence)
                if impact == "LOW":
                    low_priority_news.append(f"🔹 {clean_title}")
                    posted_news.add(news_id)
                    continue

                if impact == "MEDIUM":
                    # 30-хв тротлінг для MEDIUM (свій лічильник, не блокує HIGH)
                    if time_since_medium < 1800:
                        low_priority_news.append(f"🟡 {clean_title}")
                        posted_news.add(news_id)
                        print(f"Medium throttled (last Medium {int(time_since_medium)}s ago) → digest")
                        continue
                    print(f"Medium allowed (last Medium {int(time_since_medium)}s ago)")

                # Сюди потрапляють тільки HIGH і дозволений MEDIUM

                # 🧠 AI ПЕРЕКЛАД — тільки для HIGH
                summary_ua = ""
                if impact == "HIGH":
                    try:
                        ai_prompt = (
                            f"Analyze this financial news: {post_text}\n"
                            "Provide a very short summary (1 sentence) in Ukrainian explaining the core essence for traders."
                            "Return ONLY the Ukrainian sentence."
                        )
                        summary_ua = call_gemini_ai(ai_prompt)
                        if not summary_ua or "Не вдалося" in summary_ua:
                            summary_ua = ""
                    except Exception as e:
                        print(f"AI translation error: {e}")
                        summary_ua = ""

                # Активи та іконки
                assets = SIGNAL_IMPACT.get(signal, {})
                assets_text = " | ".join([
                    f"{ASSET_EMOJI.get(k, '')} {k} {ARROW_EMOJI.get(v, v)}"
                    for k, v in assets.items()
                ])

                if not assets_text:
                    assets_text = "No clear signal"

                signal_icon = SIGNAL_EMOJI.get(signal, "")
                confidence = min(confidence, 100)

                # UA-секція тільки для HIGH (і тільки якщо ШІ зміг)
                ua_section = f"\n🗣 {summary_ua}\n" if summary_ua else ""

                display_impact = "🔴 HIGH" if impact == "HIGH" else "🟡 MEDIUM"

                post = f"""🚨 **Macro Update**

Signal: {signal_icon} {signal.upper()} ({confidence}% {confidence_label})
Impact: {display_impact}

Category: {category.upper()}

{post_text}
{ua_section}
Assets:
{assets_text}
"""

                # Дедуплікація за заголовком тільки для MEDIUM (HIGH завжди йде)
                if impact == "MEDIUM" and any(title[:50] in t for t in recent_titles):
                    continue

                try:
                    if impact == "HIGH":
                        # HIGH → з картинкою
                        image_prompt = (
                            "cinematic 3D render of a financial news flash, sleek dark trading terminal, "
                            "glowing red and gold accent lighting, urgent breaking-news atmosphere, "
                            "abstract candlestick chart in background, professional cyberpunk aesthetic, "
                            "8k resolution, photorealistic, sharp focus."
                        )
                        image = generate_ai_image(image_prompt)
                        sent = send_photo_to_telegram(image, post)
                        if not sent:
                            # Як фолбек — текст без картинки
                            send_to_telegram(post)
                    else:
                        # MEDIUM → текст
                        send_to_telegram(post)
                        last_medium_time = time.time()

                    last_post_time = time.time()
                    posted_news.add(news_id)
                    recent_titles.append(title.lower())

                    if len(recent_titles) > 20:
                        recent_titles.pop(0)

                    print(f"✅ Posted [{impact}]:", title)

                except Exception as e:
                    print("Error:", e)

        # === ДАЙДЖЕСТ (поза циклом RSS, раз на ітерацію main) ===
        current_time_dt = datetime.datetime.now()
        current_hour = current_time_dt.hour

        if current_hour in DIGEST_HOURS and current_hour != last_sent_hour:
            if len(low_priority_news) >= 10:
                print(f"⏰ Час дайджесту ({current_hour}:00)! Новин: {len(low_priority_news)}")

                success = send_low_priority_digest()

                if success:
                    low_priority_news.clear()
                    last_sent_hour = current_hour
                    print("DEBUG: Дайджест відправлено, список очищено.")
                else:
                    print(f"⚠️ Дайджест НЕ відправлено (ШІ/Telegram не відповів). Новини збережено до наступного слоту.")
            else:
                print(f"⏳ Час {current_hour}:00 підійшов, але новин мало ({len(low_priority_news)}/10). Чекаємо.")

        # === COT REPORT (раз на тиждень, п'ятниця ≥21:00 UTC) ===
        try:
            post_cot_reports()
        except Exception as e:
            print(f"❌ COT report top-level error: {e}")

        # ⏸ Не спінити CPU — чекаємо хвилину перед наступним циклом
        print("⏸ Sleeping 60s before next cycle...")
        time.sleep(60)

if __name__ == "__main__":
    while True:
        try:
            main()
        except Exception as e:
            print(f"❌ Main crashed: {e}. Restarting in 60s")
            time.sleep(60)
