# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════╗
║     BTC SIGNAL INTELLIGENCE — INSTITUTIONAL HARDENED v3.0      ║
║                                                                  ║
║  v3.0 FINAL ENGINE:                                              ║
║  ► ANTI-REPAINT  — signals on CLOSED candles only (iloc[-2])     ║
║  ► HTF VETO      — 1H+4H must align before trend fires          ║
║  ► SLOPE FILTER  — 50 EMA must be actively angling trend dir     ║
║  ► DUMP GUARD    — blocks longs after 1.2%+ crash, vice versa    ║
║  ► KEY LEVELS    — rejections must touch VWAP/50EMA/200EMA       ║
║  ► STOCH CROSS   — reversals need Stoch K/D cross confirmation   ║
║  ► FLOW MANDATE  — reversals need 0.3+ institutional imbalance   ║
║  ► STRUCTURE     — 8/9 checks with ATR + MACD + HTF validation   ║
║  ► DIVERGENCE    — 5/7 checks with MTF RSI + volume gate         ║
║  ► MIN CONF 55   — no sub-55 signals reach Telegram ever         ║
║  ► QUALITY TIERS — A+ (conf≥72) / A (conf≥55) only sent         ║
║  INSTALL: pip install ccxt pandas numpy ta requests              ║
║  RUN:     python btc_audited.py                                  ║
╚══════════════════════════════════════════════════════════════════╝
"""

import ccxt
import pandas as pd
import numpy as np
import time
import requests
import sys
import subprocess
import io
import threading
import os
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

# ── Windows UTF-8 fix ─────────────────────────────────────────
# Ensures box-drawing chars and emoji render on all Windows terminals
import sys as _sys
if hasattr(_sys.stdout, "reconfigure"):
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
elif hasattr(_sys.stdout, "buffer"):
    _sys.stdout = io.TextIOWrapper(_sys.stdout.buffer, encoding="utf-8", errors="replace")

try:
    import ta
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "ta"])
    import ta

# ═══════════════════════════════════════════════════════════════
#  CONFIGURATION
# ═══════════════════════════════════════════════════════════════

EXCHANGE   = "bitget"        # bitget / binance / bybit
SYMBOL     = "BTC/USDT:USDT"
TF_15M     = "15m"
TF_1H      = "1h"
TF_4H      = "4h"
LIMIT      = 300

# ── Signal thresholds ──────────────────────────────────────────
RSI_OB         = 60
RSI_OS         = 40
RSI_EXTREME_OB = 68
RSI_EXTREME_OS = 32
ADX_MIN        = 18
VOL_MAX_WHALE  = 3.0
VOL_MIN_SIGNAL = 1.0
SIGNAL_GAP     = 4

# ── Risk ───────────────────────────────────────────────────────
SL_ATR   = 1.2
TP_ATR   = 2.6
MIN_RR   = 1.8

# ── v2.0: Divergence ──────────────────────────────────────────
DIV_LOOKBACK   = 20     # candles to scan for swing points
DIV_ORDER      = 2      # pivot order (candles each side)
DIV_MIN_SEP    = 3      # min candles between swing points

# ── v2.0: Session hours (UTC) ─────────────────────────────────
SESSION_ASIA   = (0, 8)
SESSION_LONDON = (8, 13)
SESSION_NY     = (13, 21)

# ── Telegram alerts (optional) ─────────────────────────────────
import os
TG_TOKEN = os.getenv("TG_TOKEN", "")
TG_CHAT  = os.getenv("TG_CHAT", "")

# ═══════════════════════════════════════════════════════════════
#  COLORS — clean, consistent, no blinking
# ═══════════════════════════════════════════════════════════════

RS  = "\033[0m"
BD  = "\033[1m"
DM  = "\033[2m"
WH  = "\033[97m"
GY  = "\033[90m"
GN  = "\033[32m"
BGN = "\033[92m"
RD  = "\033[31m"
BRD = "\033[91m"
YL  = "\033[33m"
BYL = "\033[93m"
CY  = "\033[36m"
BCY = "\033[96m"
MG  = "\033[35m"
BG_GN  = "\033[42m"
BG_RD  = "\033[41m"
BG_YL  = "\033[43m"
BG_GY  = "\033[100m"

W = 66  # terminal width

def s(text, col="", bold=False, dim=False):
    st = (BD if bold else "") + (DM if dim else "") + col
    return f"{st}{text}{RS}"

def pill(text, bg, fg="\033[30m"):
    return f"{bg}{fg}{BD} {text} {RS}"

def cbar(val, width=10):
    """Confidence bar."""
    n   = max(0, min(width, int(val / 100 * width)))
    col = BGN if val >= 70 else (YL if val >= 50 else RD)
    return s("█" * n, col) + s("░" * (width - n), GY)

# ═══════════════════════════════════════════════════════════════
#  EXCHANGE
# ═══════════════════════════════════════════════════════════════

def connect():
    cfgs = {
        "bitget":  ("bitget",      {"defaultType": "swap"}),
        "binance": ("binanceusdm", {}),
        "bybit":   ("bybit",       {"defaultType": "linear"}),
    }
    nm, opts = cfgs[EXCHANGE.lower()]
    ex = getattr(ccxt, nm)({"enableRateLimit": True, "options": opts})
    return ex

def fetch(ex, tf):
    raw = ex.fetch_ohlcv(SYMBOL, tf, limit=LIMIT)
    df  = pd.DataFrame(raw, columns=["ts","open","high","low","close","volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    return df.set_index("ts").astype(float)

# ═══════════════════════════════════════════════════════════════
#  INDICATORS
# ═══════════════════════════════════════════════════════════════

def ind(df):
    c, h, l, v = df.close, df.high, df.low, df.volume

    for p in [5, 9, 20, 50, 200]:
        df[f"e{p}"] = ta.trend.EMAIndicator(c, p).ema_indicator()

    df["rsi"]      = ta.momentum.RSIIndicator(c, 14).rsi()
    df["rsi1"]     = df["rsi"].shift(1)

    mc = ta.trend.MACD(c, 12, 26, 9)
    df["macd"]     = mc.macd()
    df["macds"]    = mc.macd_signal()
    df["macdh"]    = mc.macd_diff()
    df["macdh1"]   = df["macdh"].shift(1)

    bb = ta.volatility.BollingerBands(c, 20, 2)
    df["bb_up"]    = bb.bollinger_hband()
    df["bb_lo"]    = bb.bollinger_lband()
    df["bb_w"]     = (df.bb_up - df.bb_lo) / c.replace(0, 1) * 100

    df["atr"]      = ta.volatility.AverageTrueRange(h, l, c, 14).average_true_range()
    df["atr_avg"]  = df["atr"].rolling(20).mean()

    df["vol_ma"]   = v.rolling(10).mean()
    df["vol_ratio"]= v / df["vol_ma"].replace(0, 1)

    sr = ta.momentum.StochRSIIndicator(c, 14, 3, 3)
    df["stk"]      = sr.stochrsi_k() * 100
    df["std"]      = sr.stochrsi_d() * 100

    adx = ta.trend.ADXIndicator(h, l, c, 14)
    df["adx"]      = adx.adx()
    df["adxp"]     = adx.adx_pos()
    df["adxn"]     = adx.adx_neg()

    df["body"]     = abs(c - df.open)
    df["hiw"]      = h - df[["open","close"]].max(axis=1)
    df["low_"]     = df[["open","close"]].min(axis=1) - l
    df["bull"]     = (c > df.open).astype(int)

    # Swing structure (rolling 3-candle)
    df["swing_hi"] = (h > h.shift(1)) & (h > h.shift(2))
    df["swing_lo"] = (l < l.shift(1)) & (l < l.shift(2))

    # ── v2.0: Rolling VWAP ────────────────────────────────────
    tp = (h + l + c) / 3
    df["vwap"] = (tp * v).rolling(20).sum() / v.rolling(20).sum().replace(0, 1)

    # ── v2.0: Order-flow imbalance ────────────────────────────
    rng = (h - l).replace(0, 1)
    buy_pct = (c - l) / rng
    df["buy_vol"]  = v * buy_pct
    df["sell_vol"] = v * (1 - buy_pct)
    df["flow"]     = (df["buy_vol"] - df["sell_vol"]) / v.replace(0, 1)

    # ── v2.0: Engulfing candles ───────────────────────────────
    df["bull_engulf"] = ((df["bull"] == 1) & (df["bull"].shift(1) == 0) &
                         (c > df["open"].shift(1)) & (df["open"] < c.shift(1)))
    df["bear_engulf"] = ((df["bull"] == 0) & (df["bull"].shift(1) == 1) &
                         (df["open"] > c.shift(1)) & (c < df["open"].shift(1)))

    # ── v4.0: Smart Money Concepts (SMC) ─────────────────────
    # Fair Value Gap (FVG): 3-candle imbalance — institutional displacement zone
    # Bullish FVG: current candle's low is above the high of 2 candles ago
    df["fvg_bull"] = l > h.shift(2)
    # Bearish FVG: current candle's high is below the low of 2 candles ago
    df["fvg_bear"] = h < l.shift(2)

    # Order Block (OB): Last opposing candle before a high-volume displacement move
    # A bullish OB is a bearish candle (red) right before strong upward displacement
    # A bearish OB is a bullish candle (green) right before strong downward displacement
    strong_up = (c > c.shift(1)) & (v > df["vol_ma"] * 1.4)
    strong_dn = (c < c.shift(1)) & (v > df["vol_ma"] * 1.4)
    df["ob_bull"] = (df["bull"].shift(1) == 0) & strong_up   # Red candle before strong up
    df["ob_bear"] = (df["bull"].shift(1) == 1) & strong_dn   # Green candle before strong down

    # Liquidity Sweep: Price wicks above/below a recent high/low but closes back
    # This is a "stop hunt" — sweeping retail stops before reversing
    recent_hi = h.rolling(10).max().shift(1)
    recent_lo = l.rolling(10).min().shift(1)
    df["sweep_hi"] = (h > recent_hi) & (c < recent_hi)  # Swept highs, closed below = short signal
    df["sweep_lo"] = (l < recent_lo) & (c > recent_lo)  # Swept lows, closed above = long signal

    return df

# ═══════════════════════════════════════════════════════════════
#  v2.0: SESSION DETECTION
# ═══════════════════════════════════════════════════════════════

def get_session():
    """Returns (session_name, confidence_modifier)."""
    h = datetime.utcnow().hour
    # v3.0 FIX: London/NY overlap is 13:00-17:00 UTC, not inside London block
    if 13 <= h < 17:   # London/NY overlap — highest liquidity
        return "LDN+NY", 10
    if SESSION_ASIA[0] <= h < SESSION_ASIA[1]:
        return "ASIA", -5
    if SESSION_LONDON[0] <= h < SESSION_LONDON[1]:
        return "LONDON", 5
    if SESSION_NY[0] <= h < SESSION_NY[1]:
        return "NEW YORK", 5
    return "OFF-HOURS", -10

# ═══════════════════════════════════════════════════════════════
#  v4.0: MARKET SENTIMENT — Fear & Greed Index
#  Free API from Alternative.me. No key required. Cached 1hr.
#  Extreme Fear = contrarian LONG bias. Extreme Greed = SHORT bias.
# ═══════════════════════════════════════════════════════════════

_sentiment_cache = {"data": None, "ts": 0}

def get_sentiment():
    """Fetch Bitcoin Fear & Greed Index. Free, no API key needed."""
    global _sentiment_cache
    now = time.time()
    if _sentiment_cache["data"] and (now - _sentiment_cache["ts"]) < 3600:
        return _sentiment_cache["data"]
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=8)
        d = r.json()["data"][0]
        val   = int(d["value"])
        label = d["value_classification"]
        if val <= 20:   bias, mod = "EXTREME FEAR",  +8  # Contrarian buy signal
        elif val <= 40: bias, mod = "FEAR",           +4
        elif val <= 60: bias, mod = "NEUTRAL",          0
        elif val <= 80: bias, mod = "GREED",           -3
        else:           bias, mod = "EXTREME GREED",  -8  # Contrarian sell signal
        result = {"value": val, "label": label, "bias": bias, "mod": mod}
        _sentiment_cache = {"data": result, "ts": now}
        return result
    except Exception:
        return {"value": 50, "label": "Neutral", "bias": "NEUTRAL", "mod": 0}

# ═══════════════════════════════════════════════════════════════
#  v2.0: SWING-POINT PIVOT DETECTION
# ═══════════════════════════════════════════════════════════════

def find_swing_highs(df, end_idx, lookback=DIV_LOOKBACK, order=DIV_ORDER):
    """
    Find confirmed pivot highs in df.
    A pivot high at i requires high[i] > high[i±j] for j in 1..order.
    Returns list of (iloc_position, high_value, rsi_value).
    """
    swings = []
    start = max(order, end_idx - lookback)
    for i in range(start, end_idx - order + 1):
        is_pivot = True
        for j in range(1, order + 1):
            if df["high"].iloc[i] <= df["high"].iloc[i - j] or \
               df["high"].iloc[i] <= df["high"].iloc[i + j]:
                is_pivot = False
                break
        if is_pivot:
            swings.append((i, float(df["high"].iloc[i]), float(df["rsi"].iloc[i])))
    return swings

def find_swing_lows(df, end_idx, lookback=DIV_LOOKBACK, order=DIV_ORDER):
    """Same as above but for pivot lows."""
    swings = []
    start = max(order, end_idx - lookback)
    for i in range(start, end_idx - order + 1):
        is_pivot = True
        for j in range(1, order + 1):
            if df["low"].iloc[i] >= df["low"].iloc[i - j] or \
               df["low"].iloc[i] >= df["low"].iloc[i + j]:
                is_pivot = False
                break
        if is_pivot:
            swings.append((i, float(df["low"].iloc[i]), float(df["rsi"].iloc[i])))
    return swings

# ═══════════════════════════════════════════════════════════════
#  MARKET REGIME — v3.0
#  Reads WHAT TYPE of market this is. Vetoes trends without HTF alignment.
# ═══════════════════════════════════════════════════════════════

def regime(df15, df1h, df4h):
    r   = df15.iloc[-2]   # v3.0: use CONFIRMED for regime to prevent flicker
    r_p = df15.iloc[-3]
    r1h = df1h.iloc[-2]
    r4h = df4h.iloc[-2]
    p   = r.close

    # ── WHALE / NEWS GUARD ─────────────────────────────────────
    if r.vol_ratio > VOL_MAX_WHALE:
        return mk_regime("NEWS_MOVE", False,
            f"Volume {r.vol_ratio:.1f}× normal — whale/news. Skipping.", 0)
    recent = float(df15["vol_ratio"].iloc[-5:-2].max())
    if recent > VOL_MAX_WHALE * 0.8:
        return mk_regime("NEWS_MOVE", False,
            f"Recent spike {recent:.1f}× — aftershock. Waiting.", 10)

    # ── TREND UP (v3.0 HTF Veto & Slope) ───────────────────────
    slope_up = r.e50 > r_p.e50
    htf_bull = (r1h.close > r1h.e50) and (r4h.close > r4h.e200)

    if slope_up and htf_bull:
        up = int(p > r.e200) + int(p > r.e50) + int(r.e9 > r.e20) + \
             int(r.adx > ADX_MIN) + int(r.adxp > r.adxn) + int(bool(r.swing_hi))
        if up >= 4:
            return mk_regime("TRENDING_UP", True,
                f"Uptrend (HTF Aligned) ({up}/6)", min(100, up*16))

    # ── TREND DOWN (v3.0 HTF Veto & Slope) ─────────────────────
    slope_dn = r.e50 < r_p.e50
    htf_bear = (r1h.close < r1h.e50) and (r4h.close < r4h.e200)

    if slope_dn and htf_bear:
        dn = int(p < r.e200) + int(p < r.e50) + int(r.e9 < r.e20) + \
             int(r.adx > ADX_MIN) + int(r.adxn > r.adxp) + int(bool(r.swing_lo))
        if dn >= 4:
            return mk_regime("TRENDING_DOWN", True,
                f"Downtrend (HTF Aligned) ({dn}/6)", min(100, dn*16))

    # ── RANGING (v4.0 fix: OR logic captures more sideways markets) ────
    # ADX below threshold OR BB bands are not expanding—either means ranging
    bb_avg = float(df15["bb_w"].rolling(50).mean().iloc[-2])
    if r.adx < ADX_MIN or r.bb_w < bb_avg * 1.1:
        return mk_regime("RANGING", True,
            f"Range-bound. ADX {r.adx:.0f}. Reversal/Rejection signals active.", 55)

    # ── CHOPPY — last resort, very small window now ────────────────────
    return mk_regime("CHOPPY", False,
        f"Conflicting signals. ADX {r.adx:.0f}. Waiting.", 20)

def mk_regime(rtype, tradeable, reason, conf):
    return {"type": rtype, "tradeable": tradeable,
            "reason": reason, "confidence": conf}

# ═══════════════════════════════════════════════════════════════
#  CONFIDENCE SCORE (0–100)
# ═══════════════════════════════════════════════════════════════

def conf_score(checks, reg_conf, rr):
    # FIX #6: explicit int conversion for booleans
    passed       = sum(int(bool(c)) for c in checks)
    filter_score = (passed / max(len(checks), 1)) * 50
    rr_score     = min(rr / 3.0, 1.0) * 20
    regime_score = (reg_conf / 100) * 30
    return round(min(filter_score + rr_score + regime_score, 100))

# ═══════════════════════════════════════════════════════════════
#  SIGNAL BUILDER — v4.0: SMC + Sentiment + Adaptive ATR
# ═══════════════════════════════════════════════════════════════

# Consecutive rejection tracker
_reject_history = []   # list of (direction, candle_idx)

def mk_signal(direction, mode, price, atr, checks, reg, atr_avg=None, mtf_boost=0, smc=None, sentiment=None):
    # Adaptive ATR stops — widen in high vol, tighten in low vol
    if atr_avg and atr_avg > 0:
        vr = atr / atr_avg
        if vr > 1.5:   sl_m, tp_m = 1.5, 3.2
        elif vr < 0.7: sl_m, tp_m = 1.0, 2.2
        else:          sl_m, tp_m = SL_ATR, TP_ATR
    else:
        sl_m, tp_m = SL_ATR, TP_ATR

    if direction == "LONG":
        sl = round(price - atr * sl_m, 1)
        tp = round(price + atr * tp_m, 1)
    else:
        sl = round(price + atr * sl_m, 1)
        tp = round(price - atr * tp_m, 1)

    risk   = abs(price - sl)
    reward = abs(price - tp)
    rr     = round(reward / max(risk, 0.01), 2)
    if rr < MIN_RR:
        return None

    # Session confidence
    sess_name, sess_mod = get_session()

    # v4.0: SMC Confluence boost — reward institutional signals
    smc_boost = 0
    smc_tags  = []
    if smc:
        if smc.get("fvg"):   smc_boost += 8;  smc_tags.append("FVG")
        if smc.get("ob"):    smc_boost += 6;  smc_tags.append("OB")
        if smc.get("sweep"): smc_boost += 10; smc_tags.append("SWEEP")

    # v4.0: Sentiment alignment boost
    sent_boost = 0
    if sentiment:
        if direction == "LONG"  and sentiment["mod"] > 0: sent_boost = sentiment["mod"]
        if direction == "SHORT" and sentiment["mod"] < 0: sent_boost = abs(sentiment["mod"])
        # Penalise if trading AGAINST sentiment
        if direction == "LONG"  and sentiment["mod"] < -3: sent_boost = sentiment["mod"]
        if direction == "SHORT" and sentiment["mod"] > 3:  sent_boost = -sentiment["mod"]

    conf = conf_score(checks, reg["confidence"], rr) + mtf_boost + sess_mod + smc_boost + sent_boost
    conf = max(0, min(conf, 100))

    # v4.0 FINAL: Hard confidence floor — 65 minimum, 73 if on rejection streak
    same_dir_rejects = sum(1 for d, _ in _reject_history[-5:] if d == direction)
    min_conf = 73 if same_dir_rejects >= 3 else 65
    if conf < min_conf:
        _reject_history.append((direction, 0))
        return None

    # v4.0 FINAL: Quality tier — only A+ reaches Telegram
    htf_ok = reg["confidence"] >= 60
    if conf >= 72 and htf_ok:
        tier = "A+"
    elif conf >= 65:
        tier = "A"    # tracked internally, NOT sent to Telegram
    else:
        tier = "B"    # blocked

    return {
        "dir":     direction,
        "mode":    mode,
        "entry":   round(price, 1),
        "sl":      sl,
        "tp":      tp,
        "rr":      rr,
        "conf":    conf,
        "tier":    tier,
        "regime":  reg["type"],
        "passed":  sum(int(bool(c)) for c in checks),
        "total":   len(checks),
        "time":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "atr":     round(atr, 1),
        "sl_mode": "WIDE" if (atr_avg and atr_avg > 0 and atr/atr_avg > 1.5) else
                   ("TIGHT" if (atr_avg and atr_avg > 0 and atr/atr_avg < 0.7) else "STD"),
        "session": sess_name,
        "smc_tags": smc_tags,
        "sentiment": sentiment or {"value": 50, "label": "Neutral", "bias": "NEUTRAL"},
    }

# ═══════════════════════════════════════════════════════════════
#  SIGNAL MODES — v2.0: Anti-Repaint (all use CONFIRMED candle)
#  Every mode evaluates iloc[-2] (last CLOSED candle).
#  iloc[-1] is the FORMING candle — used for display only.
# ═══════════════════════════════════════════════════════════════

def m_trend(df15, df1h, reg):
    """
    v4.0: Trend-following with SMC confluence (FVG/OB/Sweep).
    """
    if reg["type"] not in ("TRENDING_UP", "TRENDING_DOWN"):
        return None

    r  = df15.iloc[-2]
    p  = df15.iloc[-3]
    r1 = df1h.iloc[-2]
    price, atr, atr_avg = r.close, r.atr, r.atr_avg

    swing_hi_20 = float(df15["high"].iloc[-22:-2].max())
    swing_lo_20 = float(df15["low"].iloc[-22:-2].min())
    drop_pct    = (swing_hi_20 - price) / swing_hi_20
    pump_pct    = (price - swing_lo_20) / swing_lo_20

    sentiment = get_sentiment()

    if reg["type"] == "TRENDING_UP":
        if drop_pct > 0.012:
            return None
        # v4.0: Detect SMC confluence for boost
        smc = {
            "fvg":   bool(r.fvg_bull),
            "ob":    bool(r.ob_bull),
            "sweep": bool(r.sweep_lo),   # Swept lows before long = clean entry
        }
        pb = [
            price > r.e200, price > r.e50, r.e9 > r.e20,
            40 < r.rsi < 68, r.macdh > r.macdh1,
            r.vol_ratio >= VOL_MIN_SIGNAL, r.adxp > r.adxn,
            r.bull == 1, r1.close > r1.e50,
            price < r.vwap or r.flow > 0.1,
        ]
        bk = [
            price > r.e200,
            p.close < p.e20 and price > r.e20,
            r.vol_ratio >= 1.5,
            r.rsi > 45, r.macd > r.macds,
            r.adx > ADX_MIN, r.bull == 1,
            r1.close > r1.e50, r.flow > 0,
            r.high > df15["high"].iloc[-4:-2].max()
        ]
        if sum(int(b) for b in pb) >= 8:
            return mk_signal("LONG", "TREND PULLBACK", price, atr, pb, reg, atr_avg, smc=smc, sentiment=sentiment)
        if sum(int(b) for b in bk) >= 8:
            return mk_signal("LONG", "TREND BREAKOUT", price, atr, bk, reg, atr_avg, smc=smc, sentiment=sentiment)

    if reg["type"] == "TRENDING_DOWN":
        if pump_pct > 0.012:
            return None
        smc = {
            "fvg":   bool(r.fvg_bear),
            "ob":    bool(r.ob_bear),
            "sweep": bool(r.sweep_hi),   # Swept highs before short = clean entry
        }
        pb = [
            price < r.e200, price < r.e50, r.e9 < r.e20,
            32 < r.rsi < 60, r.macdh < r.macdh1,
            r.vol_ratio >= VOL_MIN_SIGNAL, r.adxn > r.adxp,
            r.bull == 0, r1.close < r1.e50,
            price > r.vwap or r.flow < -0.1,
        ]
        bk = [
            price < r.e200,
            p.close > p.e20 and price < r.e20,
            r.vol_ratio >= 1.5,
            r.rsi < 55, r.macd < r.macds,
            r.adx > ADX_MIN, r.bull == 0,
            r1.close < r1.e50, r.flow < 0,
            r.low_ < df15["low"].iloc[-4:-2].min()
        ]
        if sum(int(b) for b in pb) >= 8:
            return mk_signal("SHORT", "TREND PULLBACK", price, atr, pb, reg, atr_avg, smc=smc, sentiment=sentiment)
        if sum(int(b) for b in bk) >= 8:
            return mk_signal("SHORT", "TREND BREAKOUT", price, atr, bk, reg, atr_avg, smc=smc, sentiment=sentiment)

    return None

def m_rejection(df15, reg):
    """
    Wick rejection. v3.0: Key Level Confluence required.
    Anti-repaint: confirmed candle only.
    """
    if not reg["tradeable"]:
        return None
    r  = df15.iloc[-2]                         # CONFIRMED
    price, atr, atr_avg = r.close, r.atr, r.atr_avg

    # v3.0: Key Level Confluence (VWAP, 50EMA, or 200EMA)
    # Ignore wicks floating in the middle of nowhere
    d_vwap = abs(price - r.vwap) / price
    d_200  = abs(price - r.e200) / price
    d_50   = abs(price - r.e50) / price
    if min(d_vwap, d_200, d_50) > 0.005: 
        return None

    sc = [
        r.hiw > r.body * 2.0,
        r.hiw > atr * 0.5,
        r.vol_ratio >= VOL_MIN_SIGNAL,
        r.bull == 0,
        r.rsi > 50,
        price < r.e9,
        r.flow < 0,                            # v2.0: selling pressure
        bool(r.bear_engulf),                   # v2.0: engulfing pattern
    ]
    lc = [
        r.low_ > r.body * 2.0,
        r.low_ > atr * 0.5,
        r.vol_ratio >= VOL_MIN_SIGNAL,
        r.bull == 1,
        r.rsi < 50,
        price > r.e9,
        r.flow > 0,
        bool(r.bull_engulf),
    ]
    # v3.0: Require 6/8 instead of 5/8
    if sum(int(b) for b in sc) >= 6:
        return mk_signal("SHORT", "REJECTION", price, atr, sc, reg, atr_avg)
    if sum(int(b) for b in lc) >= 6:
        return mk_signal("LONG",  "REJECTION", price, atr, lc, reg, atr_avg)
    return None

def m_reversal(df15, df1h, reg):
    """
    RSI exhaustion. v3.0: Volume Mandate & Stoch Cross required.
    """
    if not reg["tradeable"]:
        return None
    r  = df15.iloc[-2]                         # CONFIRMED
    p  = df15.iloc[-3]                         # PREV CONFIRMED
    r1 = df1h.iloc[-2]                         # CONFIRMED 1H
    price, atr, atr_avg = r.close, r.atr, r.atr_avg

    # v3.0: Stochastic Cross Confirmation
    stoch_cross_up = r.stk > r.std and p.stk <= p.std
    stoch_cross_dn = r.stk < r.std and p.stk >= p.std

    lc = [
        r.rsi < RSI_EXTREME_OS,
        r.rsi > r.rsi1,
        stoch_cross_up,                        # v3.0: Stoch must be crossing UP
        r.macdh > r.macdh1,
        r.low_ > r.body * 0.8,
        r1.rsi < 48,
        bool(r.bull_engulf) or r.flow > 0.3,   # v3.0: Requires HEAVY buying (0.3)
    ]
    sc = [
        r.rsi > RSI_EXTREME_OB,
        r.rsi < r.rsi1,
        stoch_cross_dn,                        # v3.0: Stoch must be crossing DOWN
        r.macdh < r.macdh1,
        r.hiw > r.body * 0.8,
        r1.rsi > 52,
        bool(r.bear_engulf) or r.flow < -0.3,  # v3.0: Requires HEAVY selling (-0.3)
    ]
    # v3.0: Require 6/7 checks
    if sum(int(b) for b in lc) >= 6:
        return mk_signal("LONG",  "REVERSAL", price, atr, lc, reg, atr_avg)
    if sum(int(b) for b in sc) >= 6:
        return mk_signal("SHORT", "REVERSAL", price, atr, sc, reg, atr_avg)
    return None

def m_divergence(df15, df1h, reg):
    """
    v2.0: COMPLETE REWRITE — Dynamic swing-point divergence scanner.

    Instead of comparing current vs fixed bar[-5], this now:
    1. Scans last 20 candles for ACTUAL pivot swing highs/lows
    2. Requires pivots confirmed by candles on both sides (order=2)
    3. Compares the two most recent swing points for divergence
    4. Also checks current candle vs last swing for developing div
    5. Cross-validates with 1H RSI for multi-timeframe confirmation
    6. Supports Regular + Hidden divergence
    """
    if not reg["tradeable"]:
        return None
    if len(df15) < DIV_LOOKBACK + 5:
        return None

    c_idx = len(df15) - 2                      # confirmed candle index
    r     = df15.iloc[c_idx]
    price, atr, atr_avg = r.close, r.atr, r.atr_avg

    # Find pivot swing points in the lookback window
    sh = find_swing_highs(df15, c_idx)
    sl_ = find_swing_lows(df15, c_idx)

    bear_div = False
    bull_div = False
    div_type = "REGULAR"
    mtf_boost = 0

    # ── BEARISH DIVERGENCE ────────────────────────────────────
    # Current candle vs most recent swing high
    if sh:
        last = sh[-1]
        sep = c_idx - last[0]
        if sep >= DIV_MIN_SEP:
            if float(r.high) > last[1] and float(r.rsi) < last[2] - 2:
                bear_div = True
                div_type = "REGULAR"
    # Classic: between two swing highs
    if not bear_div and len(sh) >= 2:
        s1, s2 = sh[-2], sh[-1]
        if (s2[0] - s1[0]) >= DIV_MIN_SEP:
            if s2[1] > s1[1] and s2[2] < s1[2] - 2:
                bear_div = True
            elif s2[1] < s1[1] and s2[2] > s1[2] + 2:
                bear_div = True
                div_type = "HIDDEN"

    # ── BULLISH DIVERGENCE ────────────────────────────────────
    if sl_:
        last = sl_[-1]
        sep = c_idx - last[0]
        if sep >= DIV_MIN_SEP:
            if float(r.low) < last[1] and float(r.rsi) > last[2] + 2:
                bull_div = True
                div_type = "REGULAR"
    if not bull_div and len(sl_) >= 2:
        s1, s2 = sl_[-2], sl_[-1]
        if (s2[0] - s1[0]) >= DIV_MIN_SEP:
            if s2[1] < s1[1] and s2[2] > s1[2] + 2:
                bull_div = True
            elif s2[1] > s1[1] and s2[2] < s1[2] - 2:
                bull_div = True
                div_type = "HIDDEN"

    vol_ok = r.vol_ratio >= VOL_MIN_SIGNAL

    # v2.0: Multi-TF divergence — check 1H RSI alignment
    if len(df1h) >= 3:
        r1h      = df1h.iloc[-2]
        r1h_prev = df1h.iloc[-3]
        if bear_div and float(r1h.rsi) < float(r1h_prev.rsi):
            mtf_boost = 15
        if bull_div and float(r1h.rsi) > float(r1h_prev.rsi):
            mtf_boost = 15

    mode_label = f"DIVERGENCE ({div_type})"

    # v3.0: No hardcoded Trues — every check is real and meaningful
    if bear_div and vol_ok:
        checks = [
            bear_div,                          # divergence confirmed
            r.rsi > 48,                        # RSI elevated (bearish context)
            r.bull == 0 or r.hiw > r.body * 0.5,  # bearish candle or wick rejection
            r.adx > 14,                        # trend has strength
            vol_ok,                            # volume supports move
            r.flow < 0.05,                     # not showing strong buying
            mtf_boost > 0 or r.rsi > 55,      # MTF or RSI confirms exhaustion
        ]
        if sum(int(bool(b)) for b in checks) >= 5:
            return mk_signal("SHORT", mode_label, price, atr, checks, reg, atr_avg, mtf_boost)
    if bull_div and vol_ok:
        checks = [
            bull_div,
            r.rsi < 52,
            r.bull == 1 or r.low_ > r.body * 0.5,
            r.adx > 14,
            vol_ok,
            r.flow > -0.05,
            mtf_boost > 0 or r.rsi < 45,
        ]
        if sum(int(bool(b)) for b in checks) >= 5:
            return mk_signal("LONG", mode_label, price, atr, checks, reg, atr_avg, mtf_boost)
    return None

def m_structure(df15, df1h, reg):
    """
    Structure break. v3.0: Real checks only, no hardcoded True. HTF confirmation added.
    Anti-repaint: confirmed candle.
    """
    if not reg["tradeable"]:
        return None
    if len(df15) < 23:
        return None

    r     = df15.iloc[-2]                      # CONFIRMED
    r1h   = df1h.iloc[-2]                      # CONFIRMED 1H
    price, atr, atr_avg = r.close, r.atr, r.atr_avg

    # Swing high/low of last 20 confirmed candles
    swing_hi = float(df15["high"].iloc[-22:-2].max())
    swing_lo = float(df15["low"].iloc[-22:-2].min())

    vol_spike = r.vol_ratio >= 1.5   # v3.0: raised from 1.4

    bull_break = price > swing_hi and vol_spike
    bear_break = price < swing_lo and vol_spike

    if bull_break:
        checks = [
            price > swing_hi,            # actual break above structure
            vol_spike,                   # high volume confirms break
            r.rsi > 48,                  # RSI supports bullish momentum
            r.e9 > r.e20,               # short-term EMA aligned
            r.adx > ADX_MIN,            # trending market
            r.flow > 0.1,               # net buying pressure
            r.macd > r.macds,           # MACD supports direction
            r1h.close > r1h.e50,        # 1H HTF confirmation
            r.macdh > r.macdh1,         # MACD histogram rising
        ]
        if sum(int(b) for b in checks) >= 7:
            return mk_signal("LONG",  "STRUCTURE BREAK", price, atr, checks, reg, atr_avg)
    if bear_break:
        checks = [
            price < swing_lo,
            vol_spike,
            r.rsi < 52,
            r.e9 < r.e20,
            r.adx > ADX_MIN,
            r.flow < -0.1,
            r.macd < r.macds,
            r1h.close < r1h.e50,
            r.macdh < r.macdh1,
        ]
        if sum(int(b) for b in checks) >= 7:
            return mk_signal("SHORT", "STRUCTURE BREAK", price, atr, checks, reg, atr_avg)
    return None

def run_modes(df15, df1h, df4h, reg):
    """
    v3.0 Priority order: trend → structure → divergence → rejection → reversal.
    Trend/Structure signals fire first (highest probability in trending markets).
    Reversal fires last (highest risk, needs full confluence).
    """
    modes = [
        ("TREND",      lambda: m_trend(df15, df1h, reg)),
        ("STRUCTURE",  lambda: m_structure(df15, df1h, reg)),
        ("DIVERGENCE", lambda: m_divergence(df15, df1h, reg)),
        ("REJECTION",  lambda: m_rejection(df15, reg)),
        ("REVERSAL",   lambda: m_reversal(df15, df1h, reg)),
    ]
    for name, fn in modes:
        try:
            sig = fn()
            if sig:
                return sig
        except (AttributeError, ValueError, KeyError) as e:
            print(s(f"  [{name}] skipped: {e}", GY))
    return None

# ═══════════════════════════════════════════════════════════════
#  DISPLAY
# ═══════════════════════════════════════════════════════════════

_ph   = []          # price history for sparkline
_lp   = [0]         # last price
_lreg = [None]      # last regime (for change detection)

def spark(p):
    _ph.append(p)
    if len(_ph) > 20:
        _ph.pop(0)
    if len(_ph) < 2:
        return s("────────────────", GY)
    mn, mx = min(_ph), max(_ph)
    rng    = mx - mn or 1
    bars   = "▁▂▃▄▅▆▇█"
    # FIX #8: guard against list length issues
    prev   = _ph[-2] if len(_ph) >= 2 else p
    out    = ""
    for v in _ph[-16:]:
        idx = min(int((v - mn) / rng * 7), 7)
        col = BGN if v >= prev else BRD
        out += s(bars[idx], col)
    return out

def print_header():
    print()
    print(s("  ╔" + "═"*(W-4) + "╗", CY))
    t1 = "₿  BTC SIGNAL INTELLIGENCE  —  v3.0 FINAL"
    t2 = "HTF-VETO · DUMP-GUARD · KEY-LEVEL · STOCH-CROSS · MIN-CONF-55"
    print(s("  ║", CY) + s(t1.center(W-4), BCY, bold=True) + s("║", CY))
    print(s("  ║", CY) + s(t2.center(W-4), GY)             + s("║", CY))
    print(s("  ╚" + "═"*(W-4) + "╝", CY))
    print()
    sess_name, _ = get_session()
    print(s(f"  Exchange : {EXCHANGE.upper()}   Symbol : {SYMBOL}", GY))
    print(s(f"  Session  : {sess_name}   Anti-Repaint: ON", GY))
    print(s(f"  Guard    : Whale/news + consecutive rejection filter", GY))
    print()
    print(s("  " + "─"*(W-2), GY))
    cols = f"  {'TIME':8}  {'PRICE':>12}  {'SPARKLINE':16}  {'REGIME':14}  {'RSI':5} {'ADX':5} {'CONF'}"
    print(s(cols, GY))
    print(s("  " + "─"*(W-2), GY))

def print_status(df15, df1h, df4h, reg):
    r     = df15.iloc[-1]
    price = r.close
    rsi   = r.rsi
    adx   = r.adx
    now   = datetime.now().strftime("%H:%M:%S")

    sp    = spark(price)
    arrow = s("▲", BGN) if price > _lp[0] else (s("▼", BRD) if price < _lp[0] else s("─", GY))
    _lp[0] = price

    rsi_c  = BGN if rsi < RSI_OS else (BRD if rsi > RSI_OB else YL)
    adx_c  = BGN if adx > ADX_MIN else GY
    rt     = reg["type"]
    reg_c  = BGN if "UP" in rt else (BRD if "DOWN" in rt else (YL if rt=="RANGING" else GY))
    cb     = cbar(reg["confidence"], 6)

    print(
        f"  {s(now,GY)}  "
        f"{arrow}{s(f'${price:>12,.1f}', WH, bold=True)}  "
        f"{sp}  "
        f"{s(rt[:14].ljust(14), reg_c)}  "
        f"{s(f'{rsi:.0f}'.ljust(5), rsi_c, bold=True)}"
        f"{s(f'{adx:.0f}'.ljust(5), adx_c)}  "
        f"{cb}{s(str(reg['confidence']), GY)}"
    )

    if rt != _lreg[0]:
        _lreg[0] = rt
        print()
        icon = "  ⚠" if not reg["tradeable"] else "  ●"
        col  = YL if not reg["tradeable"] else GY
        print(s(f"{icon}  {reg['reason']}", col))
        print()

def print_tracker(df15, df1h, reg):
    """
    FIX #7: was called on loop_count=0 (first run before data settles).
    Caller now starts loop_count=1.

    Shows exactly which filters pass/fail for next likely signal.
    """
    if not reg["tradeable"]:
        return

    r   = df15.iloc[-1]
    r1h = df1h.iloc[-1]
    p   = r.close
    up  = "UP" in reg["type"]

    checks = {
        "EMA 200 trend": p > r.e200       if up else p < r.e200,
        "EMA 50  trend": p > r.e50        if up else p < r.e50,
        "EMA 9>20 align": r.e9 > r.e20   if up else r.e9 < r.e20,
        "RSI zone      ": 40<r.rsi<68    if up else 32<r.rsi<60,
        "MACD histogram": r.macdh>r.macdh1 if up else r.macdh<r.macdh1,
        "Volume ok     ": r.vol_ratio >= VOL_MIN_SIGNAL,
        "ADX direction ": r.adxp>r.adxn  if up else r.adxn>r.adxp,
        "Candle color  ": r.bull==1       if up else r.bull==0,
        "1H confirms   ": r1h.close>r1h.e50 if up else r1h.close<r1h.e50,
    }

    passed = sum(int(v) for v in checks.values())
    needed = 6
    direction = "LONG" if up else "SHORT"
    rang = reg["type"] == "RANGING"

    print()
    print(s(f"  ── Signal Tracker ({direction}) ── {passed}/{needed} needed ──────────────", GY))
    for name, ok in checks.items():
        icon = s("  ✓", BGN) if ok else s("  ✗", BRD)
        print(icon + s(f" {name}", GY))

    if rang:
        print(s("  ℹ  RANGING: Reversal/Rejection modes active, not trend", CY))

    gap = needed - passed
    if gap <= 0:
        print(s("  → All filters passing — signal fires on next valid candle", BGN))
    elif gap == 1:
        print(s(f"  → 1 filter away from signal", BYL))
    else:
        print(s(f"  → {gap} more filters needed", YL))
    print(s("  " + "─"*(W-2), GY))
    print()

def print_signal(sig):
    lng  = sig["dir"] == "LONG"
    gc   = BGN if lng else BRD
    ar   = "▲" if lng else "▼"
    conf = sig["conf"]
    rr   = sig["rr"]
    cc   = BGN if conf>=70 else (YL if conf>=50 else BRD)

    IW = W - 4   # inner width

    def row(left, right_styled, right_raw):
        raw = len(left) + len(right_raw)
        pad = " " * max(0, IW - raw)
        print(s("  │", gc) + left + right_styled + pad + s("│", gc))

    print()
    print(s("  ┌" + "─"*IW + "┐", gc))

    # Title with v2.0 tier badge
    tier = sig.get('tier', 'B')
    tier_c = BGN if tier == 'A+' else (YL if tier == 'A' else GY)
    title = f" {ar} {sig['dir']}  ·  {sig['mode']} "
    print(s("  │", gc) + s(title.ljust(IW - 6), gc, bold=True) + s(f"[{tier}]", tier_c, bold=True) + s(" │", gc))
    print(s("  ├" + "─"*IW + "┤", GY))

    # Anti-repaint tag + time + confidence
    cb   = cbar(conf, 12)
    tl   = f"  {sig['time']}   confidence "
    tr   = f" {conf}%"
    row(s(tl, GY), cb + s(tr, cc, bold=True), tl + "█"*12 + tr)

    # Regime + session + SL mode
    sl_mode = sig.get('sl_mode', 'STD')
    sess = sig.get('session', '')
    rl  = f"  {sig['regime']}  {sess}  SL:{sl_mode}  {sig['passed']}/{sig['total']}"
    row(s(rl, GY), "", rl)

    # Anti-repaint confirmation
    ar_line = f"  🔒 ANTI-REPAINT: Signal on CLOSED candle"
    row(s(ar_line, CY), "", ar_line)

    print(s("  ├" + "─"*IW + "┤", GY))

    # Prices
    def prow(label, val, col, note):
        l  = f"  {label:<8}"
        vr = f"${val:>12,.1f}"
        n  = f"  {note}"
        row(s(l, GY), s(vr, col, bold=True) + s(n, GY, dim=True), l+vr+n)

    prow("ENTRY",  sig["entry"], WH,  "← place limit order  ")
    prow("STOP",   sig["sl"],    BRD, "← set before entering")
    prow("TARGET", sig["tp"],    BGN, "← take profit here   ")

    print(s("  ├" + "─"*IW + "┤", GY))

    # R:R bar
    rb  = s("▌"*5, BRD)
    gb  = s("▌"*min(int(rr*5), 18), BGN)
    rrl = f"  R:R {rr:.2f}:1    "
    rrv = rb + s("│", WH) + gb
    rrr = "▌"*(5+min(int(rr*5),18)) + "│"
    row(s(rrl, GY), s(f"{rr:.2f}:1", cc, bold=True) + s("    ", "") + rrv,
        rrl + f"{rr:.2f}:1    " + rrr)

    # ATR + adaptive info
    dist = abs(sig["entry"] - sig["sl"])
    al   = f"  ATR {sig['atr']}   Stop dist: ${dist:,.1f}  ({sl_mode})"
    row(s(al, CY), "", al)

    print(s("  ├" + "─"*IW + "┤", GY))

    # Action
    act = f"  {ar}  Set TP/SL on exchange FIRST, then enter manually."
    row(s(act, BYL, bold=True), "", act)

    print(s("  └" + "─"*IW + "┘", gc))
    print()

# ═══════════════════════════════════════════════════════════════
#  TELEGRAM
# ═══════════════════════════════════════════════════════════════

def send_tg(sig):
    if not TG_TOKEN or not TG_CHAT or not sig:
        return
    tier = sig.get('tier', 'B')
    # v4.0 FINAL: Only A+ signals reach Telegram — most powerful trades only
    if tier != 'A+':
        print(s(f"  Telegram: skipped (tier {tier}, need A+)", GY))
        return
    em  = "🟢" if sig["dir"]=="LONG" else "🔴"
    tc  = "🏆" if tier == "A+" else "⚡"
    cc  = "🔥" if sig["conf"]>=70 else ("⚡" if sig["conf"]>=50 else "⚠️")
    msg = (f"{em} *BTC {sig['dir']} — {sig['mode']}*\n"
           f"{tc} Grade: *{tier}*   {cc} Confidence: *{sig['conf']}%*\n\n"
           f"📍 `{sig['regime']}`   🕐 `{sig.get('session','')}`\n"
           f"⏱ `{sig['time']}`\n\n"
           f"🎯 Entry  `${sig['entry']:,.1f}`\n"
           f"🛑 Stop   `${sig['sl']:,.1f}`  ({sig.get('sl_mode','STD')})\n"
           f"💰 Target `${sig['tp']:,.1f}`\n"
           f"⚖️ R:R    `{sig['rr']}:1`\n"
           f"✅ Checks `{sig['passed']}/{sig['total']}`\n\n"
           f"🔒 _Anti-repaint: confirmed candle signal._\n"
           f"_Set TP/SL BEFORE entering. You decide._")
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": msg, "parse_mode": "Markdown"},
            timeout=10)
    except Exception as e:
        print(s(f"  Telegram: {e}", GY))

# ═══════════════════════════════════════════════════════════════
#  KEEPALIVE WEB SERVER (Render/UptimeRobot anti-sleep trick)
#  Runs a tiny HTTP server in a background thread so Render treats
#  this as a "web service" and UptimeRobot can ping it every 5 min
#  to prevent Render from putting the bot to sleep.
# ═══════════════════════════════════════════════════════════════

class _PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"BTC Signal Intelligence v4.0 APEX - Running OK")
    def log_message(self, fmt, *args):  # Silence noisy access logs
        pass

def _start_keepalive_server():
    port = int(os.getenv("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), _PingHandler)
    print(s(f"  Keepalive server listening on port {port} (Render/UptimeRobot)", GY))
    server.serve_forever()

# ═══════════════════════════════════════════════════════════════
#  MAIN LOOP
# ═══════════════════════════════════════════════════════════════

def main():
    cron_mode = "--cron" in sys.argv

    if not cron_mode:
        # Thread 1: HTTP keepalive server so Render treats this as a web service
        t1 = threading.Thread(target=_start_keepalive_server, daemon=True)
        t1.start()

        # Thread 2: Self-pinger — hits own public URL every 10 min to prevent sleep
        # RENDER_EXTERNAL_URL is automatically set by Render for all web services
        render_url = os.getenv("RENDER_EXTERNAL_URL", "")
        if render_url:
            def _self_ping():
                print(s(f"  Self-ping active -> {render_url} (every 10 min)", GY))
                while True:
                    time.sleep(600)
                    try:
                        requests.get(render_url, timeout=10)
                        print(s("  Self-ping OK", GY))
                    except Exception as pe:
                        print(s(f"  Self-ping warn: {pe}", GY))
            t2 = threading.Thread(target=_self_ping, daemon=True)
            t2.start()
        else:
            print(s("  Self-ping: local mode (no RENDER_EXTERNAL_URL)", GY))

    print_header()
    print(s(f"  Connecting to {EXCHANGE.upper()}...", GY), end="", flush=True)
    ex = connect()
    print(s(" connected.", BGN))
    
    if cron_mode:
        print(s("  Running in CRON mode (single execution).", CY))
        try:
            df15 = ind(fetch(ex, TF_15M))
            df1h = ind(fetch(ex, TF_1H))
            df4h = ind(fetch(ex, TF_4H))
            reg  = regime(df15, df1h, df4h)

            # v4.0: Fetch sentiment once per cron run
            sent = get_sentiment()
            print(s(f"  Sentiment: {sent['bias']} ({sent['value']}) — {sent['label']}", CY))

            print_status(df15, df1h, df4h, reg)
            sig = run_modes(df15, df1h, df4h, reg)

            if sig:
                print_signal(sig)
                send_tg(sig)
            else:
                print_tracker(df15, df1h, reg)
                print(s("  No signal found on this 15m close.", GY))
            sys.exit(0)
        except Exception as e:
            print(s(f"  CRON Error: {e}", RD))
            sys.exit(1)

    print(s("  Scanning every 60s. Tracker prints every 5 min.\n", GY))

    # FIX #5: track last signal by actual candle index, not loop count
    last_sig_candle = -SIGNAL_GAP
    # FIX #7: start at 1 so tracker doesn't fire on first (unsettled) loop
    loop_count = 1

    while True:
        try:
            df15 = ind(fetch(ex, TF_15M))
            df1h = ind(fetch(ex, TF_1H))
            df4h = ind(fetch(ex, TF_4H))

            reg = regime(df15, df1h, df4h)
            idx = len(df15)

            print_status(df15, df1h, df4h, reg)

            # FIX #5: use actual candle index gap
            candle_gap_ok = (idx - last_sig_candle) >= SIGNAL_GAP

            if candle_gap_ok:
                sig = run_modes(df15, df1h, df4h, reg)
                if sig:
                    print_signal(sig)
                    send_tg(sig)
                    last_sig_candle = idx
                    _reject_history.clear()    # v2.0: reset on successful signal
                else:
                    # Tracker every 5 loops (= ~5 minutes)
                    if loop_count % 5 == 0:
                        print_tracker(df15, df1h, reg)

            loop_count += 1
            time.sleep(60)

        except KeyboardInterrupt:
            print()
            print(s("  " + "─"*(W-2), GY))
            print(s("  Stopped. Protect your capital. Only trade what you can lose.", YL))
            print(s("  " + "─"*(W-2), GY))
            print()
            break
        except Exception as e:
            print(s(f"  Error: {e} — retrying in 30s", RD))
            time.sleep(30)

if __name__ == "__main__":
    main()
