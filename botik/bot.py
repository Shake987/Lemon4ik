print("START FILE")

import feedparser
import requests
import time
import datetime
import hashlib
import os
import warnings
from bs4 import XMLParsedAsHTMLWarning
import google.generativeai as genai
from google.generativeai.types import RequestOptions
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# =========================
# 🔑 CONFIG
# =========================
TELEGRAM_BOT_TOKEN = "8789135346:AAFaM57p_BN7KsZ0IeQFVVgzKGUJTa4gJL8"
TELEGRAM_CHAT_ID = "467700442"
GOOGLE_API_KEY = "AIzaSyAG8vfRs4UyMLyyRB3_-EEm1C62BwHohEg"

low_priority_news = []
last_digest_time = time.time()
posted_news = set()
posted_events = set()


def send_photo_to_telegram(photo_url, caption):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "photo": photo_url,
        "caption": caption,
        "parse_mode": "Markdown"
    }
    response = requests.post(url, json=payload)
    return response

def call_gemini_ai(prompt):
    try:
        genai.configure(api_key="AIzaSyAG8vfRs4UyMLyyRB3_-EEm1C62BwHohEg")
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content
            (prompt,
            request_options=RequestOptions(api_version='v1')
        )
        return response.text
    except Exception as e:
        print(f"AI Error: {e}")
        return "Не вдалося згенерувати аналітику ринку."

def generate_ai_image(prompt):
    try:
        # Використовуємо налаштовану модель для генерації
        model = genai.GenerativeModel('image-generation-002')
        
        # Додаємо контекст для Nano Banana 2, щоб картинка була професійною
        full_prompt = f"Professional financial news cover, cinematic trading environment, {prompt}, 8k resolution, high quality"
        
        # Виклик генерації (повертає об'єкт з посиланням на зображення)
        response = model.generate_content(full_prompt)
        
        # Припускаємо, що API повертає URL у відповіді
        # Якщо твоя бібліотека підтримує пряму генерацію посилання:
        if response.text:
            # Тут логіка отримання URL залежить від конкретного API Nano Banana 
            # Зазвичай це виглядає як повернення згенерованого посилання
            return response.text 
            
    except Exception as e:
        print(f"⚠️ Помилка генерації зображення: {e}")
        # Залишаємо Unsplash як запасний варіант (fallback)
        return "https://images.unsplash.com/photo-1611974717482-98aa003745fc"

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


def get_forexfactory_events():
    url = f"https://nfs.faireconomy.media/ff_calendar_thisweek.xml?v={int(time.time())}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/xml, text/xml, */*",
        "Accept-Language": "en-US,en;q=0.9"
    }

    for i in range(3):
        response = requests.get(url, headers=headers)

        text = response.text.lower()

        if "rate limited" not in text and "just a moment" not in text:
            break

        print(f"🔁 Retry {i+1}")
        time.sleep(2)

    if "rate limited" in text or "just a moment" in text:
        print("❌ BLOCKED AFTER RETRIES")
        return []

    # 🔍 DEBUG
    print(response.text[:500])

    soup = BeautifulSoup(response.content, "xml")

    events = []

    for item in soup.find_all("event"):
        try:
            title = item.find("title").text
            currency = item.find("country").text
            impact = item.find("impact").text

            actual_node = item.find("actual")
            forecast_node = item.find("forecast")
            previous_node = item.find("previous")

            actual = actual_node.text if actual_node else ""
            forecast = forecast_node.text if forecast_node else ""
            previous = previous_node.text if previous_node else ""

            date = item.find("date").text
            time_ = item.find("time").text

            # 🧠 PARSE DATETIME
            import datetime

            dt_str = f"{date} {time_}"
            event_time = datetime.datetime.strptime(dt_str, "%m-%d-%Y %I:%M%p")
            event_time = event_time.replace(tzinfo=datetime.timezone.utc)

            events.append({
                "title": title,
                "currency": currency,
                "impact": impact,
                "time": event_time,
                "actual": actual,
                "forecast": forecast,
                "previous": previous
            })

        except Exception as e:
            print("⚠️ SKIPPED EVENT:", e)
            continue

    return events

def send_low_priority_digest():
    global low_priority_news, last_digest_time
    
    print(f"DEBUG: Зайшли. У списку зараз: {len(low_priority_news)} новин")
    
    if not low_priority_news:
        print("DEBUG: Новин реально немає")
        return

    try:
        news_text = "\n".join(low_priority_news)
        prompt = f"Зроби стислий аналітичний підсумок цих новин для трейдерів. Який загальний фон вони створюють? Список новин:\n{news_text}"
    
        print("DEBUG: Запит до ШІ...") # КРОК 2
        summary = call_gemini_ai(prompt)
        print(f"DEBUG: ШІ відповів (перші 20 символів): {summary[:20]}") # КРОК 3
    
        mood_prompt = f"Зроби стислий аналіз фону (Bullish, Bearish чи Neutral) для цих новин. Дай відповідь одним словом. Новини: {summary[:100]}"
        market_mood = call_gemini_ai(mood_prompt).strip() # Наприклад: Bullish

    except Exception as e:
        print(f"❌ Помилка на етапі ШІ: {e}")
        market_mood = "Neutral"
    
    if market_mood == "Bullish":
        image_prompt = "modern minimalist wood desk with dark-mode MacBook display showing green abstract bar charts, ceramic mug with Bull icon, cityscape twilight background, soft natural lighting"
    else:
        image_prompt = "sleek dark-mode financial terminal graphics with deep blues and grays, vibrant neon green and red candlestick and smoothness index lines, professional trading style"

    # 3. Викликаємо реальну генерацію картинки через Nano Banana 2
    image_url = generate_ai_image(image_prompt)

    post_text = f"📊 **DAILY MARKET SUMMARY (Low Impact)**\n\n{summary}\n\n#DailyDigest #MarketUpdate"
    
    print("DEBUG: Намагаємось відправити в Телеграм...") # КРОК 4
    
    # Відправляємо в Телеграм (фото + текст)
    try:
        # Спробуємо відправити через твою функцію
        send_photo_to_telegram(image_url, post_text)
        print("✅ Дайджест успішно відправлено!")
    except Exception as e:
        # Цей блок "спіймає" помилку і виведе її в логи Railway
        print(f"❌ ПОМИЛКА ТЕЛЕГРАМУ: {e}")
        try:
            # Спробуємо відправити хоча б текст без картинки
            # send_message_to_telegram(post_text) # якщо така функція є
            print("⚠️ Спроба відправки тексту без фото...")
        except:
            pass
    
    # Очищуємо чернетку
    low_priority_news = []
    last_digest_time = time.time()

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

# =========================
# 🧠 AI GENERATION
# =========================
def generate_post(news_text):
    prompt = f"""
Ти фінансовий аналітик.

Стисло проаналізуй новину і дай формат:

🚨 Macro Update

Факт:
...

Що це означає:
...

Активи:
USD (↑/↓/~)
Gold (↑/↓/~)
Oil (↑/↓/~)
Indices (↑/↓/~)
Crypto (↑/↓/~)

Новина:
{news_text}
"""

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3
    )

    return response.choices[0].message.content

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

def main():
    global low_priority_news, last_digest_time, posted_news, posted_events
    
    recent_titles = []
    last_post_time = 0
    last_update = 0
    events = []
    while True:
        
        now_ts = time.time()
    
        # 🟢 1. FOREX FACTORY (CALENDAR)
        
        # 🔄 оновлення раз на 15 хв
        if now_ts - last_update > 900 or any(
            0 < (e["time"] - datetime.datetime.now(datetime.timezone.utc)).total_seconds()/60 < 3
            for e in events
        ):
            events = get_forexfactory_events()
            last_update = now_ts
        else:
            pass

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
                    print("⏳ Sent PRE event:", title)
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
                print("📅 Sent MAIN event:", title)   
            
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
                    impact = "🔴 HIGH"
            
                elif any(word in title_up for word in ["MARKET", "BANK", "REPORT", "ECONOMY", "GROWTH", "JOB", "OUTLOOK", "STOCKS", "ANALYSIS"]):
                    impact = "🟡 MEDIUM"
            
                else:
                    impact = "🟢 LOW"

                # 🔍 НОВИЙ БЛОК: ФІЛЬТР ПО КЛЮЧОВИМ СЛОВАМ 

                keywords = [
                    "inflation", "cpi", "fed", "interest rate", "powell",
                    "recession", "gdp", "jobs", "nfp", "earning", "revenue", "guidance",
                    "ecb", "boe", "central bank", "pce", "yield", "auction", 
                    "oil", "opec", "war", "ppi", "core ppi", "wholesale inflation",
                    "btc", "eth", "xau", "usd", "eur", "gbp" "meeting", "statement", "decision", "press conference",
                    "market", "analysis", "price", "crypto", "stock", "update", "forecast"
            ]
                
                is_relevant = any(word in title for word in keywords)
                if not is_relevant and impact != "🔴 HIGH":
                    low_priority_news.append(f"⚪️ {clean_title}")
                    continue

                # 🚫 АНТИ-ДУБЛІКАТИ
                news_id = hashlib.md5(title.encode()).hexdigest()

                if news_id in posted_news:
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
            if impact == "🔴 HIGH": confidence += 25
            elif impact == "🟡 MEDIUM": confidence += 15

            confidence += abs(signal_score) * 5

            if signal_score >= 3:
                confidence += 5

            # ключові слова (сила новини)
            strong_words = ["inflation", "cpi", "fed", "rate", "war", "crisis"]

            if any(word in title for word in strong_words):
                confidence += 10
            
            # 🔥 TIER LOGIC

            if impact == "🔴 HIGH" or confidence >= 75:
                tier = "high"
            elif confidence >= 60:
                tier = "medium"
            else:
                tier = "low"

            if confidence >= 80:
                confidence_label = "🔥 STRONG"
            elif confidence >= 65:
                confidence_label = "⚡ MEDIUM"
            else:
                confidence_label = "⚪ WEAK"


            raw_summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
            clean_summary = BeautifulSoup(raw_summary, "html.parser").get_text()

            clean_summary = clean_summary.split("http")[0]

            news_text = clean_title + ". " + clean_summary[:150]
            post_text = news_text

            # ⏱ TIME CONTROL
            current_time = time.time()
            time_since_last = current_time - last_post_time

            if tier == "high":
                pass # Пропускаємо до публікації негайно
        
            elif tier == "medium":
                if time_since_last < 600: # 10 хвилин
                    low_priority_news.append(f"🟡 {clean_title}")
                    continue
        
            else: # low
                low_priority_news.append(f"🔹 {clean_title}")
                continue
                

            try:
                assets = SIGNAL_IMPACT.get(signal, {})
                assets_text = " | ".join([
                    f"{ASSET_EMOJI.get(k, '')} {k} {ARROW_EMOJI.get(v, v)}"
                    for k, v in assets.items()
                ])
                
                if not assets_text:
                    assets_text = "No clear signal"

                signal_icon = SIGNAL_EMOJI.get(signal, "")

                confidence = min(confidence, 100)

                post = f"""🚨 Macro Update

                Signal: {signal_icon} {signal.upper()} ({confidence}% {confidence_label})
                Impact: {impact}

                Category: {category.upper()}
            
                {post_text}

                Assets:
               {assets_text}
               """

                if impact != "HIGH" and any(title[:50] in t for t in recent_titles):
                    continue
                
                send_to_telegram(post)
                last_post_time = time.time()

                posted_news.add(news_id)
                recent_titles.append(title.lower())

                if len(recent_titles) > 20:
                    recent_titles.pop(0)

                print("Posted:", title)

            except Exception as e:
                print("Error:", e)

            now_ts = time.time()
            if (now_ts - last_digest_time > 600) or (len(low_priority_news) >= 5):
                if low_priority_news:
                    print(f"⏰ Generating digest for {len(low_priority_news)} news...")
                    send_low_priority_digest() # Твоя функція з AI

                    low_priority_news.clear() # Очищуємо список, щоб наступного разу не було 7000+ новин
                    print("DEBUG: Список новин очищено.")

if __name__ == "__main__":
    while True:
        main()
        print("Waiting 60 seconds before next check...")
        time.sleep(60)
