# Cloud Storage

The bot backs up its trained ML models to Hugging Face Hub after every Sunday retrain. This solves a few real problems: if your machine dies, you don't lose months of trained models. If you want to run the bot on a different machine, it downloads everything automatically on first startup. And it means you never have to manually manage model files across machines.

Everything about this is automatic once you set it up once.

---

## What gets backed up

The models folder contains `.pkl` files for every trained pair:

- `xgb_EURUSD.pkl` and `rf_EURUSD.pkl` (and the same for every other pair)
- `xgb_trading_model.pkl` and `rf_trading_model.pkl` (the global fallback models)

The data folder backup includes:
- `manual_trade_features.csv` — the running record of your actual closed trades used for retraining
- `user_pairs.txt` — any pairs you've added through Telegram

Logs are not backed up — they're machine-specific and small enough not to matter.

---

## How to set it up

**Step 1 — Create a Hugging Face account**

Go to huggingface.co and sign up. It's free and takes two minutes.

**Step 2 — Create a private model repository**

After logging in, click the + icon in the top right and select New Model. Name it `forex-ai-bot-models`, set visibility to Private, and click Create. Your repository ID will be `YourUsername/forex-ai-bot-models`.

**Step 3 — Generate an access token**

Go to huggingface.co/settings/tokens. Click New Token, give it a name like `forex-bot`, set the type to Write, and click Create Token. Copy the token — it starts with `hf_` and you won't be able to see it again after you leave the page.

**Step 4 — Add your credentials to .env**

Open `.env` in the bot folder and add these two lines:

```
HF_TOKEN=hf_your_token_here
HF_REPO_ID=YourUsername/forex-ai-bot-models
```

**Step 5 — Install the library**

```bash
venv\Scripts\activate
pip install huggingface_hub
```

That's all the setup. The bot handles everything from here automatically.

---

## What happens automatically

**On startup:** The bot checks whether your local `models/` folder has all expected files. If anything is missing — because it's a fresh install, a new machine, or something got deleted — it downloads the missing files from Hugging Face before loading the models. If everything is already there locally, it skips the download entirely. No unnecessary transfers.

**After Sunday retrain:** Once training finishes and all models are written to disk, the bot uploads every `.pkl` file and the training data CSVs to Hugging Face. You'll see the upload progress printed in the console and get a Telegram notification confirming the backup completed.

---

## Restoring on a new machine

If you move to a new machine or set up a fresh copy of the bot:

1. Copy the project folder across (or just the code — not the models)
2. Set up your `.env` with all credentials including `HF_TOKEN`
3. Run `pip install -r requirements.txt`
4. Start the bot with `start_bot.bat`

On the first startup, the bot will see that `models/` is empty and download everything from Hugging Face automatically. By the time it's finished loading, the models are ready and trading begins as if nothing changed.

---

## Keeping your token safe

Your Hugging Face token gives anyone who has it write access to your repositories. Treat it like a password:

- Never paste it into a chat, email, or forum post
- Keep it in `.env` only — that file is in `.gitignore` and won't be committed if you ever push code to GitHub
- If you think a token was exposed, go to huggingface.co/settings/tokens immediately, delete it, and create a new one. Update `.env` with the new token.

---

## Storage limits

Hugging Face's free tier gives you unlimited storage for model repositories up to a reasonable file size. A trained XGBoost model with 400 trees is typically 5–15MB. A Random Forest with 300 trees runs 20–60MB. With 8 pairs and 2 models each plus 2 global models, you're looking at maybe 400–600MB total for the models folder. That's well within free tier limits.

The training data CSVs are a few megabytes at most and grow slowly. You won't hit any limits for years at the current scale.
