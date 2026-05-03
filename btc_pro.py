"""
╔══════════════════════════════════════════════════════════════════╗
║      BTC SIGNAL INTELLIGENCE — PRODUCTION EDITION v3.0          ║
║                                                                  ║
║  UPGRADES OVER AUDITED EDITION:                                  ║
║  1. Anti-Repainting: All signals evaluated on CLOSED candles     ║
║  2. Dynamic Divergence: Real swing-high/low scanner (20 bars)    ║
║  3. Volatility-Adaptive SL/TP: Scales with ATR ratio            ║
║  4. Weighted Confidence Model: Volume/ADX/MTF bonuses            ║
║  5. VWAP Anchor Mode: Institutional reference level signals      ║
║  6. Engulfing Pattern Recognition: Candle pattern layer          ║
║  7. Smart Tracker: Shows correct filters per regime              ║
║  8. Session Stats: Live signal counter + L/S breakdown           ║
║  9. Swing Structure: Proper 5-bar pivot detection                ║
║ 10. Doji Filter: Blocks entry on indecision candles              ║
║                                                                  ║
║  INSTALL: pip install ccxt pandas numpy ta requests              ║
║  RUN:     python btc_pro.py                                      ║
╚══════════════════════════════════════════════════════════════════╝
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

EXCHANGE   = "bitget"        # bitget / binance / bybit
SYMBOL     = "BTC/USDT:USDT"
TF_15M     = "15m"
TF_1H      = "1h"
TF_4H      = "4h"
LIMIT      = 300

# ── Signal thresholds ──────────────────────────────────────────
RSI_OB         = 60     # overbought: short zone above this
RSI_OS         = 40     # oversold:   long zone below this
RSI_EXTREME_OB = 68     # strong overbought for reversal mode
RSI_EXTREME_OS = 32     # strong oversold for reversal mode
ADX_MIN        = 18     # trending market above this
ADX_STRONG     = 28     # strong trend — bonus confidence
VOL_MAX_WHALE  = 3.0    # spike above 3× avg → skip (news/whale)
VOL_MIN_SIGNAL = 1.0    # signal needs at least 1.0× avg volume
VOL_STRONG     = 2.0    # strong volume — bonus confidence
SIGNAL_GAP     = 4      # min candles between signals
DIV_LOOKBACK   = 20     # bars to scan for real swing pivots

# ── Adaptive Risk (base values — auto-scaled by ATR ratio) ─────
SL_ATR_BASE  = 1.2      # stop loss base multiplier
TP_ATR_BASE  = 2.6      # take profit base multiplier
MIN_RR       = 1.8      # skip signal if R:R below this
MIN_CONF     = 48       # minimum confidence (raised from 42)

# ── Telegram alerts (optional) ─────────────────────────────────
TG_TOKEN = ""
TG_CHAT  = ""

# ═══════════════════════════════════════════════════════════════
#  COLORS
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
BMG = "\033[95m"
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
    n   = max(0, min(width, int(val / 100 * width)))
    col = BGN if val >= 70 else (YL if val >= 50 else RD)
    return s("█" * n, col) + s("░" * (width - n), GY)

# ═══════════════════════════════════════════════════════════════
#  SESSION STATS
# ═══════════════════════════════════════════════════════════════

_session = {"total": 0, "long": 0, "short": 0, "last_dir": "—", "last_time": "—", "last_mode": "—"}

def record_signal(sig):
    _session["total"] += 1
    if sig["dir"] == "LONG":
        _session["long"] += 1
    else:
        _session["short"] += 1
    _session["last_dir"]  = sig["dir"]
    _session["last_time"] = sig["time"][-8:]   # HH:MM:SS only
    _session["last_mode"] = sig["mode"]

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

    df["rsi"]   = ta.momentum.RSIIndicator(c, 14).rsi()
    df["rsi1"]  = df["rsi"].shift(1)

    mc = ta.trend.MACD(c, 12, 26, 9)
    df["macd"]  = mc.macd()
    df["macds"] = mc.macd_signal()
    df["macdh"] = mc.macd_diff()
    df["macdh1"]= df["macdh"].shift(1)

    bb = ta.volatility.BollingerBands(c, 20, 2)
    df["bb_up"] = bb.bollinger_hband()
    df["bb_lo"] = bb.bollinger_lband()
    df["bb_w"]  = (df.bb_up - df.bb_lo) / c.replace(0, 1) * 100

    df["atr"]     = ta.volatility.AverageTrueRange(h, l, c, 14).average_true_range()
    df["atr_avg"] = df["atr"].rolling(20).mean()
    df["atr_ratio"]= df["atr"] / df["atr_avg"].replace(0, 1)

    df["vol_ma"]   = v.rolling(10).mean()
    df["vol_ratio"]= v / df["vol_ma"].replace(0, 1)

    sr = ta.momentum.StochRSIIndicator(c, 14, 3, 3)
    df["stk"] = sr.stochrsi_k() * 100
    df["std"] = sr.stochrsi_d() * 100

    adx = ta.trend.ADXIndicator(h, l, c, 14)
    df["adx"]  = adx.adx()
    df["adxp"] = adx.adx_pos()
    df["adxn"] = adx.adx_neg()

    df["body"] = abs(c - df.open)
    df["hiw"]  = h - df[["open","close"]].max(axis=1)
    df["low_"] = df[["open","close"]].min(axis=1) - l
    df["bull"] = (c > df.open).astype(int)

    # 5-bar pivot swing detection (more reliable than 3-bar)
    df["swing_hi"] = (
        (h > h.shift(1)) & (h > h.shift(2)) &
        (h > h.shift(-1)) & (h > h.shift(-2))
    )
    df["swing_lo"] = (
        (l < l.shift(1)) & (l < l.shift(2)) &
        (l < l.shift(-1)) & (l < l.shift(-2))
    )

    # Candle pattern flags
    df["engulf_bull"] = (
        (df.bull == 1) &
        (df.bull.shift(1) == 0) &
        (c > df.open.shift(1)) &
        (df.open < c.shift(1))
    )
    df["engulf_bear"] = (
        (df.bull == 0) &
        (df.bull.shift(1) == 1) &
        (c < df.open.shift(1)) &
        (df.open > c.shift(1))
    )
    df["doji"] = df["body"] < (df["atr"] * 0.15)

    # VWAP (cumulative from first bar of session data)
    df["tp_vwap"] = (h + l + c) / 3
    cum_vol       = v.cumsum()
    df["vwap"]    = (df["tp_vwap"] * v).cumsum() / cum_vol.replace(0, 1)

    return df
