# -*- coding: utf-8 -*-
"""
DIAGNOSTIC SCRIPT — Run this FIRST to test everything works.
Shows exactly what score each screen gets and why signals fire/don't fire.
Also tests Telegram connectivity.

Run: python diagnose.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Set your Telegram credentials here for local testing
os.environ["TG_TOKEN"] = "8775276870:AAGABvQ6PwtRgGPNbk3V4YX_A0eVXxpiWyo"
os.environ["TG_CHAT"]  = "998659643"

from btc_apex_v5 import (
    connect, fetch, ind, ichimoku, analyze_1d, analyze_4h, analyze_1h, analyze_15m,
    build_signal, get_sentiment, get_fib_levels, weinstein_stage, wyckoff_phase,
    TF_15M, TF_1H, TF_4H, TF_1D, MIN_1D, MIN_4H, MIN_1H, MIN_15M, MIN_TOTAL, MIN_APLUS,
    s, BGN, BRD, YL, GY, CY, WH, GN, MG, BD, RS, BCY
)
import requests

W = 70

def tg_test(msg):
    token = os.environ["TG_TOKEN"]
    chat  = os.environ["TG_CHAT"]
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": msg, "parse_mode": "Markdown"},
            timeout=10)
        return r.status_code == 200, r.json()
    except Exception as e:
        return False, str(e)

def bar(score, need, width=20):
    filled = int(score / 100 * width)
    ok = score >= need
    col = BGN if ok else BRD
    bar = "█"*filled + "░"*(width-filled)
    status = "✅ PASS" if ok else f"❌ FAIL (need {need})"
    return f"{s(bar, col)} {s(str(score)+'/100', col, bold=True)} {status}"

def sep(title=""):
    line = "─" * (W - 2)
    if title:
        pad = (W - len(title) - 4) // 2
        print(s(f"\n  {'─'*pad} {title} {'─'*pad}\n", CY))
    else:
        print(s(f"  {line}", GY))

def main():
    print()
    print(s("  ╔" + "═"*(W-4) + "╗", CY))
    print(s("  ║" + " BTC APEX v5.0 — FULL DIAGNOSTIC".center(W-4) + "║", BCY, bold=True))
    print(s("  ╚" + "═"*(W-4) + "╝", CY))
    print()

    # ── 1. Test Telegram ─────────────────────────────────────────────
    sep("TELEGRAM TEST")
    print(s("  Sending test message...", GY), end="", flush=True)
    ok, resp = tg_test(
        "🔧 *BTC APEX v5.0 — Diagnostic Test*\n"
        "✅ Telegram connection working!\n"
        "_Bot is alive and sending messages correctly._"
    )
    if ok:
        print(s(" ✅ Telegram OK — check your phone!", BGN, bold=True))
    else:
        print(s(f" ❌ Telegram FAILED: {resp}", BRD, bold=True))
        print(s("  → Check TG_TOKEN and TG_CHAT are correct", YL))

    # ── 2. Connect exchange ───────────────────────────────────────────
    sep("EXCHANGE CONNECTION")
    print(s("  Connecting to Bitget...", GY), end="", flush=True)
    try:
        ex = connect()
        print(s(" ✅ Connected", BGN))
    except Exception as e:
        print(s(f" ❌ Failed: {e}", BRD))
        return

    # ── 3. Fetch data ─────────────────────────────────────────────────
    sep("MARKET DATA")
    dfs = {}
    for tf in [TF_15M, TF_1H, TF_4H, TF_1D]:
        print(s(f"  Fetching {tf}...", GY), end="", flush=True)
        try:
            df = ind(fetch(ex, tf))
            dfs[tf] = df
            print(s(f" ✅ {len(df)} candles", BGN))
        except Exception as e:
            print(s(f" ❌ {e}", BRD))
            return

    df15, df1h, df4h, df1d = dfs[TF_15M], dfs[TF_1H], dfs[TF_4H], dfs[TF_1D]

    price = float(df15.iloc[-1].close)
    r15   = df15.iloc[-2]
    r4h   = df4h.iloc[-2]
    r1d   = df1d.iloc[-2]

    print()
    print(s(f"  Current BTC Price: ${price:,.1f}", WH, bold=True))
    print(s(f"  15m RSI: {r15.rsi:.1f}  MACD hist: {r15.macdh:.1f}  ADX: {r15.adx:.1f}", GY))
    print(s(f"  4H  RSI: {r4h.rsi:.1f}  MACD hist: {r4h.macdh:.1f}  ADX: {r4h.adx:.1f}", GY))
    print(s(f"  1D  RSI: {r1d.rsi:.1f}  MACD hist: {r1d.macdh:.1f}  ADX: {r1d.adx:.1f}", GY))

    # ── 4. Sentiment ──────────────────────────────────────────────────
    sep("SENTIMENT")
    sent = get_sentiment()
    mc = BGN if sent["mod"] > 0 else (BRD if sent["mod"] < -3 else YL)
    print(s(f"  Fear & Greed: {sent['value']}/100 — {sent['bias']} ({sent['label']})", mc))
    mod_word = "boosts LONG" if sent["mod"] > 0 else ("hurts LONG" if sent["mod"] < -3 else "neutral")
    print(s(f"  Signal mod: {sent['mod']:+d} ({mod_word})", GY))

    # ── 5. Fibonacci ──────────────────────────────────────────────────
    sep("AUTO-FIBONACCI (4H)")
    fib = get_fib_levels(df4h)
    if fib:
        print(s(f"  Swing High: ${fib['hi']:,.1f}  Swing Low: ${fib['lo']:,.1f}", GY))
        levels = [("23.6%", fib["236"]), ("38.2%", fib["382"]),
                  ("50.0%", fib["500"]), ("61.8%", fib["618"]), ("78.6%", fib["786"])]
        for name, lv in levels:
            dist = abs(price - lv) / price * 100
            close = dist < 0.8
            col = BGN if close else GY
            arrow = "← PRICE HERE" if close else ""
            print(s(f"  {name}: ${lv:,.1f}  (dist {dist:.2f}%) {arrow}", col))

    # ── 6. Weinstein Stage ────────────────────────────────────────────
    sep("WEINSTEIN STAGE ANALYSIS")
    stage1d = weinstein_stage(df1d)
    stage4h = weinstein_stage(df4h)
    stage_names = {1:"Stage 1 (Base)", 2:"Stage 2 (Markup) ✅ BUY",
                   3:"Stage 3 (Top)", 4:"Stage 4 (Markdown) ✅ SHORT", 0:"Unclear"}
    sc1d = BGN if stage1d == 2 else (BRD if stage1d == 4 else YL)
    sc4h = BGN if stage4h == 2 else (BRD if stage4h == 4 else YL)
    print(s(f"  1D: {stage_names.get(stage1d, '?')}", sc1d))
    print(s(f"  4H: {stage_names.get(stage4h, '?')}", sc4h))

    # ── 7. Wyckoff Phase ──────────────────────────────────────────────
    sep("WYCKOFF PHASE (4H)")
    phase = wyckoff_phase(df4h)
    pc = BGN if phase in ("ACCUMULATION","MARKUP") else (BRD if phase in ("DISTRIBUTION","MARKDOWN") else YL)
    print(s(f"  Phase: {phase}", pc))

    # ── 8. SCREEN 1: 1D Analysis ──────────────────────────────────────
    sep("SCREEN 1 — 1D TIDE (Direction Filter)")
    sc1d_long, reasons1d_long, sc1d_short, reasons1d_short = analyze_1d(df1d)
    if sc1d_long >= sc1d_short and sc1d_long >= MIN_1D:
        dir1d = "LONG"
        sc1d_score = sc1d_long
        reasons1d = reasons1d_long
    elif sc1d_short > sc1d_long and sc1d_short >= MIN_1D:
        dir1d = "SHORT"
        sc1d_score = sc1d_short
        reasons1d = reasons1d_short
    else:
        dir1d = "NEUTRAL"
        sc1d_score = max(sc1d_long, sc1d_short)
        reasons1d = reasons1d_long if sc1d_long >= sc1d_short else reasons1d_short

    d1c = BGN if dir1d=="LONG" else (BRD if dir1d=="SHORT" else YL)
    print(s(f"  Direction: {dir1d}", d1c, bold=True))
    print(f"  Score:     {bar(sc1d_score, MIN_1D)}")
    print()
    print(s("  Reasons:", GY))
    for r in reasons1d:
        ok_col = BGN if any(w in r for w in ["above","Golden","Stage 2","rising","bull","up"]) else BRD
        print(s(f"    {'✅' if any(w in r for w in ['above','Golden','Stage 2','rising','bull','up']) else '⚡'} {r}", GY))

    if dir1d == "NEUTRAL":
        print()
        print(s("  ⚠️  1D is NEUTRAL — no signals will fire until 1D picks a direction", YL))

    # ── 9. SCREEN 2: 4H Analysis ──────────────────────────────────────
    sep("SCREEN 2 — 4H SETUP (Setup Finder)")
    if dir1d == "NEUTRAL":
        print(s("  Skipped — 1D is NEUTRAL", GY))
        sc4h_score, reasons4h = 0, []
    else:
        sc4h_score, reasons4h = analyze_4h(df4h, dir1d)
        print(f"  Direction being tested: {s(dir1d, d1c, bold=True)}")
        print(f"  Score: {bar(sc4h_score, MIN_4H)}")
        print()
        print(s("  Reasons:", GY))
        for r in reasons4h:
            print(s(f"    ✅ {r}", GY))
        if not reasons4h:
            print(s("    (no specific setup triggers found)", YL))

    # ── 9.5. SCREEN 2.5: 1H Analysis ──────────────────────────────────
    sep("SCREEN 2.5 — 1H SETUP (Setup Finder)")
    if dir1d == "NEUTRAL":
        print(s("  Skipped — 1D is NEUTRAL", GY))
        sc1h_score, reasons1h = 0, []
    else:
        sc1h_score, reasons1h = analyze_1h(df1h, dir1d)
        print(f"  Direction being tested: {s(dir1d, d1c, bold=True)}")
        print(f"  Score: {bar(sc1h_score, MIN_1H)}")
        print()
        print(s("  Reasons:", GY))
        for r in reasons1h:
            print(s(f"    ✅ {r}", GY))
        if not reasons1h:
            print(s("    (no specific setup triggers found)", YL))

    # ── 10. SCREEN 3: 15m Analysis ────────────────────────────────────
    sep("SCREEN 3 — 15m ENTRY (Entry Trigger)")
    if dir1d == "NEUTRAL" or sc4h_score < MIN_4H or sc1h_score < MIN_1H:
        msg = "Skipped — 1D NEUTRAL" if dir1d == "NEUTRAL" else f"Skipped — 4H score {sc4h_score} < {MIN_4H} or 1H score {sc1h_score} < {MIN_1H}"
        print(s(f"  {msg}", GY))
        sc15_score, reasons15 = 0, []
    else:
        sc15_score, reasons15 = analyze_15m(df15, dir1d)
        print(f"  Score: {bar(sc15_score, MIN_15M)}")
        print()
        print(s("  Reasons:", GY))
        for r in reasons15:
            print(s(f"    ✅ {r}", GY))
        if not reasons15:
            print(s("    (no entry trigger on current 15m candle)", YL))

    # ── 11. Final Score ───────────────────────────────────────────────
    sep("FINAL SIGNAL SCORE")
    final = round(sc1d_score*0.20 + sc4h_score*0.25 + sc1h_score*0.25 + sc15_score*0.30)
    print(f"  1D ({sc1d_score}) × 0.20 + 4H ({sc4h_score}) × 0.25 + 1H ({sc1h_score}) × 0.25 + 15m ({sc15_score}) × 0.30 = {s(str(final), WH, bold=True)}")
    print()
    print(f"  Total score: {bar(final, MIN_TOTAL, width=25)}")
    print()

    if final >= MIN_APLUS and dir1d != "NEUTRAL" and sc4h_score >= MIN_4H and sc1h_score >= MIN_1H and sc15_score >= MIN_15M:
        print(s("  🏆 A+ SIGNAL WOULD FIRE!", BGN, bold=True))
    elif final >= MIN_TOTAL and dir1d != "NEUTRAL" and sc4h_score >= MIN_4H and sc1h_score >= MIN_1H and sc15_score >= MIN_15M:
        print(s("  ⚡ A SIGNAL WOULD FIRE!", BGN, bold=True))
    else:
        print(s("  ❌ No signal this scan. Missing:", YL))
        if dir1d == "NEUTRAL": print(s("     • 1D direction unclear", YL))
        if sc1d_score < MIN_1D: print(s(f"     • 1D score {sc1d_score} < {MIN_1D} needed", YL))
        if sc4h_score < MIN_4H: print(s(f"     • 4H score {sc4h_score} < {MIN_4H} needed", YL))
        if sc1h_score < MIN_1H: print(s(f"     • 1H score {sc1h_score} < {MIN_1H} needed", YL))
        if sc15_score < MIN_15M: print(s(f"     • 15m score {sc15_score} < {MIN_15M} needed", YL))
        if final < MIN_TOTAL: print(s(f"     • Final score {final} < {MIN_TOTAL} minimum", YL))

    # ── 12. Summary to Telegram ───────────────────────────────────────
    sep("SENDING STATUS TO TELEGRAM")
    d1c_em = "🟢" if dir1d == "LONG" else ("🔴" if dir1d == "SHORT" else "⚪")
    status_msg = (
        f"📊 *BTC APEX Diagnostic Report*\n"
        f"💰 Price: `${price:,.1f}`\n\n"
        f"*Screen Scores:*\n"
        f"{d1c_em} 1D Tide: `{sc1d_score}/100` (need {MIN_1D}) — {dir1d}\n"
        f"{'✅' if sc4h_score>=MIN_4H else '❌'} 4H Wave: `{sc4h_score}/100` (need {MIN_4H})\n"
        f"{'✅' if sc1h_score>=MIN_1H else '❌'} 1H Wave: `{sc1h_score}/100` (need {MIN_1H})\n"
        f"{'✅' if sc15_score>=MIN_15M else '❌'} 15m Entry: `{sc15_score}/100` (need {MIN_15M})\n\n"
        f"*Final: `{final}/100` (need {MIN_TOTAL} for signal)*\n\n"
        f"😱 F&G: `{sent['value']} — {sent['bias']}`\n"
        f"{'🚀 SIGNAL READY!' if final >= MIN_TOTAL and dir1d != 'NEUTRAL' and sc4h_score >= MIN_4H and sc1h_score >= MIN_1H and sc15_score >= MIN_15M else '⏳ No signal yet — market not aligned'}"
    )
    ok2, _ = tg_test(status_msg)
    if ok2:
        print(s("  ✅ Diagnostic report sent to Telegram!", BGN))
    else:
        print(s("  ❌ Telegram send failed", BRD))

    print()
    print(s("  ╔" + "═"*(W-4) + "╗", CY))
    print(s("  ║" + " DIAGNOSTIC COMPLETE ".center(W-4) + "║", BCY, bold=True))
    print(s("  ╚" + "═"*(W-4) + "╝", CY))
    print()

if __name__ == "__main__":
    main()
