print("START FILE")

import feedparser
import requests
import time
import random
import datetime
import hashlib
import os
import warnings
from bs4 import XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

from bs4 import BeautifulSoup

def get_direction(actual, forecast):
    try:
        a = float(actual.replace("%", ""))
        f = float(forecast.replace("%", ""))

        if a > f:
             return "UP"
        elif a < f:
             return "DOWN"
        else:
             return "NEUTRAL"
    except:
        return "UNKNOWN"


def get_forexfactory_events():
    url = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    import time

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
# 🔑 CONFIG
# =========================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

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
    posted_news = set()
    recent_titles = []
    posted_events = set()
    last_post_time = time.time()
    last_update = 0

    while True:
        print("LOOP STARTED")
        
        now_ts = time.time()
        
        # =========================
        # 🟢 1. FOREX FACTORY (CALENDAR)
        # =========================
        
        # 🔄 оновлення раз на 15 хв
        if now_ts - last_update > 900 or any(
            0 < (e["time"] - datetime.datetime.now(datetime.timezone.utc)).total_seconds()/60 < 3
            for e in events
        ):
            print("🔄 Updating ForexFactory...")
            events = get_forexfactory_events()
            print("📅 EVENTS COUNT:", len(events))
            last_update = now_ts
        else:
            print("📊 Using cached events:", len(events))

        for event in events:
            title = event["title"]
            currency = event["currency"]
            impact = event["impact"]

            if currency not in ["USD", "EUR", "GBP"]:
                continue

            if impact.lower() != "high":
                continue

            # 🔍 DEBUG 
            print("EVENT TIME:", event["time"], type(event["time"]))

            # 🧠 SCENARIOS (SMART)

            title_lower = title.lower()

            # CPI / Inflation
            if any(word in title_lower for word in ["cpi", "pce", "inflation"]):

                if currency == "USD":
                    scenario = """↑ Strong inflation → USD ↑ / Gold ↓ / Indices ↓
↓ Weak inflation → USD ↓ / Gold ↑ / Indices ↑"""

                elif currency == "EUR":
                    scenario = """↑ Strong inflation → EUR ↑ / USD ↓
↓ Weak inflation → EUR ↓ / USD ↑"""

                elif currency == "GBP":
                    scenario = """↑ Strong inflation → GBP ↑ / USD ↓
↓ Weak inflation → GBP ↓ / USD ↑"""

                else:
                    scenario = "Inflation event – watch volatility"
            
            # NFP / Jobs
            elif "nfp" in title_lower or "employment" in title_lower:

                if currency == "USD":
                    scenario = """↑ Strong jobs → USD ↑ / Indices ↑
↓ Weak jobs → USD ↓ / Indices ↓"""

                elif currency == "EUR":
                    scenario = """↑ Strong jobs → EUR ↑
↓ Weak jobs → EUR ↓"""

                elif currency == "GBP":
                    scenario = """↑ Strong jobs → GBP ↑
↓ Weak jobs → GBP ↓"""
            
                else:
                    scenario = "Jobs data – volatility expected"
            
            # 🔥 PRE-NEWS ЛОГІКА

            event_time = event["time"]
            now = datetime.datetime.now(datetime.timezone.utc)

            minutes_to_event = (event_time - now).total_seconds() / 60

            if minutes_to_event > 120:
                continue

            if 0 < minutes_to_event <= 5 and impact.lower() == "high":            
                
                event_id = (title + currency + impact + "PRE").strip()

                if event_id in posted_events:
                    continue
                post = f"""⏳ Upcoming Event ({int(minutes_to_event)} min)

            Event: {title.upper()}
            Currency: {currency}
            Impact: 🔴 HIGH

            🧠 Scenarios:
            {scenario}
            """

                send_to_telegram(post)
                posted_events.add(event_id)

                print("⏳ Sent PRE event:", title)

                continue 

            # 🔥 MAIN (в момент новини)
            if -20 <= minutes_to_event <= 10:

                actual = event.get("actual", "")
                forecast = event.get("forecast", "")
                previous = event.get("previous", "")

                if not actual and minutes_to_event < -12:
                    continue

                # 🧠 УНІКАЛЬНИЙ КЛЮЧ ПОДІЇ
                base_id = (title + currency + impact).strip()
                
                # 🔥 ЛОГІКА ID
                
                base_id = (title + currency + impact).strip()
                
                if not actual:
                    event_id = base_id + "_WAIT"
                else:
                    event_id = base_id + "_ACTUAL_" + actual

                # 🚫 БЛОК ДУБЛІВ
                if event_id in posted_events:
                    continue

                print("ACTUAL:", actual)
                print("FORECAST:", forecast)

                
                # 🧠 ЛОГІКА ДАНИХ
                if not actual:
                    result = "⏳ Waiting for data..."
                    move = ""
                else:
                    direction = get_direction(actual, forecast)

                    if direction == "UP":
                        result = "📈 ABOVE FORECAST"
                        move = "📈 USD ↑ / Gold ↓ / Indices ↓"

                    elif direction == "DOWN":
                        result = "📉 BELOW FORECAST"
                        move = "📉 USD ↓ / Gold ↑ / Indices ↑"

                    elif direction == "NEUTRAL":
                        result = "📊 IN LINE"
                        move = "⚖️ No strong move"

                    else:
                        result = "⚠️ Data error"
                        move = ""

                post = f"""🚨 Economic Release

            Event: {title.upper()}
            Currency: {currency}

            Actual: {actual}
            Forecast: {forecast}
            Previous: {previous}

            {result}

            {move}
            """

                send_to_telegram(post)
                posted_events.add(event_id)

                print("📅 Sent MAIN event:", title)

                continue   
            
        # =========================
        # 🔵 2. RSS NEWS
        # =========================
        for url in RSS_URLS:
            print("👉 LOOP URL:", url)
            
            response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
            feed = feedparser.parse(response.content)

            print("RSS LOADED")
            print("ENTRIES COUNT:", len(feed.entries))
            

            for entry in feed.entries[:3]:

                category = "other"
                impact = "LOW" 
                print("Checking:", entry.title)

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

                print("CATEGORY:", category)

                # 🔥 IMPACT
                if any(word in title for word in HIGH_IMPACT):
                    impact = "🔴 HIGH"
                elif any(word in title for word in MEDIUM_IMPACT):
                    impact = "🟡 MEDIUM"
                else:
                    impact = "🟢 LOW"

                print("IMPACT:", impact)


                # 🎯 FINAL CONTROL 

                if impact == "HIGH":
                    pass  # завжди публікуємо

                elif impact == "MEDIUM":
                    if random.random() > 0.8:  # ~80% проходить
                        print("⏭ SKIP MEDIUM:", title)
                        continue

                elif impact == "LOW":
                    if category == "other":
                        print("❌ SKIP LOW OTHER:", title)
                        continue
                    if random.random() > 0.2:  # тільки ~20% LOW
                        print("⏭ SKIP LOW:", title)
                        continue
                
                news_id = (entry.title + str(entry.get("link", ""))).strip()

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

            print("SIGNAL:", signal)

            # 🔥 ФІЛЬТР
            keywords = [
                    "inflation", "cpi", "fed", "interest rate", "powell",
                    "recession", "gdp", "jobs", "nfp",
                    "ecb", "boe", "central bank",
                    "oil", "opec", "war", "ppi", "core ppi", "wholesale inflation"
            ]
                
            if impact != "HIGH":
                if not any(word in title for word in keywords):
                    print("❌ SKIPPED:", title)
                    continue

            # 🚫 АНТИ-ДУБЛІКАТИ
                news_id = hashlib.md5(title.encode()).hexdigest()

                if news_id in posted_news:
                    print("⛔ DUPLICATE:", title)
                    continue       

            # 🔥 FORCE HIGH NEWS (агресивний режим)
            if impact == "HIGH":
                print("🔥 FORCE HIGH:", title)
                pass

            # ⏱ АНТИ-СПАМ (2 хв між новинами)
                if impact != "HIGH" and time.time() - last_post_time < 120:
                    print("⏱ SKIP (SPAM CONTROL):", title)
                    continue

            # 2. CRYPTO — теж пропускаємо навіть якщо neutral
            elif any(x in title for x in ["bitcoin", "btc", "crypto"]):
                print("🟡 CRYPTO FORCE:", title)
                pass

            # 3. всі інші — фільтр neutral
            elif signal == "neutral":
                print("❌ SKIPPED (neutral):", title)
                continue

            # 🔥 CONFIDENCE
            confidence = 50  # база 

            # вплив сигналу
            if signal in ["hawkish", "dovish"]:
                confidence += 20

            elif signal in ["risk_on", "risk_off"]:
                confidence += 15

            # вплив impact
            if impact == "🔴 HIGH":
                confidence += 25
            elif impact == "🟡 MEDIUM":
                confidence += 15
            else:
                confidence += 5

            confidence += abs(signal_score) * 5

            if signal_score >= 3:
                confidence += 5

            # ключові слова (сила новини)
            strong_words = ["inflation", "cpi", "fed", "rate", "war", "crisis"]

            if any(word in title for word in strong_words):
                confidence += 10
            
            # 🔥 TIER LOGIC
            if impact == "HIGH":
                tier = "high"

            elif confidence >= 75:
                tier = "high"

            elif confidence >= 60:
                tier = "medium"

            else:
                tier = "low"

            if impact == "HIGH":
                tier = "high"    

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

            # 🚨 HIGH — одразу
            if impact == "HIGH":
                pass

            # 🟡 MEDIUM
            elif tier == "medium":
                if time_since_last < 600:
                    print("❌ BLOCKED MEDIUM:", title)
                    continue

            # 🟢 LOW
            elif tier == "low":
                if time_since_last < 3600:
                    print("❌ BLOCKED LOW:", title)
                    continue
                
            print("✅ PASSED:", title, "| impact:", impact, "| tier:", tier)
            print("BEFORE TRY")
            

            try:
                print("INSIDE TRY")

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
                    print("⚠️ SIMILAR NEWS:", title)
                    continue
                
                print("SENDING MESSAGE")
                send_to_telegram(post)

                posted_news.add(news_id)
                recent_titles.append(title.lower())

                if len(recent_titles) > 20:
                    recent_titles.pop(0)
    
                last_post_time = time.time()

                print("Posted:", title)

                time.sleep(10)  # пауза між постами

            except Exception as e:
                print("Error:", e)

        time.sleep(300)  # перевірка кожні 5 хвилин


if __name__ == "__main__":
    print("BOT STARTED")
    main()

