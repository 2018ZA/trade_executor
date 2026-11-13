# trade_executor

Движок принятия торговых решений. **Стратегия 1: Score-based (только LONG).**

Читает `overall_score` из таблицы `oscillator_data` (общая БД с `crypto_allert`), открывает LONG-позиции при превышении `buy_score`, закрывает по сигналу `close_position`, стоп-лоссу или тейк-профиту. Все параметры вынесены в таблицу `strategy_confs` — меняются прямо в БД без перезапуска кода.

## Правила Стратегии 1

| Параметр | По умолчанию | Значение |
|---|---|---|
| `buy_score` | 8.0 | Открыть LONG, если `overall_score > buy_score` (по закрытию свечи) |
| `close_position` | −6.0 | Закрыть LONG, если `overall_score < close_position` (по закрытию свечи) |
| `stop_loss_percent` | 0.05 | SL = entry × (1 − 0.05) = −5% от входа |
| `profit_percent` | 0.10 | TP = entry × (1 + 0.10) = +10% от входа |
| `position_size_usd` | 100.0 | Фиксированный размер позиции в USD |
| `max_open_positions` | 10 | Глобальный лимит одновременных позиций |
| `symbols` | null | null = все символы, найденные в `oscillator_data` |

**Жизненный цикл сделки:**
- `status = '1'` — позиция открыта
- `status = '0'` — позиция закрыта
- При закрытии фиксируются `pnl_pct` и `pnl_usd`

**Порядок проверки на каждом закрытом баре:**

1. Если позиции нет:
   - `overall_score > buy_score` → **OPEN** по `kline.close_price`
   - иначе → HOLD
2. Если позиция есть (проверка в порядке приоритета):
   - `kline.low_price <= stop_loss` → **CLOSE** по цене стопа (`STOP_LOSS`)
   - `kline.high_price >= take_profit` → **CLOSE** по цене TP (`TAKE_PROFIT`)
   - `overall_score < close_position` → **CLOSE** по `kline.close_price` (`SIGNAL`)

> Если в одном баре задеты и SL, и TP — приоритет у **стоп-лосса** (консервативная логика).

## Архитектура

```
trade_executor/
├── config.py              # Переменные окружения (.env)
├── models.py              # ORM-модели: StrategyConf, Trade, TEState + read-only зеркала
├── database.py            # Database: сессии, create_tables
├── strategy.py            # Логика принятия решений (process_bar)
├── engine.py              # Открытие/закрытие сделок в БД
├── init_db.py             # Создать таблицы + seed дефолтного конфига
├── backtest.py            # Полный исторический прогон (перезаписывает тей-трейды)
├── run.py                 # Инкрементальная обработка (для сервиса)
├── service.py             # Демон: цикл run.py каждые N секунд
├── stats.py               # Статистика по сделкам
├── requirements.txt
├── .env.example
└── .gitignore
```

## Схема БД

### Свои таблицы (префикс `te_`)

**`strategy_confs`** — конфигурации стратегий

| Поле | Тип | Описание |
|---|---|---|
| `id` | serial4 PK | |
| `code` | varchar(32) UNIQUE | `s1_score_based` |
| `name` | varchar(128) | Описание |
| `is_active` | bool | Активна ли стратегия |
| `buy_score` | float8 | Порог открытия LONG |
| `close_position` | float8 | Порог закрытия по сигналу |
| `stop_loss_percent` | float8 | Стоп-лосс в долях (0.05 = 5%) |
| `profit_percent` | float8 | Тейк-профит в долях (0.10 = 10%) |
| `position_size_usd` | float8 | Размер позиции в USD |
| `max_open_positions` | int4 | Лимит одновременных позиций |
| `symbols` | jsonb NULL | Список символов (null = все) |
| `created_at`, `updated_at` | timestamptz | |

**`te_trades`** — журнал сделок

| Поле | Тип | Описание |
|---|---|---|
| `id` | bigserial PK | |
| `strategy_id` | int4 FK → strategy_confs | |
| `symbol` | varchar(20) | Тикер (`BTCUSDT`) |
| `direction` | varchar(8) | `LONG` (в Стр.1 только LONG) |
| `status` | varchar(8) | `'1'` = open, `'0'` = closed |
| `open_kline_id` | bigint | ID свечи открытия |
| `open_oscillator_id` | bigint | ID осциллятора открытия |
| `open_time` | timestamp | Время открытия (open_time свечи) |
| `entry_price` | float8 | Цена входа |
| `quantity` | float8 | Размер в базовой валюте |
| `size_usd` | float8 | Размер в USD |
| `stop_loss` | float8 | Цена стоп-лосса |
| `take_profit` | float8 | Цена тейк-профита |
| `open_score` | float8 | `overall_score` на момент открытия |
| `close_kline_id` | bigint NULL | ID свечи закрытия |
| `close_oscillator_id` | bigint NULL | ID осциллятора закрытия |
| `close_time` | timestamp NULL | Время закрытия |
| `exit_price` | float8 NULL | Цена выхода |
| `close_reason` | varchar(32) NULL | `STOP_LOSS` / `TAKE_PROFIT` / `SIGNAL` |
| `close_score` | float8 NULL | `overall_score` на момент закрытия |
| `pnl_pct` | float8 NULL | Прибыль/убыток в долях |
| `pnl_usd` | float8 NULL | Прибыль/убыток в USD |
| `signal_snapshot` | jsonb | Снимок данных при открытии |
| `created_at`, `updated_at` | timestamptz | |

**Уникальный constraint:** `uq_te_trades_open (strategy_id, symbol, open_kline_id)` — защита от дублирующихся сделок на одной свече.

**`te_state`** — состояние инкрементальной обработки

| Поле | Тип | Описание |
|---|---|---|
| `strategy_id` | int4 FK | |
| `symbol` | varchar(20) | |
| `last_kline_id` | bigint | ID последней обработанной свечи |
| `updated_at` | timestamptz | |

**Уникальный constraint:** `uq_te_state (strategy_id, symbol)`.

### Read-only зеркала из `crypto_allert`

`KlineData` и `OscillatorData` определены в `models.py`, но **никогда не пишутся** из `trade_executor` — только читаются.

## Установка

### Требования

- Python 3.10+
- PostgreSQL 14+
- Доступ к БД `crypto_allert` (та же БД)

### Шаги

```bash
git clone <your-repo-url> trade_executor
cd trade_executor

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
nano .env
```

**Пример `.env`:**

```
DB_HOST=localhost
DB_PORT=5432
DB_NAME=crypto_analyzer
DB_USER=postgres
DB_PASSWORD=your_password
SERVICE_INTERVAL_SEC=60
```

## Использование

### 1. Инициализация БД

```bash
python init_db.py
```

Создаёт таблицы `strategy_confs`, `te_trades`, `te_state` (если их нет). Заполняет конфиг `s1_score_based` дефолтными параметрами.

**Ожидаемый вывод:**
```
Tables created (or already exists)
Created config 's1_score_based' (id=1)
```

Повторный запуск не создаёт дубликат — сообщает `Config 's1_score_based' already exists (id=1)`.

### 2. Бэктест на всей истории

```bash
# Чистый прогон — удаляет все предыдущие сделки стратегии
python backtest.py --reset

# Инкрементальный (продолжить с te_state.last_kline_id)
python backtest.py
```

**Что происходит:**
- Для каждого символа из `oscillator_data` загружаются все пары `(oscillator_data, kline_data)`, отсортированные по `kline_id ASC`.
- Для каждой пары вызывается `process_bar`.
- OPEN → создаётся запись в `te_trades` со `status='1'`.
- CLOSE → обновляется существующая запись (`status='0'`, `exit_price`, `pnl_pct`, `pnl_usd`).
- `te_state.last_kline_id` обновляется после каждой свечи.

**Ожидаемый вывод:**
```
Config: buy>8.0, close<-6.0, SL=5.0%, TP=10.0%, size=$100.0, max_open=10
Processing 13 symbols
[BTCUSDT   ] processed 264 bars
[ETHUSDT   ] processed 262 bars
...
============================================================
Total bars processed: 3300
Trades: 42 (open=3, closed=39)
Wins: 18, Losses: 21, Win rate: 46.2%
Total PnL: +12.34%  |  +123.40 USD
Avg PnL per closed: +0.32%
============================================================
```

### 3. Paper-trading в реальном времени

```bash
# Первый запуск — обработать только новейшую свечу (без истории)
python run.py --from-latest

# Первый запуск — обработать всю историю (без --reset)
python run.py

# Последующие запуски — только новые свечи с момента te_state.last_kline_id
python run.py
```

Отличие от `backtest.py`:
- `backtest.py --reset` **стирает** старые сделки и прогоняет заново.
- `run.py` **никогда не стирает**, только добавляет новые сделки и закрывает открытые.

### 4. Сервис (демон)

```bash
python service.py
```

Бесконечно вызывает `run.py` каждые `SERVICE_INTERVAL_SEC` секунд (по умолчанию 60). Ошибки логируются, но цикл не останавливается.

### 5. Статистика

```bash
python stats.py
```

**Ожидаемый вывод:**
```
=== Strategy 1 (s1_score_based) ===
Total trades: 42  (open=3, closed=39)
Wins: 18  Losses: 21  Win rate: 46.2%
Total PnL: +12.34%  |  +123.40 USD
Avg PnL per closed: +0.32%
Close reasons: {'TAKE_PROFIT': 15, 'STOP_LOSS': 20, 'SIGNAL': 4}

By symbol:
  BTCUSDT    trades=  8  win_rate= 62.5%  pnl=  +8.21%
  ETHUSDT    trades=  7  win_rate= 57.1%  pnl=  +5.13%
  ...
```

## Управление конфигурацией

Все параметры меняются **прямо в БД**, без перезапуска сервиса:

```sql
-- Изменить порог открытия
UPDATE strategy_confs SET buy_score = 7.0 WHERE code = 's1_score_based';

-- Изменить стоп-лосс на 3%
UPDATE strategy_confs SET stop_loss_percent = 0.03 WHERE code = 's1_score_based';

-- Ограничить только мажорами
UPDATE strategy_confs
SET symbols = '["BTCUSDT","ETHUSDT","SOLUSDT"]'::jsonb
WHERE code = 's1_score_based';

-- Выключить стратегию
UPDATE strategy_confs SET is_active = false WHERE code = 's1_score_based';
```

После изменения — перезапустите `backtest.py --reset`, чтобы увидеть, как новые параметры влияют на исторические результаты.

## Развёртывание (systemd)

`/etc/systemd/system/trade-executor.service`:

```ini
[Unit]
Description=Trade Executor Strategy 1
After=network.target postgresql.service

[Service]
Type=simple
User=root
WorkingDirectory=/root/trade_executor
EnvironmentFile=/root/trade_executor/.env
ExecStart=/root/trade_executor/venv/bin/python service.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable trade-executor
sudo systemctl start trade-executor
sudo systemctl status trade-executor

# Логи
sudo journalctl -u trade-executor -f
sudo journalctl -u trade-executor --since "1 hour ago" | grep -iE "error|traceback"
```

## Проверка работоспособности

```bash
# 1. Сервис жив
sudo systemctl is-active trade-executor

# 2. Свежие сделки в БД
psql -d $DB_NAME -c "
SELECT symbol, status, entry_price, exit_price, pnl_pct, close_reason
FROM te_trades ORDER BY open_time DESC LIMIT 10;
"

# 3. Открытые позиции
psql -d $DB_NAME -c "
SELECT symbol, entry_price, stop_loss, take_profit, open_time
FROM te_trades WHERE status='1' ORDER BY open_time DESC;
"

# 4. Состояние обработки (не отстаёт ли от свежих баров)
psql -d $DB_NAME -c "
SELECT s.symbol, s.last_kline_id,
       (SELECT MAX(kline_id) FROM oscillator_data o WHERE o.symbol = s.symbol) AS latest_kline_id
FROM te_state s ORDER BY s.symbol;
"
```

## Важные замечания

### 1. PnL без учёта издержек

`pnl_pct` и `pnl_usd` — **gross** (до комиссий и проскальзывания). Реальная доходность будет ниже.

Для оценки **чистой** доходности вычтите:
- комиссию taker (~0.04–0.1% за сторону → ~0.08–0.2% за круг);
- проскальзывание (~0.05–0.1% на ликвидных парах).

Формула:
```
net_pnl_pct = pnl_pct - fee_pct - slippage_pct
```

### 2. Проверка intrabar SL/TP

Стоп-лосс и тейк-профит проверяются по `low_price`/`high_price` свечи — то есть **внутри бара**. Это точнее, чем проверка только по `close_price`.

Ограничение: если в одном баре цена сходила и к SL, и к TP — мы **не знаем** порядок. Решение — приоритет у **SL** (консервативно). В реальной торговле это может занижать прибыль на волатильных барах, но защищает от иллюзорной доходности в бэктесте.

### 3. Используются только закрытые свечи

`oscillator_data` пишется только по закрытым барам (`process_update` в `crypto_allert`). Поэтому сигналы стратегии **не перерисовываются** — что рассчитано, то и записано.

### 4. Уникальность `kline_id` в `oscillator_data`

Стратегия опирается на 1:1 связь `kline_data.id ↔ oscillator_data.kline_id`. В вашей текущей схеме **нет UNIQUE constraint** на `oscillator_data.kline_id`. Проверьте:

```sql
SELECT kline_id, COUNT(*) FROM oscillator_data GROUP BY kline_id HAVING COUNT(*) > 1;
```

Если вернёт строки — есть дубли. Тогда:
- либо добавьте `UNIQUE INDEX` в `oscillator_data(kline_id)`,
- либо в `backtest.py` используйте `DISTINCT ON (kline_id)` при загрузке.

`backfill_oscillators.py` защищён от дублей (`exists(kline_id)`), но live-цикл `process_update` теоретически может создать дубль при двойном вызове `save_oscillator_data` на одной свече.

### 5. Изоляция от `crypto_allert`

`trade_executor` — **отдельный процесс** с собственной БД-сессией. Таблицы `te_*` не конфликтуют с существующими. `KlineData`/`OscillatorData` — read-only, никогда не пишутся.

### 6. Ограничение по max_open_positions

Глобальный счётчик по стратегии. Если `max_open_positions = 10` и уже открыто 10 позиций — новые сигналы **игнорируются** до закрытия хотя бы одной. Это защита от переконцентрации.

## Roadmap

- [x] Стратегия 1: score-based (LONG only)
- [ ] Стратегия 2: balanced (LONG + SHORT, funding/OI/LS фильтры)
- [ ] Стратегия 3: aggressive (высокая частота)
- [ ] Стратегия 4: contrarian (экстремумы funding/LS/каскады)
- [ ] Стратегия 5: momentum (EMA + OI + trailing stop)
- [ ] Реальная интеграция с биржей (Binance/OKX API)
- [ ] Учёт комиссий и funding costs в PnL
- [ ] Портфельные лимиты (общий риск по всем стратегиям)
- [ ] Telegram-алерты об открытии/закрытии
- [ ] API для frontend (список сделок, статистика)

## Лицензия

MIT

---

## Быстрый старт

```bash
cd /root/trade_executor

python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
nano .env                           # DB_HOST, DB_PASSWORD и т.д.

python init_db.py                   # таблицы + дефолтный конфиг
python backtest.py --reset          # прогон по всей истории
python stats.py                     # результаты
```

Дальше — настроить параметры в `strategy_confs`, снова запустить `backtest.py --reset`, сравнить. Когда результат устроит — запустить `service.py` (или через systemd) для обработки новых баров.

---

**Что прислать после первого запуска:**

1. Вывод `python stats.py` — проверю метрики (win rate, распределение close reasons, PnL по символам).
2. `psql -c "SELECT COUNT(*) FROM oscillator_data WHERE symbol='BTCUSDT'"` — проверю, что хватает истории.
3. `psql -c "SELECT kline_id, COUNT(*) FROM oscillator_data GROUP BY kline_id HAVING COUNT(*) > 1 LIMIT 5"` — проверю, нет ли дублей (важно для корректности).