"""
Force a TEST ETH signal to Telegram — verifies the whole pipeline is working.
"""
import sys
sys.path.insert(0, r'D:\Final')
import btc_apex_v5 as bot

# ── Lower all gates to guarantee a signal fires ──────────────────────
bot.MIN_1D    = 10
bot.MIN_4H    = 10
bot.MIN_1H    = 10
bot.MIN_15M   = 10
bot.MIN_TOTAL = 10
bot.MIN_RR    = 0.5

print("=" * 60)
print("  ETH APEX — TELEGRAM TEST SIGNAL")
print("=" * 60)

print("\nConnecting to Binance (Frankfurt)...")
ex = bot.connect()

print("Fetching ETH/USDT:USDT live data...")
df15 = bot.ind(bot.fetch(ex, bot.TF_15M))
df1h = bot.ind(bot.fetch(ex, bot.TF_1H))
df4h = bot.ind(bot.fetch(ex, bot.TF_4H))
df1d = bot.ind(bot.fetch(ex, bot.TF_1D))

price          = float(df15.iloc[-1].close)
sent           = bot.get_sentiment()
funding, oi    = bot.fetch_derivative_data(ex)
forecast_slope = bot.linear_regression_forecast(df15.close.tolist(), 15)
ml_pred, ml_label = bot.ml_ensemble_forecast(df15, lookback=60)

# Analyse both directions
sc1d_long, r1d_long, sc1d_short, r1d_short = bot.analyze_1d(df1d)

sc4h_long,  r4h_long  = bot.analyze_4h(df4h,  "LONG")
sc1h_long,  r1h_long  = bot.analyze_1h(df1h,  "LONG")
sc15_long,  r15_long  = bot.analyze_15m(df15, "LONG")
final_long = round(sc1d_long*0.20 + sc4h_long*0.25 + sc1h_long*0.25 + sc15_long*0.30)

sc4h_short, r4h_short = bot.analyze_4h(df4h,  "SHORT")
sc1h_short, r1h_short = bot.analyze_1h(df1h,  "SHORT")
sc15_short, r15_short = bot.analyze_15m(df15, "SHORT")
final_short = round(sc1d_short*0.20 + sc4h_short*0.25 + sc1h_short*0.25 + sc15_short*0.30)

# Pick strongest direction
if final_long >= final_short:
    direction = "LONG"
    sc1d, r1d = sc1d_long, r1d_long
    sc4h, r4h = sc4h_long, r4h_long
    sc1h, r1h = sc1h_long, r1h_long
    sc15, r15r = sc15_long, r15_long
    final = final_long
else:
    direction = "SHORT"
    sc1d, r1d = sc1d_short, r1d_short
    sc4h, r4h = sc4h_short, r4h_short
    sc1h, r1h = sc1h_short, r1h_short
    sc15, r15r = sc15_short, r15_short
    final = final_short

print(f"\nETH Price : ${price:.2f}")
print(f"Direction : {direction}")
print(f"Scores    : 1D={sc1d}  4H={sc4h}  1H={sc1h}  15m={sc15}  Blend={final}")
print(f"ML        : {ml_label}")
print(f"F&G       : {sent.get('value')} {sent.get('bias')}")

# Build signal
sig = bot.build_signal(
    direction, df15, sc1d, sc4h, sc1h, sc15, sent,
    r1d, r4h, r1h, r15r, forecast_slope,
    5, 2, 70.0, ml_pred, ml_label, funding, oi
)

if sig:
    # Label as TEST
    sig["time"] = sig["time"] + " [TEST]"
    print(f"\nSignal built:")
    print(f"  Direction  : {sig['dir']} ({sig['tier']}-Grade)")
    print(f"  Confidence : {sig['conf']}%")
    print(f"  Entry      : ${sig['entry']:.2f}")
    print(f"  Stop Loss  : ${sig['sl']:.2f}")
    print(f"  Take Profit: ${sig['tp']:.2f}")
    print(f"  R:R        : {sig['rr']}:1")
    print("\nSending to Telegram...")
    bot.send_signal_tg(sig, r1d, r4h, r1h)
    print("SUCCESS — check your Telegram now!")
else:
    print("\nR:R too low even with test settings — sending raw Telegram ping instead...")
    import requests
    msg = (
        f"*ETH APEX v5.0 — TEST PING*\n\n"
        f"Bot is LIVE and connected.\n"
        f"ETH Price: `${price:.2f}`\n"
        f"Direction: `{direction}` | Scores: 1D:{sc1d} 4H:{sc4h} 1H:{sc1h} 15m:{sc15}\n"
        f"ML: {ml_label}\n"
        f"F&G: {sent.get('value')} — {sent.get('bias')}\n\n"
        f"_Waiting for a high-confidence A+ setup to fire a real signal._"
    )
    requests.post(
        f"https://api.telegram.org/bot{bot.TG_TOKEN}/sendMessage",
        json={"chat_id": bot.TG_CHAT, "text": msg, "parse_mode": "Markdown"},
        timeout=10
    )
    print("Telegram ping sent! Check your chat.")
