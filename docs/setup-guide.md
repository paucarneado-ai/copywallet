# Copywallet Setup Guide

## 1. Anthropic API Key (for Claude Brain)

### Steps:
1. Go to `console.anthropic.com`
2. Sign up or log in
3. Go to **API Keys** in the left sidebar
4. Click **Create Key**
5. Name it "copywallet" and copy the key (starts with `sk-ant-...`)
6. Add credit — Claude Sonnet costs ~$3/1M input tokens. $5-10 is enough for weeks of paper trading.

### Add to config:
```yaml
anthropic_api_key: "sk-ant-your-key-here"
```

**Cost estimate**: ~50 market evaluations/hour × $0.002 each = ~$2-3/day

---

## 2. Telegram Bot

### Step 2a: Create the bot
1. Open Telegram and search for `@BotFather`
2. Send `/newbot`
3. BotFather asks for a name — type: `Copywallet Bot`
4. BotFather asks for a username — type: `copywallet_yourname_bot` (must end in `bot`, must be unique)
5. BotFather gives you a **token** like: `7123456789:AAF1234567890abcdefghijklmnop`
6. Copy that token

### Step 2b: Get your Telegram user ID
1. In Telegram, search for `@userinfobot`
2. Send it any message
3. It replies with your **user ID** — a number like `123456789`
4. Copy that number

### Add to config:
```yaml
telegram_bot_token: "7123456789:AAF1234567890abcdefghijklmnop"
telegram_user_id: "123456789"
```

### Step 2c: Start your bot
1. In Telegram, search for your bot's username (the one you chose in step 2a)
2. Press **Start** — this is required before the bot can send you messages

---

## 3. Polymarket Account + Wallet

### Step 3a: Create Polymarket account
1. Go to `polymarket.com`
2. Sign up with email or connect a wallet (MetaMask, Coinbase Wallet, etc.)
3. Complete any verification steps

### Step 3b: Create a DEDICATED wallet for the bot
**CRITICAL: Do NOT use your main crypto wallet. Create a new one specifically for the bot.**

Option A — MetaMask (recommended for beginners):
1. Install MetaMask browser extension
2. Click your account icon → **Create Account**
3. Name it "Copywallet Bot"
4. Copy the wallet address (starts with `0x...`)
5. **Export the private key**: Click the 3 dots → Account Details → Show Private Key
6. Copy the private key (never share this with anyone)

Option B — Generate a wallet via command line:
```bash
python -c "from eth_account import Account; a = Account.create(); print(f'Address: {a.address}\nPrivate Key: {a.key.hex()}')"
```
(Requires: `pip install eth-account`)

### Step 3c: Get your Polymarket proxy wallet address
1. Log into Polymarket with your dedicated wallet
2. Go to your profile page
3. The URL will be: `polymarket.com/profile/0xYourProxyAddress`
4. That `0xYourProxyAddress` is your **proxy wallet** — this is what goes in config

### Step 3d: Fund the wallet (for live trading later)
**Skip this for paper trading — you don't need funds to paper trade.**

When ready for live trading:
1. Buy USDC on an exchange (Coinbase, Binance, etc.)
2. Withdraw USDC to **Polygon network** to your dedicated wallet address
3. Start with $100-300 — scale up only after the bot proves profitable
4. Deposit USDC into Polymarket via their deposit flow

### Add to config:
```yaml
polymarket:
  private_key: "your-private-key-hex-without-0x-prefix"
  proxy_wallet: "0xYourPolymarketProxyAddress"
```

---

## 4. Create Your Config File

Copy the template and fill in your values:

```bash
cp config.example.yaml config.yaml
```

Then edit `config.yaml` with your keys. At minimum for paper trading:

```yaml
mode: paper

# Required for Claude Brain (Phase 2)
anthropic_api_key: "sk-ant-your-key"

# Required for Telegram alerts
telegram_bot_token: "your-bot-token"
telegram_user_id: "your-user-id"

# Not needed for paper trading — fill when going live
polymarket:
  private_key: ""
  proxy_wallet: ""

# Enable copy trading (Phase 1)
copy_trading:
  enabled: true
  categories:
    - CRYPTO

# Enable Claude Brain (Phase 2) — optional for now
claude_brain:
  enabled: false  # set to true when you have Anthropic API key with credits
```

---

## 5. Run the Bot

```bash
# From the project directory:
python -m src.main

# Or with a custom config path:
python -m src.main path/to/config.yaml
```

### What to expect:
1. Bot starts in paper mode with $10,000 virtual bankroll
2. Runs wallet discovery (takes 1-2 minutes on first run)
3. Starts monitoring leader wallets every 2 seconds
4. Dashboard available at http://127.0.0.1:8080
5. Telegram alerts when trades are executed

### Common first-run issues:
- `ModuleNotFoundError` → run `pip install -r requirements.txt`
- `FileNotFoundError: config.yaml` → copy config.example.yaml to config.yaml
- Telegram bot not sending messages → make sure you pressed "Start" in Telegram
- No trades happening → normal! The bot waits for leaders to make trades that pass all 9 filters

---

## Security Reminders

- `config.yaml` is in `.gitignore` — it will NOT be committed to git
- Never share your private key with anyone or any service
- The bot's monitoring is read-only (public APIs) — private key is only used for live trading
- Start with paper trading. Always.
