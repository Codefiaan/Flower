# Flower Terminal

A personal, Bloomberg-style **information terminal** for your stocks. It covers market overview, portfolio, watchlist, screener, financial statements, valuation history, news and an AI analysis of annual reports.
**Information only:** there is no trading and no broker connection, and nothing here is investment advice.

The app has two front ends over the same data:

| Page | URL | Style |
|---|---|---|
| **Terminal** | `/` | Keyboard-first, black and amber, dense tables, command line (`SAP.DE <GO>`, `AAPL FA`), F-keys |
| **Overview** | `/overview` | Clean, minimal mobile-first list of all your stocks with a portfolio chart (light/dark) |
| Settings | `/settings` | LLM provider and API key, web-search provider |

## Quick start

### Windows (local)
1. Install Python 3.11+ from python.org and tick "Add python.exe to PATH".
2. Double-click `start.bat`. The first start creates `.venv` and installs the dependencies.
3. The browser opens http://127.0.0.1:8000.

### Linux / macOS (local)
```bash
./start.sh            # then open http://127.0.0.1:8000
```

### Try it without internet
Set `FLOWER_DEMO=1` (in `.env` or the environment). The app then serves synthetic sample data, marked "DEMO DATA".

## Configuration

Copy `.env.example` to `.env`. The most important values:

| Variable | Default | Meaning |
|---|---|---|
| `BASE_CURRENCY` | `EUR` | Currency for portfolio totals (converted with the current FX rate) |
| `FLOWER_DEMO` | `0` | `1` = synthetic data, no network |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | Listen address |
| `FLOWER_USER` / `FLOWER_PASSWORD` | empty | Login (HTTP basic auth). **Required** on a server |
| `SEC_CONTACT` | example address | Contact e-mail sent to SEC EDGAR, which their access policy requires |

The LLM and search API keys are entered on the **Settings** page. They are stored in `data/flower.db` on the server and are never sent back to the browser.

## Features

- **Market (F1):** major indices, VIX, US 10Y, EUR/USD, gold, oil and bitcoin with sparklines, plus a portfolio summary, watchlist and top news.
- **Portfolio (F2):** lots with buy date and buy price (in the stock's trading currency). Shows P/L, day change, weights, allocation by sector and currency, and a value history replayed from buy dates. CSV import and export.
- **Watchlist (F3):** P/E, 52-week position, RSI, signal light, and your target buy price. The row turns green once the price is at or below the target.
- **Screener (F4):** Yahoo's equity screener with presets ("P/E below 15", "Dividend > 3%", "Quality", "Large caps cheap") and filters for region, sector, P/E, dividend, ROE and market cap.
- **News (F5):** combined news for all your symbols.
- **Security pages:** `DES` overview (candles, SMA50/200, volume, RSI, key figures, signal check), `GP` chart, `FA` statements (annual and quarterly), `EV` P/E and P/S at each fiscal-year end against today, `CN` news and upcoming earnings/dividend dates, `AI` analysis.
- **Signal check:** a transparent checklist covering trend, golden/death cross, RSI, drawdown, P/E against the stock's own history, forward P/E, PEG, debt, FCF yield, growth, analyst target and next earnings. Every point shows its value and a one-line explanation. It is meant to structure your research, not to make decisions for you.
- **AI analysis (bring your own key):** for US listings Flower pulls the latest 10-K/20-F and 10-Q from **SEC EDGAR**. For everything else it **searches the web** for the annual-report PDF. It extracts the relevant passages (outlook, risks, segments, cash flow) and streams an analysis from your LLM, with citations to the source documents. There is a balanced, a value (Graham) and a growth lens, plus a chat to ask follow-up questions. Results are cached per stock.

### Data sources
| Source | Used for | Key |
|---|---|---|
| Yahoo Finance via [`yfinance`](https://github.com/ranaroussi/yfinance) | Quotes, history, key figures, statements (about 4–5 years), news, screener, FX | none |
| SEC EDGAR XBRL + filings | Long EPS/revenue history for US stocks (P/E history), 10-K/10-Q documents | none |
| DuckDuckGo HTML / Tavily / Brave | Finding annual reports of non-US companies | optional |
| DeepSeek, OpenAI, Anthropic, Gemini, OpenRouter, Groq, Ollama or any OpenAI-compatible API | AI analysis | yours |

`yfinance` is an unofficial scraper of Yahoo's website. It is fine for personal use, but it can break when Yahoo changes something (then `pip install -U yfinance` usually helps) and Yahoo may rate-limit heavy use. Data can be delayed or wrong.

## Running on your Ubuntu server

```bash
sudo adduser --system --group --home /opt/flower flower
sudo git clone <this repo> /opt/flower && sudo chown -R flower:flower /opt/flower
cd /opt/flower
sudo -u flower python3 -m venv .venv
sudo -u flower .venv/bin/pip install -r requirements.txt
sudo -u flower cp .env.example .env
sudo -u flower nano .env      # set FLOWER_USER, FLOWER_PASSWORD, SEC_CONTACT
sudo cp deploy/flower.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now flower
```
Then put nginx with HTTPS in front (`deploy/nginx.conf`, certificate via `certbot --nginx`).
The app keeps listening on `127.0.0.1:8000`. It refuses to start on a public address without a password, and it refuses proxied requests while no login is set.

To move your local data to the server, use **Export CSV** in the Terminal portfolio view and then **Import CSV** on the server. Alternatively, copy `data/flower.db`, which also holds the watchlist and settings.

## Development

```bash
pip install -r requirements-dev.txt
pytest                         # runs fully offline in demo mode
FLOWER_DEMO=1 python -m backend
```
API docs: http://127.0.0.1:8000/api/docs

Project layout: `backend/` (FastAPI, providers, analysis, AI pipeline), `frontend/` (plain HTML/CSS/JS, no build step; charts by TradingView lightweight-charts, Apache-2.0, vendored), `deploy/` (systemd, nginx), `tests/`.
