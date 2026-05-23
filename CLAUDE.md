# Trading Bot — Project Context

Telegram-бот для трейдерів. Парсить економічний календар ForexFactory + RSS-стрічки, категоризує по важливості, постить у Telegram-канал. Деплой — Railway, автодеплой з гілки `main` на GitHub (`Shake987/Lemon4ik`).

## Архітектура

Один файл — [botik/bot.py](botik/bot.py). Без модулів, без тестів. Класичний `while True:` з `time.sleep(60)` між ітераціями.

### Зовнішні залежності

| Сервіс | Призначення | Як викликається |
|---|---|---|
| Telegram Bot API | Постить повідомлення/фото в канал | `send_to_telegram()`, `send_photo_to_telegram()` |
| **Finnhub Economic Calendar API** | Економічний календар (PreNews + Actual) | `get_forexfactory_events()` — назва функції збережена для сумісності, реальне джерело тепер Finnhub |
| RSS-фіди (BBC, FXStreet, MarketWatch, SCMP, CoinTelegraph, CoinDesk, Investing.com) | Новини | `feedparser` на список `RSS_URLS` |
| Google Gemini API | UA-переклад HIGH-новин + аналітика дайджесту + COT-коментарі | `call_gemini_ai()`, моделі: `gemini-2.5-flash-lite` (дешева first), `gemini-2.5-flash`, `gemini-2.0-flash` |
| Pollinations AI | Генерація картинок для HIGH-новин і дайджесту | `generate_ai_image()` через `image.pollinations.ai` |
| **CFTC через `cot_reports`** | COT звіти (тижневі позиції хедж-фондів) | `post_cot_reports()`, бібліотека `cot_reports` |
| **Finnhub Earnings Calendar API** | Квартальна звітність акцій (Revenue + EPS actual/estimate) | `post_earnings_reports()` через `/calendar/earnings` |
| **Deribit public API** | Опціонна аналітика BTC/ETH (Max Pain, PCR, Walls) | `post_options_desk()` через `/get_book_summary_by_currency` + `/get_index_price` |

### ENV variables

```
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
GOOGLE_API_KEY
FINNHUB_API_KEY
```

### Python dependencies (`requirements.txt`)

```
feedparser, requests, beautifulsoup4, lxml, google-genai,
cot_reports, pandas, matplotlib
```

## Логіка маршрутизації новин

Маршрутизація **по `impact`-міткі**, не по `confidence`. Impact визначається ключовими словами в title:
- `FED, RATE, CPI, INFLATION, FOMC, URGENT, BREAKING` → **HIGH**
- `MARKET, BANK, REPORT, ECONOMY, GROWTH, JOB, OUTLOOK, STOCKS, ANALYSIS` → **MEDIUM**
- решта → **LOW**

### HIGH

- Постимо **завжди**, без тротлінгу
- Викликаємо Gemini для UA-перекладу (1 речення українською)
- Генеруємо картинку через Pollinations за **тематичним пулом промтів** (`HIGH_NEWS_IMAGE_PROMPTS`):
  - `monetary` (3 промти) — title містить FED / FOMC / RATE
  - `inflation` (3 промти) — title містить CPI / INFLATION
  - `breaking` (3 промти) — title містить URGENT / BREAKING або fallback
  - `_pick_high_news_image_prompt(title)` обирає bucket за keywords, потім випадковий промт, але **виключає** `_last_high_image_prompt` (anti-repeat — два пости поспіль ніколи не використають той самий промт)
- Шлемо `send_photo_to_telegram(image, post)`. Якщо Telegram не прийняв фото — фолбек: текст без фото

### MEDIUM

- Тротлінг: один Medium на 90 хв (`last_medium_time`)
- Якщо вікно ще не пройшло — **в дайджест** з префіксом `🟡`
- Якщо пройшло — **текстовий пост**, мовою оригіналу, **без AI**
- Дедуплікація за заголовком через `recent_titles[:50]`

### LOW

- Завжди в дайджест з префіксом `🔹`
- Без виклику AI

### Дайджест

- Слоти: `DIGEST_TIMES_KYIV = [(9, 0), (15, 30), (19, 0)]` у **київському часі** (через `ZoneInfo("Europe/Kyiv")`, DST враховано автоматично)
  - 09:00 Київ — ранковий, опираючись на азійську сесію
  - 15:30 Київ — відкриття США
  - 19:00 Київ — вечір
- Вікно слоту — **60 хв** від запланованого часу (щоб бот міг наздогнати дайджест після рестарту в межах години)
- Умова відправки: `slot_start ≤ current_minutes_kyiv < slot_start + 60 AND slot_key != last_sent_slot AND len(low_priority_news) >= 10`
- Викликає `send_low_priority_digest()`, яка:
  1. Просить Gemini проаналізувати останні 30 новин і повернути `MOOD: ...` + `SUMMARY: ...`
  2. Генерує картинку (Bullish — зелений тейпсетап, Bearish/Neutral — темний неон)
  3. Шле `send_photo_to_telegram` з caption ≤ 1024 символів
- **Повертає `True/False`**:
  - `True` → main очищує `low_priority_news`, ставить `last_sent_slot = "YYYY-MM-DD_HH:MM"` (не дасть двічі відправити той самий слот того ж дня)
  - `False` (ШІ не відповів АБО Telegram не прийняв) → новини **залишаються** до наступного слоту
- `tzdata` у requirements.txt — Railway-контейнер інколи без системних таймзон, без цього `ZoneInfo` крешне

## COT Report (тижневі позиції хедж-фондів)

Вікно публікації — **п'ятниця ≥21:00 UTC до кінця неділі** (catch-up на вихідних) — `post_cot_reports()` тягне Legacy COT з CFTC через `cot_reports.cot_year()` і постить **5 ринків** з інтервалом 1 хв. Якщо в п'ятницю щось зашпортнулось (фетч впав, рестарт, дані з запізненням) — субота/неділя наздоженуть; дедуп `last_cot_release_date` не дасть опублікувати той самий звіт двічі.

| Display name | Ticker | CFTC pattern |
|---|---|---|
| GOLD | XAUUSD | `GOLD - COMMODITY EXCHANGE` |
| BITCOIN | BTCUSD | `BITCOIN - CHICAGO MERCANTILE` |
| EURO FX | EURUSD | `EURO FX - CHICAGO MERCANTILE` |
| S&P 500 | SPX | `E-MINI S&P 500 - CHICAGO MERCANTILE` |
| CRUDE OIL | WTI | `CRUDE OIL, LIGHT SWEET` |

Для кожного ринку:
1. Тягнемо рік + минулий рік (для 52-тижневого вікна sentiment percentile)
2. Витягуємо Noncommercial Long/Short + weekly change
3. Рахуємо Net = Long - Short і percentile поточного Net у 52-тижневому вікні
4. Генеруємо лінійний графік Net Position за 26 тижнів через `matplotlib` (headless via `Agg` backend)
5. Просимо Gemini короткий коментар (2-3 речення українською)
6. Шлемо як фото з текстом-caption. Якщо фото не доставлено — фолбек: текст без фото

Стан в пам'яті: `last_cot_release_date` (дата останнього звіту). Не постимо повторно якщо дата збігається. **Дата рахується з колонки `As of Date in Form YYYY-MM-DD` (рядок) + `.astype(str)`** — НЕ з `YYMMDD` (integer), бо pandas парсить int як наносекунди → 1970-01-01 і дедуп ламається назавжди (звіт «вже опублікований» щотижня). Це баг, на який уже наступали — див. розділ «Граблі».

### Тестовий перемикач

`COT_TEST_NOW=1` у Railway Variables — обходить guard вікна (Пт 21:00 → Нд), постить одразу при наступному циклі. **Обов'язково видалити після тесту** — інакше кожен рестарт контейнера буде заново публікувати 5 ринків (бо `last_cot_release_date` скидається при рестарті).

### Граблі, на які наступали (не повторювати)

- **MICRO/E-MICRO контракти** треба явно виключити з матчингу — інакше для GOLD/BTC/S&P беруться неправильні дані (інший номінал контракту, інші об'єми). Виключення через regex `\bMICRO\b|\bE-MICRO\b` у `_extract_market()`.
- **pandas `to_datetime` на int** інтерпретує число як наносекунди з Unix epoch → дата `1970-01-01`. Завжди `.astype(str)` перед парсингом дати з CFTC колонок. **CFTC віддає ДВІ колонки дати**: `As of Date in Form YYMMDD` (integer, перша по порядку!) і `As of Date in Form YYYY-MM-DD` (рядок). Наївний `_find_col(["as_of_date"])` грабає integer-колонку → 1970. Завжди преферити `["...","yyyy"]` варіант. **Цей баг уже ламав дедуп `post_cot_reports` (травень 2026)**: latest=1970 щотижня → COT не постився, бо «вже опублікований».
- **Telegram Markdown ламається** на незбалансованих `*` (часто від AI). Для COT-постів використовуємо `parse_mode=None` (plain text), бо Gemini у відповіді часом вкладає bullet-list з зірочками.
- **Gemini може дати "роман"** замість 2 речень — стримуємо жорстким промтом ("РОВНО 2 речення, БЕЗ списків, БЕЗ заголовків") + `_sanitize_ai_text()` ще раз чистить markdown і обрізає до 350 симв.

## Earnings (квартальна звітність акцій)

Джерело: `https://finnhub.io/api/v1/calendar/earnings` (той самий ключ Finnhub, free tier).

Функція `post_earnings_reports()` запускається на кожній ітерації main (раз на хвилину):
1. Тягне earnings calendar за `[today-1, today+1]` UTC
2. Фільтрує по `EARNINGS_TICKERS` (17 топ-акцій)
3. Для кожної події де `epsActual` заповнений (звіт уже вийшов) і ключ `SYMBOL_YEAR_Q{Q}` ще не в `posted_earnings`:
   - Форматує Revenue (`_fmt_money`: 94.93B / 1.50T / 500M)
   - Рахує beat/miss % vs estimate (`_earnings_beat`: ✅/❌/⚖️)
   - Постить плоским текстом через `send_to_telegram` (без Markdown — щоб назви на кшталт `JD.COM` не ламали парсер)
   - Додає ключ у `posted_earnings`

### Список тікерів (17)

| Категорія | Тікери |
|---|---|
| Big Tech US | AAPL, MSFT, GOOGL, AMZN, META, NFLX |
| AI / Semi | NVDA, AMD, AVGO, PLTR |
| Auto / Innovation | TSLA |
| Finance | JPM, V |
| Crypto-correlated | COIN, MSTR |
| Chinese ADR | BABA, JD |

Прапор країни — `🇺🇸` або `🇨🇳`, прописаний в `EARNINGS_TICKERS[symbol] = (flag, display_name)`.

### Тестовий перемикач

`EARNINGS_TEST_NOW=1` у Railway Variables — постить ОДИН тестовий пост за запуск контейнера. Логіка fallback:
1. Якщо у Finnhub-календарі є реальний звіт із заповненим `epsActual` → постить його з підписом `(тестовий пост)`
2. Якщо немає (між сезонами) → постить моковий AAPL Q4 FY2025 з підписом `(тестовий пост, мокові дані)`

Guard `_earnings_test_done` блокує повтор у тій же сесії. **Прибери змінну після перевірки** — інакше кожен рестарт контейнера буде слати ще один тест.

### Граблі

- **Пре-анонс не робимо** — постимо лише факт. Якщо змінити рішення — паттерн PreNews з Finnhub Economic Calendar можна повторити (тригер за `T-Xmin` від `ev["hour"]`).
- **Revenue в нативній валюті** — для US-акцій USD, для китайських ADR Finnhub теж повертає USD. Якщо колись треба буде показати CNY/EUR — додати `currency` поле з `_fmt_money`.
- **Дедуп при рестарті**: `posted_earnings` скидається. Якщо контейнер рестартує в межах того ж дня — є шанс повторного посту за тим самим ключем. Але вікно фетчу [today-1, today+1] → теоретично повтор можливий. Якщо це почне траплятись — додати персистентність (Redis або файл).

## Options Desk (BTC/ETH опціонна аналітика)

Джерело: **Deribit public API** (без ключа). Дві ендпоінтні точки:
- `/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option` — повна опціонна книга з Open Interest
- `/api/v2/public/get_index_price?index_name=btc_usd` — поточний спот

`post_options_desk()` запускається на кожній ітерації main, але виходить раніше якщо:
- День тижня ≠ понеділок (0) і ≠ п'ятниця (4)
- Година ≠ 10 за київським часом
- Дата вже в `posted_options_dates`

При спрацьовуванні слоту — постить BTC, потім через 60 сек ETH.

### Метрики

Для кожної з двох експірацій:
1. **Max Pain** — страйк що мінімізує сумарний payoff холдерам опціонів (формула: `Σ max(0, S−K_call)·OI + Σ max(0, K_put−S)·OI`, ітеруємо по всіх страйках, беремо min)
2. **PCR (Put/Call Ratio)** — `total_put_OI / total_call_OI`. Лейбли:
   - `> 1.0` → 🔴 Ведмежі настрої
   - `< 0.7` → 🟢 Бичачі настрої
   - інакше → ⚖️ Нейтральні
3. **Call Wall** — страйк з найбільшим OI кол-опціонів (рівень опору, маркетмейкери захищають)
4. **Put Wall** — страйк з найбільшим OI пут-опціонів (рівень підтримки)

### Вибір експірацій

`_pick_expirations()`:
- **Тижнева** = nearest п'ятниця ≥ сьогодні. Не nearest *будь-яка* експірація, бо Deribit має щоденні опціони, а канонічна "weekly" — це п'ятниця.
- **Місячна** = nearest остання-п'ятниця-місяця після weekly. Fallback — будь-яка експірація щонайменше через 14 днів від weekly.

### AI коментар

`_options_ai_commentary()` → Gemini → `_sanitize_ai_text` (як у COT). 2 речення українською, без markdown/списків. Якщо Gemini заблокований/упав — пост без `🗣` рядка.

### Тестовий перемикач

`OPTIONS_TEST_NOW=1` у Railway Variables — постить одразу обидва (BTC + ETH), ігнорує розклад. Guard `_options_test_done` — один пост за запуск контейнера. **Прибери після тесту!**

### Граблі / обмеження v1

- **Тільки крипта**. Традиційні активи (Gold, EUR, S&P, WTI) потребують CME / Coinglass платний API. Не реалізовано.
- **GEX (Gamma Exposure)** не рахуємо. Потребує greeks для кожного інструмента — це 100+ API-калів і ризик rate-limit Deribit. Можна додати v2 через окремий ендпоінт `/get_option_data`.
- **Daily expirations** Deribit BTC можуть бути будь-якого дня тижня (включаючи Sat/Sun). Тому беремо саме nearest **Friday** як weekly, а не nearest expiration.
- Дедуп: `posted_options_dates` скидається при рестарті. Якщо контейнер рестартує між 10:00 і 11:00 у понеділок/п'ятницю — є шанс повторного посту. Малоймовірно (Railway рестарти рідкі), але можна додати персистентність.

## Економічний календар (через Finnhub)

Джерело: `https://finnhub.io/api/v1/calendar/economic` (free tier, 60 req/min). Раніше використовували static XML feed від `nfs.faireconomy.media`, але він відставав на 10-15 хв і ми пропускали FactNews. Finnhub оновлює дані близько до реал-тайму.

### Фільтр валют

Country code → currency мапінг у `FINNHUB_COUNTRY_TO_CURRENCY`:

| Country code | Currency | Які impact постимо |
|---|---|---|
| US | USD | High + Medium (30% Medium після антиспам-фільтра) |
| EU | EUR | High + Medium (30% Medium після антиспам-фільтра) |
| GB | GBP | High + Medium (30% Medium після антиспам-фільтра) |
| JP | JPY | тільки High (через `FINNHUB_HIGH_ONLY_COUNTRIES`) |
| CA | CAD | тільки High |
| AU | AUD | тільки High |

Інші країни — пропускаються в `get_forexfactory_events()` (назва функції збережена historically, реально працює через Finnhub).

**Антиспам Medium:** `FINNHUB_MEDIUM_DROP_PCT = 70` — 70% Medium-подій (US/EU/GB) пропускаємо. Рішення детерміноване через `md5(event_title + country + time)` — та сама подія завжди або проходить, або дропається. Гарантує що PreNews і MAIN для тої ж події синхронні (обоє або дропаються, або обоє постяться).

Подія повертається у форматі: `{title, currency, impact, time, actual, forecast, previous}`. Далі у main loop:
- Skip `impact.lower() == "low"`
- Skip події, які >2 год вперед

### PreNews

- Вікно `0 < minutes_to_event ≤ 5`
- ID: `title + currency + impact + "_PRE"`
- Шле сценарій (`Strong inflation → USD ↑ / Gold ↓`)
- **Планує точковий фетч** Finnhub на `event_time + 4 хв` (через `pending_actual_fetches`)

### FactNews / MAIN

- Вікно `-20 ≤ minutes_to_event ≤ 2`
- ID: `title + currency + impact + "_MAIN_" + actual` (унікальний по значенню Actual)
- Якщо `actual` порожній і не speech-подія → `continue`, чекаємо наступну ітерацію
- Шле блок з Actual / Forecast / Previous + напрямок (`📈 ABOVE FORECAST`, etc.)

### Точкові фетчі календаря

Оновлення йде у двох випадках:
1. **Раз на 15 хв** (стандартне планове)
2. **Точкова перевірка** — після PreNews заплановано `pending_actual_fetches[id]["check_at"] = event_time + 240`. Коли наступив час — один фетч.

Якщо Actual ще не з'явився — **одна додаткова спроба** через 6 хв (`retries=1`). Потім — здаємось і видаляємо з черги.

Спіч-події (`speak`, `testif` у title) **не плануються на retry** — там Actual не буває.

## Запобіжники

### Gemini circuit breaker

При першій помилці `RESOURCE_EXHAUSTED` / `prepayment` / `credits depleted`:
- `gemini_blocked_until = time.time() + 1800` (30 хв)
- Усі наступні виклики `call_gemini_ai` повертають одразу без API-запиту
- Через 30 хв пробуємо знов

Це захист від ситуації, коли кредити вичерпались — інакше бот марнував ~2 хв на ретраї при кожному виклику.

### Main loop

- `while True:` всередині `main()` з обов'язковим `time.sleep(60)` в кінці — інакше бот спінить CPU на 100% і б'є API
- Зовнішній `if __name__ == "__main__":` обгортає `main()` в `try/except` з sleep-60s — якщо щось крешне, бот рестартується замість падіння

### Telegram

- `send_photo_to_telegram` повертає `True/False` (раніше — `Response`, помилки тихо ковтались)
- `send_low_priority_digest` чекає `True` перед очисткою списку — інакше новини зберігаються

## Деплой

```bash
cd /Users/user/trading-bot
git add botik/bot.py
git commit -m "..."
git push origin main
```

Railway підхопить з GitHub автоматично за 1-2 хв. Перевіряти: Railway → Deployments → View Logs.

Перші ознаки що все ОК у логах:
- `START FILE`
- `⏸ Sleeping 60s before next cycle...` (бот не спінить)
- На дайджест-слот (Київ): `⏰ Час дайджесту (HH:MM Київ)!` або `⏳ Слот HH:MM Київ підійшов, але новин мало (X/10)`

## Загальні зауваження

- **Часова зона**: код використовує `datetime.datetime.now()` без tz — це серверний час (Railway = UTC). Київ зимою = UTC+2, влітку = UTC+3.
- **Стан в пам'яті, не персистентний**: `posted_news`, `posted_events`, `posted_earnings`, `posted_options_dates`, `low_priority_news`, `last_sent_slot`, `last_medium_time`, `pending_actual_fetches`, `gemini_blocked_until` — все скидається при рестарті контейнера. Це OK для коротких вікон (PreNews/MAIN <30 хв), не OK для дайджесту якщо рестарт стається часто.
- **Gemini spend cap**: бажано встановити в AI Studio → Set spend cap (наприклад $5/місяць) як safety net проти runaway costs. Раніше один зациклений лоп з'їв ~$8 за пару днів.
- **Pollinations** безкоштовний, без API ключа, але може повертати не-картинку (HTML/помилку) — є фолбек на `FALLBACK_IMAGE_URL` (Unsplash).

## Формати постів (для довідки)

### Хештеги по рубриках

| Тип посту | Хештег |
|---|---|
| RSS HIGH (Macro Update) | `#highimpactnews` |
| RSS MEDIUM | без хештега |
| Finnhub PreNews + FactNews | `#economiccalendar` |
| Дайджест | `#digest` |
| COT Report | `#cotreport #{TICKER}` (напр. `#cotreport #XAUUSD`) |
| Earnings | `#{SYMBOL} #earnings #звітність` (напр. `#AAPL #earnings #звітність`) |
| Options Desk | `#optionsdesk #{CURRENCY}` (напр. `#optionsdesk #BTC`) |

### HIGH (з картинкою)
```
🚨 **Macro Update**

Signal: 📈 HAWKISH (95% 🔥 STRONG)
Impact: 🔴 HIGH

Category: MACRO

{title + summary[:150]}

🗣 {UA переклад одним реченням}

Assets:
💵 USD 🟢↑ | 🥇 Gold 🔴↓ | 📊 Indices 🔴↓

#highimpactnews
```

### MEDIUM (текст)
Те саме, без `🗣` рядка, `Impact: 🟡 MEDIUM`, **без хештега**.

### PreNews
```
⏳ Upcoming Event ({N} min)

Event: {TITLE}
Currency: {USD}
Impact: {HIGH}

🧠 Scenarios:
↑ Strong inflation → USD ↑ / Gold ↓
↓ Weak inflation → USD ↓ / Gold ↑

#economiccalendar
```

### FactNews
```
🚨 Economic Release

Event: {TITLE}
Currency: {USD}

Actual: 3.2%
Forecast: 3.1%
Previous: 3.0%

📈 ABOVE FORECAST

📈 USD ↑ / Gold ↓

#economiccalendar
```

### Дайджест (з картинкою)
```
📊 **DAILY MARKET SUMMARY (Low Impact)**

{AI summary 3-5 речень українською, mood-based image}

#digest
```

### Earnings (текст)
```
🇺🇸 #AAPL #earnings #звітність

APPLE INC.
Звіт: Q4 FY2025

Виторг: $94.93B vs $94.10B ✅ (+0.9%)
EPS:    $1.64 vs $1.59 ✅ (+3.1%)
```

### Options Desk (з картинкою, parse_mode=Markdown)
```
📊 *ОПЦІОННИЙ ДЕСК: BTC*

💰 Спот: $81,145

📅 *Тижнева експірація* (15 травня, +2д)
🎯 *Max Pain*: $80,000 (-1.4%)
⚖️ *PCR*: 0.65 🟢 Бичачі настрої
🧱 *Стіна опору*: $85,000 (OI 1.6K)
🛡 *Стіна підтримки*: $72,000 (OI 1.4K)

📅 *Місячна експірація* (29 травня, +16д)
🎯 *Max Pain*: $75,000 (-7.6%)
⚖️ *PCR*: 0.71 ⚖️ Нейтральні
🧱 *Стіна опору*: $80,000 (OI 7.0K)
🛡 *Стіна підтримки*: $70,000 (OI 3.6K)

🗣 {AI 1 коротке речення українською, ≤170 симв.}

#optionsdesk #BTC
```

- Жирним (`*term*`, Telegram legacy Markdown): назви секцій, метрик
- Картинка генерується Pollinations за тематичним промптом (`OPTIONS_IMAGE_PROMPTS`): для BTC — orange/gold neon, для ETH — electric blue/purple neon
- Фолбек: якщо Telegram не прийняв фото — шлемо текст-only через `send_to_telegram`

## Що не використовується / dead code

- `get_scenario(title)` — функція з `if pmi/cpi → ...`. Не викликається в main, але залишена як шаблон.
- `ASSET_IMPACT` — словник по категоріях, не використовується (заміщений `SIGNAL_IMPACT`).

## Типові операції

- Поглянути за останніми постами/помилками: Railway → Deployments → останній → View Logs
- Швидкий відкат: `git revert HEAD && git push origin main`
- Перевірити, чи не зациклився бот: у логах має бути регулярний `⏸ Sleeping 60s before next cycle...`
- Запустити локально (для тесту): встановити ENV-змінні + `python3 botik/bot.py`
