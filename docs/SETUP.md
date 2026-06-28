# Setup & Installation

Getting this running takes about 20–30 minutes if you've never done anything like it before. Less if you have. The steps below cover everything from scratch, including the parts most guides skip over.

---

## What you need before starting

**MetaTrader 5** — Download from your broker's website. This bot was built and tested on Exness, but it'll work on any MT5 broker as long as the symbols match (the bot handles broker-specific symbol suffixes automatically). Make sure MT5 is open and logged into your account before starting the bot.

**Python 3.11 or 3.12** — The bot was developed on Python 3.14, but 3.11 or 3.12 are more stable for the ML libraries. Download from python.org. During installation, tick the box that says "Add Python to PATH" — easy to miss, painful to fix later.

**A Telegram account** — You'll need to create a bot through Telegram's BotFather. It only takes two minutes and gives you a way to monitor trades from your phone anywhere in the world.

---

## Step 1 — Clone or copy the project

If you're working directly from the folder, skip this. Otherwise, copy the entire `forex_ai_bot` folder somewhere on your machine. The bot stores data in paths relative to its own location, so just make sure the folder structure stays intact.

---

## Step 2 — Create a virtual environment

Open a terminal (Command Prompt or PowerShell) inside the project folder:

```bash
python -m venv venv
venv\Scripts\activate
```

You'll know it worked when you see `(venv)` at the start of your terminal prompt. Always make sure this is active before installing packages or running the bot.

---

## Step 3 — Install dependencies

```bash
pip install -r requirements.txt
```

This installs everything: MetaTrader5 connector, pandas, scikit-learn, XGBoost, Streamlit, and all the rest. It might take a few minutes the first time. If anything fails, it's usually a version conflict with the ML libraries — try `pip install --upgrade pip` first, then run the install again.

---

## Step 4 — Set up your credentials

The project includes a `.env` file that holds all your private credentials. Open it in any text editor — Notepad works fine:

```
MT5_LOGIN=your_account_number
MT5_PASSWORD=your_mt5_password
MT5_SERVER=your_broker_server_name

TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id

PROP_FIRM_MODE=false

NGROK_AUTH_TOKEN=your_ngrok_token

HF_TOKEN=your_huggingface_token
HF_REPO_ID=YourUsername/forex-ai-bot-models
```

**Getting your MT5 details:** these are the same login you use for the MT5 app. The server name is visible in MT5 under File → Open Account. It'll look something like `Exness-MT5Trial9`.

**Creating a Telegram bot:** open Telegram, search for `@BotFather`, send `/newbot`, follow the steps. You'll get a token that looks like `1234567890:AAF...`. For your chat ID, search for `@userinfobot` and it'll tell you your ID after you send it any message.

**Hugging Face token:** go to huggingface.co/settings/tokens, create a new token with Write access. This is used to back up your trained models to the cloud — see [Cloud Storage](CLOUD_STORAGE.md) for more on this.

---

## Step 5 — Start the bot

Double-click `start_bot.bat`. This launches everything at once:

- The trading bot in its own console window
- The Streamlit dashboard at `http://localhost:8501`
- An ngrok tunnel so you can access the dashboard from your phone

The bot will automatically detect which pairs are available on your broker, load any existing ML models, and start scanning. If it's a Sunday before market open, it'll ask whether you want to run the weekly retrain first.

---

## First run notes

The first time you start, there are no trained models yet. The bot will fall back to pure rule-based ICT signals until Sunday's auto-retrain runs. This is normal — it still trades, just without the AI layer on top. After the first Sunday retrain it switches to the full ensemble automatically.

You won't see any trades immediately either. The signal scanner runs every 30 seconds, and the bot only trades during its scheduled session windows. Depending on the time of day and which pairs are active, it might scan for an hour before finding anything worth taking.

---

## Updating the pair list

You can add or remove pairs at any time through Telegram without restarting the bot:

```
/addpair EURAUD
/removepair BTCUSD
```

The bot picks up the change on the next 30-second cycle. New pairs get their own per-pair models trained on the next Sunday retrain.

---

## Running without ngrok

If you don't need remote phone access, you can skip the ngrok setup. The dashboard still runs locally at `http://localhost:8501`. Just leave `NGROK_AUTH_TOKEN` blank in your `.env` and the bot will warn you but continue normally.
