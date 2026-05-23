import requests, ccxt, datetime

# Get real live BTC price from Bitget
ex = ccxt.bitget({"enableRateLimit": True, "options": {"defaultType": "swap"}})
ticker = ex.fetch_ticker("BTC/USDT:USDT")
price  = ticker["last"]
print(f"Live BTC Price: ${price:,.1f}")

entry = round(price, 1)
sl    = round(price - 850, 1)
tp    = round(price + 2200, 1)
rr    = round((tp - entry) / (entry - sl), 2)
now   = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

token = "8775276870:AAGABvQ6PwtRgGPNbk3V4YX_A0eVXxpiWyo"
chat  = "998659643"

msg = (
    "\U0001f7e2 *BTC LONG \u2014 TREND PULLBACK*\n"
    "\U0001f3c6 Grade: *A+*   \U0001f525 Confidence: *78%*\n\n"
    f"\U0001f4cd `TRENDING_UP`   \U0001f550 `NEW YORK`   \U0001f552 `{now}`\n\n"
    "\U0001f7e2 Sentiment: `FEAR (31)` \u2014 Fear\n"
    "\U0001f9e0 SMC: `FVG + SWEEP`\n\n"
    f"\U0001f3af Entry  `${entry:,.1f}`\n"
    f"\U0001f6d1 Stop   `${sl:,.1f}`  (STD)\n"
    f"\U0001f4b0 Target `${tp:,.1f}`\n"
    f"\u2696\ufe0f R:R    `{rr}:1`\n"
    "\u2705 Checks `8/10`\n\n"
    "\u26a0\ufe0f _This is a TEST SIGNAL to confirm delivery._\n"
    "_Real signals fire automatically when all checks pass._\n"
    "\U0001f512 _Anti-repaint: confirmed candle only._"
)

r = requests.post(
    f"https://api.telegram.org/bot{token}/sendMessage",
    json={"chat_id": chat, "text": msg, "parse_mode": "Markdown"},
    timeout=15
)
data = r.json()
print(f"Telegram: {r.status_code} ok={data.get('ok')}")
if data.get("ok"):
    print(f"Message ID: {data['result']['message_id']} — CHECK YOUR TELEGRAM NOW")
else:
    print(f"Error: {data}")
