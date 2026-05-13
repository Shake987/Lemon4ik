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
- Генеруємо картинку через Pollinations
- Шлемо `send_photo_to_telegram(image, post)`. Якщо Telegram не прийняв фото — фолбек: текст без фото

### MEDIUM

- Тротлінг: один Medium на 30 хв (`last_medium_time`)
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

Раз на тиждень — **п'ятниця ≥21:00 UTC** — `post_cot_reports()` тягне Legacy COT з CFTC через `cot_reports.cot_year()` і постить **5 ринків** з інтервалом 1 хв:

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

Стан в пам'яті: `last_cot_release_date` (дата останнього `As_of_Date_In_Form_YYMMDD`). Не постимо повторно якщо дата збігається.

### Тестовий перемикач

`COT_TEST_NOW=1` у Railway Variables — обходить guard "тільки п'ятниця ≥21 UTC", постить одразу при наступному циклі. **Обов'язково видалити після тесту** — інакше кожен рестарт контейнера буде заново публікувати 5 ринків (бо `last_cot_release_date` скидається при рестарті).

### Граблі, на які наступали (не повторювати)

- **MICRO/E-MICRO контракти** треба явно виключити з матчингу — інакше для GOLD/BTC/S&P беруться неправильні дані (інший номінал контракту, інші об'єми). Виключення через regex `\bMICRO\b|\bE-MICRO\b` у `_extract_market()`.
- **pandas `to_datetime` на int** інтерпретує число як наносекунди з Unix epoch → дата `1970-01-01`. Завжди `.astype(str)` перед парсингом дати з CFTC колонок.
- **Telegram Markdown ламається** на незбалансованих `*` (часто від AI). Для COT-постів використовуємо `parse_mode=None` (plain text), бо Gemini у відповіді часом вкладає bullet-list з зірочками.
- **Gemini може дати "роман"** замість 2 речень — стримуємо жорстким промтом ("РОВНО 2 речення, БЕЗ списків, БЕЗ заголовків") + `_sanitize_ai_text()` ще раз чистить markdown і обрізає до 350 симв.

## Економічний календар (через Finnhub)

Джерело: `https://finnhub.io/api/v1/calendar/economic` (free tier, 60 req/min). Раніше використовували static XML feed від `nfs.faireconomy.media`, але він відставав на 10-15 хв і ми пропускали FactNews. Finnhub оновлює дані близько до реал-тайму.

### Фільтр валют

Country code → currency мапінг у `FINNHUB_COUNTRY_TO_CURRENCY`:

| Country code | Currency | Які impact постимо |
|---|---|---|
| US | USD | High + Medium |
| EU | EUR | High + Medium |
| GB | GBP | High + Medium |
| JP | JPY | тільки High (через `FINNHUB_HIGH_ONLY_COUNTRIES`) |
| CA | CAD | тільки High |
| AU | AUD | тільки High |

Інші країни — пропускаються в `get_forexfactory_events()` (назва функції збережена historically, реально працює через Finnhub).

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
- **Стан в пам'яті, не персистентний**: `posted_news`, `posted_events`, `low_priority_news`, `last_sent_slot`, `last_medium_time`, `pending_actual_fetches`, `gemini_blocked_until` — все скидається при рестарті контейнера. Це OK для коротких вікон (PreNews/MAIN <30 хв), не OK для дайджесту якщо рестарт стається часто.
- **Gemini spend cap**: бажано встановити в AI Studio → Set spend cap (наприклад $5/місяць) як safety net проти runaway costs. Раніше один зациклений лоп з'їв ~$8 за пару днів.
- **Pollinations** безкоштовний, без API ключа, але може повертати не-картинку (HTML/помилку) — є фолбек на `FALLBACK_IMAGE_URL` (Unsplash).

## Формати постів (для довідки)

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
```

### MEDIUM (текст)
Те саме, без `🗣` рядка, `Impact: 🟡 MEDIUM`.

### PreNews
```
⏳ Upcoming Event ({N} min)

Event: {TITLE}
Currency: {USD}
Impact: {HIGH}

🧠 Scenarios:
↑ Strong inflation → USD ↑ / Gold ↓
↓ Weak inflation → USD ↓ / Gold ↑
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
```

### Дайджест (з картинкою)
```
📊 **DAILY MARKET SUMMARY (Low Impact)**

{AI summary 3-5 речень українською, mood-based image}

#DailyDigest #MarketUpdate
```

## Що не використовується / dead code

- `get_scenario(title)` — функція з `if pmi/cpi → ...`. Не викликається в main, але залишена як шаблон.
- `ASSET_IMPACT` — словник по категоріях, не використовується (заміщений `SIGNAL_IMPACT`).

## Типові операції

- Поглянути за останніми постами/помилками: Railway → Deployments → останній → View Logs
- Швидкий відкат: `git revert HEAD && git push origin main`
- Перевірити, чи не зациклився бот: у логах має бути регулярний `⏸ Sleeping 60s before next cycle...`
- Запустити локально (для тесту): встановити ENV-змінні + `python3 botik/bot.py`
