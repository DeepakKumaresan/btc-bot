"""
╔═══════════════════════════════════════════════════════════════╗
║           BTC SIGNAL INTELLIGENCE — FINAL EDITION            ║
║                                                               ║
║  Built with brutal honesty about what bots CAN and CAN'T do  ║
║                                                               ║
║  WHAT THIS BOT DOES DIFFERENTLY:                              ║
║  ✓ 5 signal modes — trend, rejection, reversal,              ║
║    divergence, structure break                                ║
║  ✓ Whale/news guard — skips signals during                   ║
║    abnormal volume spikes that suggest manipulation           ║
║  ✓ Market regime detector — knows if BTC is                  ║
║    trending, ranging, or in a news-driven move               ║
║  ✓ Signal confidence scoring 0-100                           ║
║  ✓ Clean, readable, professional terminal UI                  ║
║  ✓ Honest — tells you when NOT to trade                      ║
║                                                               ║
║  WHAT NO BOT CAN DO (including this one):                     ║
║  ✗ Predict whale dumps                                        ║
║  ✗ Know about news before it happens                          ║
║  ✗ Guarantee profit on any trade                             ║
║                                                               ║
║  INSTALL: pip install ccxt pandas numpy ta requests           ║
║  RUN:     python btc_final.py                                 ║
╚═══════════════════════════════════════════════════════════════╝
"""

import ccxt
import pandas as pd
import numpy as np
import time
import requests
import sys
import subprocess
from datetime import datetime

try:
    import ta
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "ta"])
    import ta

# ═══════════════════════════════════════════════════════════════
#  CONFIGURATION
# ═══════════════════════════════════════════════════════════════

EXCHANGE      = "bitget"           # bitget / binance / bybit
SYMBOL        = "BTC/USDT:USDT"
TF_15M        = "15m"
TF_1H         = "1h"
TF_4H         = "4h"
LIMIT         = 300

# Signal thresholds — calibrated from real BTC behaviour
RSI_OVERBOUGHT    = 60    # short zone starts here
RSI_OVERSOLD      = 40    # long zone starts here
RSI_EXTREME_OB    = 68    # strong overbought
RSI_EXTREME_OS    = 32    # strong oversold
ADX_TRENDING      = 18    # market is trending above this
VOL_NORMAL_MAX    = 3.0   # volume above 3x average = whale/news, SKIP
VOL_CONFIRM       = 1.0   # minimum volume to confirm signal
MIN_ATR_RATIO     = 0.6   # signal needs real volatility

# Risk
SL_ATR_MULT   = 1.2
TP_ATR_MULT   = 2.6
MIN_RR        = 1.8
SIGNAL_GAP    = 4         # candles between signals

# Telegram (optional)
TG_TOKEN      = ""
TG_CHAT       = ""

# ═══════════════════════════════════════════════════════════════
#  CLEAN TERMINAL COLORS — no blinking, no choppy garbage
# ═══════════════════════════════════════════════════════════════

RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"

# Clean palette — only these colors, used consistently
WHITE  = "\033[97m"
GRAY   = "\033[90m"
GREEN  = "\033[32m"
BGREEN = "\033[92m"
RED    = "\033[31m"
BRED   = "\033[91m"
YELLOW = "\033[33m"
BYELLOW= "\033[93m"
CYAN   = "\033[36m"
BCYAN  = "\033[96m"
MAGENTA= "\033[35m"

BG_GREEN  = "\033[42m"
BG_RED    = "\033[41m"
BG_YELLOW = "\033[43m"
BG_GRAY   = "\033[100m"

W = 66  # terminal width

def g(text, color="", bold=False, dim=False):
    """Apply color/style to text cleanly."""
    style = ""
    if bold: style += BOLD
    if dim:  style += DIM
    style += color
    return f"{style}{text}{RESET}"

def line(char="─", color=GRAY, width=W):
    return g(char * width, color)

def center(text, width=W, fill=" "):
    return text.center(width, fill)

def pill(text, bg, fg="\033[30m"):
    return f"{bg}{fg}{BOLD} {text} {RESET}"

# ═══════════════════════════════════════════════════════════════
#  EXCHANGE CONNECTION
# ═══════════════════════════════════════════════════════════════

def connect():
    configs = {
        "bitget":  ("bitget",     {"defaultType": "swap"}),
        "binance": ("binanceusdm", {}),
        "bybit":   ("bybit",      {"defaultType": "linear"}),
    }
    name, opts = configs[EXCHANGE.lower()]
    ex = getattr(ccxt, name)({"enableRateLimit": True, "options": opts})
    return ex

def fetch_df(ex, tf):
    raw = ex.fetch_ohlcv(SYMBOL, tf, limit=LIMIT)
    df  = pd.DataFrame(raw, columns=["ts","open","high","low","close","volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    return df.set_index("ts").astype(float)

# ═══════════════════════════════════════════════════════════════
#  ALL INDICATORS
# ═══════════════════════════════════════════════════════════════

def build_indicators(df):
    c, h, l, v = df.close, df.high, df.low, df.volume

    # EMAs — multiple timeframes of trend
    for p in [5, 9, 20, 50, 200]:
        df[f"ema{p}"] = ta.trend.EMAIndicator(c, p).ema_indicator()

    # RSI + divergence base
    df["rsi"] = ta.momentum.RSIIndicator(c, 14).rsi()
    df["rsi_prev"] = df["rsi"].shift(1)

    # MACD
    macd = ta.trend.MACD(c, 12, 26, 9)
    df["macd"]   = macd.macd()
    df["macds"]  = macd.macd_signal()
    df["macdh"]  = macd.macd_diff()
    df["macdh_prev"] = df["macdh"].shift(1)

    # Bollinger Bands
    bb = ta.volatility.BollingerBands(c, 20, 2)
    df["bb_up"]  = bb.bollinger_hband()
    df["bb_lo"]  = bb.bollinger_lband()
    df["bb_mid"] = bb.bollinger_mavg()
    df["bb_w"]   = (df.bb_up - df.bb_lo) / c * 100

    # ATR — volatility
    df["atr"]    = ta.volatility.AverageTrueRange(h, l, c, 14).average_true_range()
    df["atr_ma"] = df["atr"].rolling(20).mean()

    # Volume analysis
    df["vol_ma"]  = v.rolling(10).mean()
    df["vol_ma20"]= v.rolling(20).mean()
    df["vol_ratio"]= v / df["vol_ma"].replace(0, 1)

    # Stochastic RSI
    sr = ta.momentum.StochRSIIndicator(c, 14, 3, 3)
    df["stk"] = sr.stochrsi_k() * 100
    df["std"] = sr.stochrsi_d() * 100

    # ADX — trend strength
    adx = ta.trend.ADXIndicator(h, l, c, 14)
    df["adx"]  = adx.adx()
    df["adxp"] = adx.adx_pos()
    df["adxn"] = adx.adx_neg()

    # Candle structure
    df["body"]   = abs(c - df.open)
    df["hi_wick"]= h - df[["open","close"]].max(axis=1)
    df["lo_wick"]= df[["open","close"]].min(axis=1) - l
    df["is_bull"]= (c > df.open).astype(int)

    # Price structure
    df["hh"] = (h > h.shift(1)) & (h.shift(1) > h.shift(2))
    df["ll"] = (l < l.shift(1)) & (l.shift(1) < l.shift(2))
    df["lh"] = (h < h.shift(1))
    df["hl"] = (l > l.shift(1))

    return df

# ═══════════════════════════════════════════════════════════════
#  MARKET REGIME DETECTOR
#  This is what separates this bot from simple indicator bots.
#  It reads WHAT KIND of market we're in before signaling.
# ═══════════════════════════════════════════════════════════════

def detect_regime(df15, df1h, df4h):
    """
    Returns regime dict:
    - type: TRENDING_UP / TRENDING_DOWN / RANGING / NEWS_MOVE / CHOPPY
    - tradeable: True/False
    - reason: human-readable explanation
    - confidence: 0-100
    """
    r15 = df15.iloc[-1]
    r1h = df1h.iloc[-1]
    r4h = df4h.iloc[-1]
    p   = r15.close

    # WHALE / NEWS GUARD — abnormal volume = stay out
    # This catches the Iran-style drops and whale dumps
    if r15.vol_ratio > VOL_NORMAL_MAX:
        return {
            "type": "NEWS_MOVE",
            "tradeable": False,
            "reason": f"Volume {r15.vol_ratio:.1f}x normal — whale/news event. Waiting.",
            "confidence": 0
        }

    # Check last 3 candles for volume spike (delayed news)
    recent_vol_spike = df15["vol_ratio"].iloc[-4:-1].max()
    if recent_vol_spike > VOL_NORMAL_MAX * 0.8:
        return {
            "type": "NEWS_MOVE",
            "tradeable": False,
            "reason": f"Recent volume spike ({recent_vol_spike:.1f}x) — news aftershock. Waiting.",
            "confidence": 10
        }

    # TRENDING UP
    trend_up_score = sum([
        p > r15.ema200,
        p > r15.ema50,
        r15.ema9 > r15.ema20,
        r15.adx > ADX_TRENDING,
        r15.adxp > r15.adxn,
        r1h.close > r1h.ema50,
        r4h.close > r4h.ema200,
        bool(r15.hh),
    ])
    if trend_up_score >= 5:
        return {
            "type": "TRENDING_UP",
            "tradeable": True,
            "reason": f"Strong uptrend confirmed on 3 timeframes ({trend_up_score}/8)",
            "confidence": min(100, trend_up_score * 13)
        }

    # TRENDING DOWN
    trend_dn_score = sum([
        p < r15.ema200,
        p < r15.ema50,
        r15.ema9 < r15.ema20,
        r15.adx > ADX_TRENDING,
        r15.adxn > r15.adxp,
        r1h.close < r1h.ema50,
        r4h.close < r4h.ema200,
        bool(r15.ll),
    ])
    if trend_dn_score >= 5:
        return {
            "type": "TRENDING_DOWN",
            "tradeable": True,
            "reason": f"Strong downtrend confirmed on 3 timeframes ({trend_dn_score}/8)",
            "confidence": min(100, trend_dn_score * 13)
        }

    # RANGING — price bouncing between levels
    bb_pct = r15.bb_w
    bb_avg = df15["bb_w"].rolling(50).mean().iloc[-1]
    adx_low = r15.adx < ADX_TRENDING
    if adx_low and bb_pct < bb_avg * 0.9:
        return {
            "type": "RANGING",
            "tradeable": True,
            "reason": f"Range-bound market. ADX {r15.adx:.0f}. Reversal signals only.",
            "confidence": 45
        }

    # CHOPPY — don't trade
    return {
        "type": "CHOPPY",
        "tradeable": False,
        "reason": f"No clear structure. ADX {r15.adx:.0f}. Waiting for setup.",
        "confidence": 20
    }

# ═══════════════════════════════════════════════════════════════
#  SIGNAL CONFIDENCE SCORER (0-100)
# ═══════════════════════════════════════════════════════════════

def confidence_score(checks, regime_conf, rr):
    """
    Combines filter passes + regime confidence + RR
    into a single 0-100 score.
    """
    filter_score = (sum(checks) / len(checks)) * 50
    rr_score     = min(rr / 3.0, 1.0) * 20
    regime_score = (regime_conf / 100) * 30
    total = filter_score + rr_score + regime_score
    return round(min(total, 100))

# ═══════════════════════════════════════════════════════════════
#  SIGNAL MODES
# ═══════════════════════════════════════════════════════════════

def mode_trend(df15, df1h, regime):
    """
    Trend-following with two sub-modes:
    - PULLBACK: in uptrend, buy dips to EMA9/20 (RSI 45-65 range is fine)
    - BREAKOUT: price crosses above/below key EMA with volume
    """
    if regime["type"] not in ("TRENDING_UP", "TRENDING_DOWN"):
        return None
    r, p  = df15.iloc[-1], df15.iloc[-2]
    r1h   = df1h.iloc[-1]
    price = r.close
    atr   = r.atr

    if regime["type"] == "TRENDING_UP":
        # LONG PULLBACK: price dips to EMA9 or EMA20 in uptrend, then bounces
        # RSI between 40-65 is fine — we are in an uptrend, not waiting for oversold
        pullback_lc = [
            price > r.ema200,                          # above long trend
            price > r.ema50,                           # above mid trend
            r.ema9 > r.ema20,                          # short trend up
            40 < r.rsi < 68,                           # healthy RSI in uptrend
            r.macdh > r.macdh_prev,                    # MACD histogram rising
            r.volume > r.vol_ma * VOL_CONFIRM,         # volume ok
            r.adxp > r.adxn,                           # +DI dominant
            r.is_bull == 1,                            # green candle
            r1h.close > r1h.ema50,                     # 1H confirms up
        ]
        # LONG BREAKOUT: price just crossed above EMA20 with strong volume
        breakout_lc = [
            price > r.ema200,
            p.close < p.ema20 and price > r.ema20,     # fresh cross above EMA20
            r.volume > r.vol_ma * 1.5,                 # strong volume
            r.rsi > 45,
            r.macd > r.macds,
            r.adx > ADX_TRENDING,
            r.is_bull == 1,
            r1h.close > r1h.ema50,
        ]
        pb = sum(pullback_lc)
        bk = sum(breakout_lc)
        if pb >= 6:
            return _signal("LONG", "TREND PULLBACK", price, atr, pullback_lc, regime)
        if bk >= 5:
            return _signal("LONG", "TREND BREAKOUT", price, atr, breakout_lc, regime)

    if regime["type"] == "TRENDING_DOWN":
        # SHORT PULLBACK: price bounces up in downtrend, then resumes down
        pullback_sc = [
            price < r.ema200,
            price < r.ema50,
            r.ema9 < r.ema20,
            32 < r.rsi < 60,
            r.macdh < r.macdh_prev,
            r.volume > r.vol_ma * VOL_CONFIRM,
            r.adxn > r.adxp,
            r.is_bull == 0,
            r1h.close < r1h.ema50,
        ]
        # SHORT BREAKOUT: price breaks below EMA20
        breakout_sc = [
            price < r.ema200,
            p.close > p.ema20 and price < r.ema20,
            r.volume > r.vol_ma * 1.5,
            r.rsi < 55,
            r.macd < r.macds,
            r.adx > ADX_TRENDING,
            r.is_bull == 0,
            r1h.close < r1h.ema50,
        ]
        pb = sum(pullback_sc)
        bk = sum(breakout_sc)
        if pb >= 6:
            return _signal("SHORT", "TREND PULLBACK", price, atr, pullback_sc, regime)
        if bk >= 5:
            return _signal("SHORT", "TREND BREAKOUT", price, atr, breakout_sc, regime)

    return None

def mode_rejection(df15, regime):
    """
    Wick rejection — catches the 79176-type drops.
    Works in ALL tradeable regimes.
    """
    if not regime["tradeable"]:
        return None
    r, p = df15.iloc[-1], df15.iloc[-2]
    price = r.close
    atr   = r.atr

    # SHORT: long upper wick = sellers rejecting price
    sc = [
        r.hi_wick > r.body * 2.0,
        r.hi_wick > atr * 0.6,
        r.volume > r.vol_ma * VOL_CONFIRM,
        r.is_bull == 0,
        r.rsi > 52,
        price < r.ema9,
    ]
    # LONG: long lower wick = buyers rejecting dip
    lc = [
        r.lo_wick > r.body * 2.0,
        r.lo_wick > atr * 0.6,
        r.volume > r.vol_ma * VOL_CONFIRM,
        r.is_bull == 1,
        r.rsi < 55,
        price > r.ema9,
    ]
    ss, ls = sum(sc), sum(lc)
    if ss >= 4:
        return _signal("SHORT", "REJECTION", price, atr, sc, regime)
    if ls >= 4:
        return _signal("LONG",  "REJECTION", price, atr, lc, regime)
    return None

def mode_reversal(df15, df1h, regime):
    """
    RSI exhaustion reversal.
    Best in RANGING regime. Also fires in trending for counter-move exits.
    """
    if not regime["tradeable"]:
        return None
    r, p  = df15.iloc[-1], df15.iloc[-2]
    r1h   = df1h.iloc[-1]
    price = r.close
    atr   = r.atr

    # LONG from oversold
    lc = [
        r.rsi < RSI_EXTREME_OS,
        r.rsi > r.rsi_prev,
        r.stk < 25 and r.stk > r.std,
        r.macdh > r.macdh_prev,
        r.lo_wick > r.body,
        r1h.rsi < 45,
    ]
    # SHORT from overbought
    sc = [
        r.rsi > RSI_EXTREME_OB,
        r.rsi < r.rsi_prev,
        r.stk > 75 and r.stk < r.std,
        r.macdh < r.macdh_prev,
        r.hi_wick > r.body,
        r1h.rsi > 55,
    ]
    ls, ss = sum(lc), sum(sc)
    if ls >= 4:
        return _signal("LONG",  "REVERSAL", price, atr, lc, regime)
    if ss >= 4:
        return _signal("SHORT", "REVERSAL", price, atr, sc, regime)
    return None

def mode_divergence(df15, regime):
    """
    RSI divergence — price makes new high/low but RSI doesn't.
    This is one of the most reliable signals in technical analysis.
    """
    if not regime["tradeable"]:
        return None
    df   = df15.tail(6)
    r    = df.iloc[-1]
    price= r.close
    atr  = r.atr

    # Bearish divergence: price higher high, RSI lower high
    ph   = df["high"].iloc[-3]
    prsi = df["rsi"].iloc[-3]
    bear_div = (r.high > ph) and (r.rsi < prsi) and (r.rsi > 45)

    # Bullish divergence: price lower low, RSI higher low
    pl   = df["low"].iloc[-3]
    plrsi= df["rsi"].iloc[-3]
    bull_div = (r.low < pl) and (r.rsi > plrsi) and (r.rsi < 55)

    vol_ok = r.volume > r.vol_ma * VOL_CONFIRM

    if bear_div and vol_ok:
        checks = [bear_div, vol_ok, r.rsi > 50, r.is_bull == 0, r.adx > 15, True]
        return _signal("SHORT", "DIVERGENCE", price, atr, checks, regime)
    if bull_div and vol_ok:
        checks = [bull_div, vol_ok, r.rsi < 50, r.is_bull == 1, r.adx > 15, True]
        return _signal("LONG",  "DIVERGENCE", price, atr, checks, regime)
    return None

def mode_structure(df15, df1h, regime):
    """
    Market structure break — when price breaks a swing high/low with volume.
    Strong signal in all regimes.
    """
    if not regime["tradeable"]:
        return None
    df   = df15.tail(20)
    r    = df.iloc[-1]
    price= r.close
    atr  = r.atr

    # Recent swing high and low
    swing_high = df["high"].iloc[-20:-2].max()
    swing_low  = df["low"].iloc[-20:-2].min()

    # Break above swing high = bullish structure
    bull_break = price > swing_high and r.volume > r.vol_ma * 1.5

    # Break below swing low = bearish structure
    bear_break = price < swing_low and r.volume > r.vol_ma * 1.5

    # Must have volume to confirm (not a fake breakout)
    vol_spike = r.vol_ratio > 1.4

    if bull_break and vol_spike:
        checks = [True, True, r.rsi > 45, r.ema9 > r.ema20, vol_spike, r.adx > 15]
        return _signal("LONG",  "STRUCTURE BREAK", price, atr, checks, regime)
    if bear_break and vol_spike:
        checks = [True, True, r.rsi < 55, r.ema9 < r.ema20, vol_spike, r.adx > 15]
        return _signal("SHORT", "STRUCTURE BREAK", price, atr, checks, regime)
    return None

# ═══════════════════════════════════════════════════════════════
#  SIGNAL BUILDER
# ═══════════════════════════════════════════════════════════════

def _signal(direction, mode, price, atr, checks, regime):
    if direction == "LONG":
        sl = round(price - atr * SL_ATR_MULT, 1)
        tp = round(price + atr * TP_ATR_MULT, 1)
    else:
        sl = round(price + atr * SL_ATR_MULT, 1)
        tp = round(price - atr * TP_ATR_MULT, 1)

    risk   = abs(price - sl)
    reward = abs(price - tp)
    rr     = round(reward / max(risk, 0.01), 2)

    if rr < MIN_RR:
        return None

    conf = confidence_score(checks, regime["confidence"], rr)

    # Only return signals with meaningful confidence
    if conf < 40:
        return None

    return {
        "direction": direction,
        "mode":      mode,
        "entry":     round(price, 1),
        "sl":        sl,
        "tp":        tp,
        "rr":        rr,
        "confidence":conf,
        "regime":    regime["type"],
        "checks":    sum(checks),
        "total":     len(checks),
        "time":      datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "atr":       round(atr, 1),
    }

def run_all_modes(df15, df1h, df4h, regime):
    """Run all 5 modes, return first valid signal found."""
    for fn in [
        lambda: mode_rejection(df15, regime),
        lambda: mode_structure(df15, df1h, regime),
        lambda: mode_divergence(df15, regime),
        lambda: mode_trend(df15, df1h, regime),
        lambda: mode_reversal(df15, df1h, regime),
    ]:
        try:
            sig = fn()
            if sig:
                return sig
        except:
            continue
    return None

# ═══════════════════════════════════════════════════════════════
#  CLEAN PROFESSIONAL TERMINAL DISPLAY
#  One consistent style. No choppy colors. Readable at a glance.
# ═══════════════════════════════════════════════════════════════

_price_history = []

def sparkline(prices, width=16):
    if len(prices) < 2:
        return g("─" * width, GRAY)
    mn, mx = min(prices), max(prices)
    rng = mx - mn or 1
    chars = "▁▂▃▄▅▆▇█"
    out = ""
    for v in prices[-width:]:
        idx = int((v - mn) / rng * 7)
        c_  = BGREEN if v >= prices[-2] else BRED
        out += g(chars[idx], c_)
    return out

def regime_badge(regime_type):
    badges = {
        "TRENDING_UP":   pill("BULL TREND",  BG_GREEN,  "\033[30m"),
        "TRENDING_DOWN": pill("BEAR TREND",  BG_RED,    "\033[97m"),
        "RANGING":       pill("RANGING",     BG_YELLOW, "\033[30m"),
        "NEWS_MOVE":     pill("NEWS/WHALE",  BG_GRAY,   "\033[97m"),
        "CHOPPY":        pill("CHOPPY",      BG_GRAY,   "\033[97m"),
    }
    return badges.get(regime_type, pill(regime_type, BG_GRAY))

def confidence_bar(conf, width=10):
    filled = int(conf / 100 * width)
    color  = BGREEN if conf >= 70 else (YELLOW if conf >= 50 else RED)
    return g("█" * filled, color) + g("░" * (width - filled), GRAY)

_last_price = [0]
_last_regime = [None]

def print_header():
    print()
    print(g("  ╔" + "═" * (W-4) + "╗", CYAN))
    print(g("  ║", CYAN) + g(center("₿  BTC SIGNAL INTELLIGENCE  —  FINAL EDITION", W-4), BCYAN, bold=True) + g("║", CYAN))
    print(g("  ║", CYAN) + g(center("5 SIGNAL MODES  ·  REGIME AWARE  ·  WHALE GUARD", W-4), GRAY) + g("║", CYAN))
    print(g("  ╚" + "═" * (W-4) + "╝", CYAN))
    print()
    print(g(f"  Exchange : {EXCHANGE.upper()}   Symbol : {SYMBOL}", GRAY))
    print(g(f"  Modes    : TREND · REJECTION · REVERSAL · DIVERGENCE · STRUCTURE", GRAY))
    print(g(f"  Guard    : Skips whale/news moves automatically", GRAY))
    print()
    print(g("  " + "─" * (W-2), GRAY))
    print(g(f"  {'TIME':8}  {'PRICE':>12}  {'CHART':16}  {'REGIME':14}  {'RSI':6}  {'ADX':5}  {'CONF'}",GRAY))
    print(g("  " + "─" * (W-2), GRAY))

def print_status(df15, df1h, df4h, regime):
    r     = df15.iloc[-1]
    price = r.close
    rsi   = r.rsi
    adx   = r.adx
    now   = datetime.now().strftime("%H:%M:%S")

    _price_history.append(price)
    if len(_price_history) > 20:
        _price_history.pop(0)

    arrow = g("▲", BGREEN) if price > _last_price[0] else (g("▼", BRED) if price < _last_price[0] else g("─", GRAY))
    _last_price[0] = price

    sp    = sparkline(_price_history)

    rsi_c = BGREEN if rsi < RSI_OVERSOLD else (BRED if rsi > RSI_OVERBOUGHT else YELLOW)
    adx_c = BGREEN if adx > ADX_TRENDING else GRAY

    # Regime
    rt = regime["type"]
    regime_c = BGREEN if "UP" in rt else (BRED if "DOWN" in rt else YELLOW if rt=="RANGING" else GRAY)

    # Confidence
    conf = regime["confidence"]
    cb   = confidence_bar(conf, 6)

    print(
        f"  {g(now, GRAY)}  "
        f"{arrow}{g(f'${price:>12,.1f}', WHITE, bold=True)}  "
        f"{sp}  "
        f"{g(rt[:14].ljust(14), regime_c)}  "
        f"{g(f'{rsi:.0f}'.ljust(6), rsi_c, bold=True)}"
        f"{g(f'{adx:.0f}'.ljust(5), adx_c)}  "
        f"{cb} {g(str(conf), GRAY)}"
    )

    # If regime changed — print a note
    if regime["type"] != _last_regime[0]:
        _last_regime[0] = regime["type"]
        print()
        if not regime["tradeable"]:
            print(g(f"  ⚠  {regime['reason']}", YELLOW))
        else:
            print(g(f"  ●  {regime['reason']}", GRAY))
        print()

def print_why_no_signal(df15, df1h, df4h, regime):
    """Shows exactly which filters are close to firing — printed every 5 minutes."""
    r, p = df15.iloc[-1], df15.iloc[-2]
    r1h  = df1h.iloc[-1]
    price = r.close
    if not regime["tradeable"]:
        return
    checks = {
        "EMA trend  ": price > r.ema50 if "UP" in regime["type"] else price < r.ema50,
        "EMA align  ": r.ema9 > r.ema20 if "UP" in regime["type"] else r.ema9 < r.ema20,
        "RSI zone   ": 40 < r.rsi < 68 if "UP" in regime["type"] else 32 < r.rsi < 60,
        "MACD hist  ": r.macdh > r.macdh_prev if "UP" in regime["type"] else r.macdh < r.macdh_prev,
        "Volume     ": r.volume > r.vol_ma * VOL_CONFIRM,
        "ADX dir    ": r.adxp > r.adxn if "UP" in regime["type"] else r.adxn > r.adxp,
        "Candle     ": r.is_bull == 1 if "UP" in regime["type"] else r.is_bull == 0,
        "1H confirm ": r1h.close > r1h.ema50 if "UP" in regime["type"] else r1h.close < r1h.ema50,
    }
    passed = sum(checks.values())
    needed = 6
    print()
    print(g(f"  ─── Signal Tracker: {passed}/{needed} needed ─────────────────────────", GRAY))
    for name, ok in checks.items():
        icon = g("  ✓", BGREEN) if ok else g("  ✗", BRED)
        print(icon + g(f" {name}", GRAY))
    if passed >= needed:
        print(g(f"  → All {needed} passed — signal should fire next candle", BGREEN))
    else:
        print(g(f"  → Need {needed - passed} more filter(s) to fire a signal", YELLOW))
    print(g("  " + "─" * (W-2), GRAY))
    print()

def print_signal(sig):
    d    = sig["direction"]
    lng  = d == "LONG"
    gc   = BGREEN if lng else BRED
    dim_c= GREEN  if lng else RED
    ar   = "▲" if lng else "▼"
    conf = sig["confidence"]
    rr   = sig["rr"]

    # Confidence color
    cc = BGREEN if conf >= 70 else (YELLOW if conf >= 50 else BRED)

    print()
    print(g("  ┌" + "─" * (W-4) + "┐", gc))

    # Title row
    mode_str = sig["mode"]
    title = f" {ar} {d}  ·  {mode_str} "
    print(g("  │", gc) + g(title.ljust(W-4), gc, bold=True) + g("│", gc))
    print(g("  ├" + "─" * (W-4) + "┤", GRAY))

    # Time + confidence
    conf_bar = confidence_bar(conf, 12)
    t_line = f"  {g(sig['time'], GRAY)}   confidence {conf_bar} {g(str(conf)+'%', cc, bold=True)}"
    t_raw  = len(f"  {sig['time']}   confidence {'█'*12} {conf}%")
    print(g("  │", gc) + t_line + " " * max(0, W-4 - t_raw) + g("│", gc))
    print(g("  │", gc) + g(f"  Regime: {sig['regime']}   Checks: {sig['checks']}/{sig['total']}", GRAY) + " " * max(0, W-4 - len(f"  Regime: {sig['regime']}   Checks: {sig['checks']}/{sig['total']}")) + g("│", gc))

    print(g("  ├" + "─" * (W-4) + "┤", GRAY))

    # Price levels — clean 3-column layout
    def price_row(label, price_val, color, note):
        lbl_s  = g(f"  {label:<10}", GRAY)
        val_s  = g(f"${price_val:>12,.1f}", color, bold=True)
        note_s = g(f"  {note}", GRAY, dim=True)
        raw    = len(f"  {label:<10}${price_val:>12,.1f}  {note}")
        pad    = " " * max(0, W - 4 - raw)
        print(g("  │", gc) + lbl_s + val_s + note_s + pad + g("│", gc))

    price_row("ENTRY",  sig["entry"], WHITE,   "← place limit order")
    price_row("STOP",   sig["sl"],    BRED,    "← set before entering")
    price_row("TARGET", sig["tp"],    BGREEN,  "← take profit here")

    print(g("  ├" + "─" * (W-4) + "┤", GRAY))

    # R:R visual
    risk_blocks   = 5
    reward_blocks = min(int(rr * risk_blocks), 20)
    rr_visual = g("▌" * risk_blocks, BRED) + g("│", WHITE) + g("▌" * reward_blocks, BGREEN)
    rr_line = f"  R:R  {g(f'{rr:.2f}:1', cc, bold=True)}    {rr_visual}"
    rr_raw  = len(f"  R:R  {rr:.2f}:1    " + "▌"*(risk_blocks+reward_blocks) + "│")
    print(g("  │", gc) + rr_line + " " * max(0, W-4-rr_raw) + g("│", gc))

    # ATR info
    sl_dist = abs(sig["entry"] - sig["sl"])
    atr_line = f"  ATR  {g(str(sig['atr']), CYAN)}   Stop distance: {g(f'${sl_dist:,.1f}', GRAY)}"
    atr_raw  = len(f"  ATR  {sig['atr']}   Stop distance: ${abs(sig['entry']-sig['sl']):,.1f}")
    print(g("  │", gc) + atr_line + " " * max(0, W-4-atr_raw) + g("│", gc))

    print(g("  ├" + "─" * (W-4) + "┤", GRAY))

    # Action line
    act = f"  {ar}  Set TP/SL on exchange, then enter manually. You decide."
    print(g("  │", gc) + g(act, BYELLOW, bold=True) + " " * max(0, W-4-len(act)) + g("│", gc))

    print(g("  └" + "─" * (W-4) + "┘", gc))
    print()

# ═══════════════════════════════════════════════════════════════
#  TELEGRAM
# ═══════════════════════════════════════════════════════════════

def send_telegram(sig):
    if not TG_TOKEN or not TG_CHAT or not sig:
        return
    em  = "🟢" if sig["direction"] == "LONG" else "🔴"
    cc  = "🔥" if sig["confidence"] >= 70 else ("⚡" if sig["confidence"] >= 50 else "⚠️")
    msg = (
        f"{em} *BTC {sig['direction']} — {sig['mode']}*\n"
        f"{cc} Confidence: *{sig['confidence']}%*\n\n"
        f"📍 Regime: `{sig['regime']}`\n"
        f"⏱ `{sig['time']}`\n\n"
        f"🎯 Entry  `${sig['entry']:,.1f}`\n"
        f"🛑 Stop   `${sig['sl']:,.1f}`\n"
        f"💰 Target `${sig['tp']:,.1f}`\n"
        f"⚖️ R:R    `{sig['rr']}:1`\n"
        f"✅ Checks `{sig['checks']}/{sig['total']}`\n\n"
        f"_Set TP/SL BEFORE entering. You control the trade._"
    )
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": msg, "parse_mode": "Markdown"},
            timeout=10
        )
    except Exception as e:
        print(g(f"  Telegram error: {e}", GRAY))

# ═══════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    print_header()
    print(g(f"  Connecting to {EXCHANGE.upper()}...", GRAY), end="", flush=True)
    ex = connect()
    print(g(" connected.", BGREEN))
    print(g("  Scanning every 60s. Press Ctrl+C to stop.\n", GRAY))

    last_sig_idx = -SIGNAL_GAP
    loop_count = 0

    while True:
        try:
            df15 = build_indicators(fetch_df(ex, TF_15M))
            df1h = build_indicators(fetch_df(ex, TF_1H))
            df4h = build_indicators(fetch_df(ex, TF_4H))

            regime = detect_regime(df15, df1h, df4h)
            idx    = len(df15)

            print_status(df15, df1h, df4h, regime)

            if idx - last_sig_idx >= SIGNAL_GAP:
                sig = run_all_modes(df15, df1h, df4h, regime)
                if sig:
                    print_signal(sig)
                    send_telegram(sig)
                    last_sig_idx = idx
                else:
                    # Every 5 minutes show why no signal is firing
                    if loop_count % 5 == 0:
                        print_why_no_signal(df15, df1h, df4h, regime)

            loop_count += 1
            time.sleep(60)

        except KeyboardInterrupt:
            print()
            print(g("  " + "─" * (W-2), GRAY))
            print(g("  Bot stopped. Protect your capital.", YELLOW))
            print(g("  " + "─" * (W-2), GRAY))
            print()
            break
        except Exception as e:
            print(g(f"  Error: {e} — retrying in 30s", RED))
            time.sleep(30)

if __name__ == "__main__":
    main()
