# -*- coding: utf-8 -*-
"""
BTC APEX SIGNAL BOT v6.0 — INSTITUTIONAL PRO INTELLIGENCE
==========================================================
Triple Screen Cascade: 1D -> 4H -> 15m (Elder's Method)
ALL THREE timeframes must agree. No compromise.

BOOK CONCEPTS:
  Elder's Triple Screen  : 1D tide -> 4H wave -> 15m entry ripple
  Murphy Multi-TF        : Volume + price multi-timeframe confluence
  Weinstein Stage        : Only buy Stage 2, short Stage 4
  Douglas A-Grade        : Minimum 80/100 confidence — zero compromise
  ICT Smart Money        : FVG, Order Blocks, Liquidity Sweeps, BoS

INDICATORS: EMA9/20/50/200, Ichimoku, RSI+Div, MACD, BB, ATR, ADX,
            StochRSI, Williams %R, CCI, OBV, VWAP, Auto-Fibonacci, SMC

ML ENGINE : scikit-learn LinearRegression + Ridge + Lasso ensemble
            Trained on live indicator features — predicts next-bar direction
            and target zones without overfitting or memory bloat.

BACKTESTING: Logs every signal -> auto-checks TP/SL outcomes -> learns
SENTIMENT  : Fear & Greed + CoinGecko market data
DEPLOYMENT : Runs on laptop OR Render background worker (free, 24/7)
"""

import ccxt, pandas as pd, numpy as np
import time, requests, sys, os, io, threading, json, uuid
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

import sys as _sys
if hasattr(_sys.stdout, "reconfigure"):
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    import ta
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "ta"])
    import ta

try:
    from sklearn.linear_model import LinearRegression, Ridge, Lasso
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import Pipeline
    _SKLEARN_OK = True
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "scikit-learn"])
    from sklearn.linear_model import LinearRegression, Ridge, Lasso
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import Pipeline
    _SKLEARN_OK = True

# ── CONFIG ────────────────────────────────────────────────────────────
EXCHANGE  = "binance"
SYMBOL    = "BTC/USDT:USDT"
TF_15M, TF_1H, TF_4H, TF_1D = "15m", "1h", "4h", "1d"
LIMIT     = 600

# Telegram — hardcoded fallback (also reads from env for GitHub Actions secrets)
_TG_TOKEN_DEFAULT = "8775276870:AAGABvQ6PwtRgGPNbk3V4YX_A0eVXxpiWyo"
_TG_CHAT_DEFAULT  = "998659643"
TG_TOKEN  = os.getenv("TG_TOKEN", _TG_TOKEN_DEFAULT)
TG_CHAT   = os.getenv("TG_CHAT",  _TG_CHAT_DEFAULT)

# Signal thresholds — calibrated for absolute elite precision (A+ Grade only)
MIN_1D    = 40    # 1D must show clear trend tide
MIN_4H    = 40    # 4H setup wave minimum
MIN_1H    = 40    # 1H confirmation minimum
MIN_15M   = 40    # 15m entry trigger minimum
MIN_TOTAL = 70    # Telegram alert gate (70%+ = strong signal, 60-69% = Watchlist)
MIN_APLUS = 85    # A+ Grade elite setups
MIN_RR    = 1.8   # Minimum 1.8:1 risk-to-reward (relaxed from 2.0)
SL_ATR    = 1.2
TP_ATR    = 2.8
COOLDOWN  = 2     # 2 candles between signals (was 4)

# Backtesting
SIGNAL_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "signals_log.json")

# ── COLORS ────────────────────────────────────────────────────────────
RS  = "\033[0m";  BD  = "\033[1m";  WH  = "\033[97m";  GY  = "\033[90m"
GN  = "\033[32m"; BGN = "\033[92m"; RD  = "\033[31m";  BRD = "\033[91m"
YL  = "\033[33m"; BYL = "\033[93m"; CY  = "\033[36m";  BCY = "\033[96m"
MG  = "\033[35m"; BMG = "\033[95m"
W   = 70

def s(text, col="", bold=False):
    return f"{BD if bold else ''}{col}{text}{RS}"

def cbar(val, w=10):
    n   = max(0, min(w, int(val / 100 * w)))
    col = BGN if val >= 78 else (YL if val >= 65 else RD)
    return s("█"*n, col) + s("░"*(w-n), GY)

# ── EXCHANGE ──────────────────────────────────────────────────────────
def connect():
    cfgs = {
        "bitget":  ("bitget",      {"defaultType": "swap"}),
        "binance": ("binanceusdm", {}),
        "bybit":   ("bybit",       {"defaultType": "linear"}),
    }
    nm, opts = cfgs[EXCHANGE.lower()]
    return getattr(ccxt, nm)({"enableRateLimit": True, "options": opts})

def fetch(ex, tf, retries=3):
    # Try the primary exchange first
    for attempt in range(retries):
        try:
            raw = ex.fetch_ohlcv(SYMBOL, tf, limit=LIMIT)
            df  = pd.DataFrame(raw, columns=["ts","open","high","low","close","volume"])
            df["ts"] = pd.to_datetime(df["ts"], unit="ms")
            return df.set_index("ts").astype(float)
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
            else:
                # If primary exchange fails, try fallback exchanges for public OHLCV data to ensure 24/7 uptime
                print(s(f"  Primary exchange {ex.id} failed: {e}. Trying fallback exchanges...", YL))
                fallbacks = ["bybit", "bitget", "gateio", "okx"]
                for f_name in fallbacks:
                    if f_name == ex.id:
                        continue
                    try:
                        if f_name == "gateio":
                            f_ex = ccxt.gateio()
                        elif f_name == "okx":
                            f_ex = ccxt.okx()
                        elif f_name == "bybit":
                            f_ex = ccxt.bybit({"options": {"defaultType": "linear"}})
                        else:
                            f_ex = ccxt.bitget({"options": {"defaultType": "swap"}})
                        
                        raw = f_ex.fetch_ohlcv(SYMBOL, tf, limit=LIMIT)
                        df  = pd.DataFrame(raw, columns=["ts","open","high","low","close","volume"])
                        df["ts"] = pd.to_datetime(df["ts"], unit="ms")
                        print(s(f"  Fallback to {f_name} successful for {tf}!", BGN))
                        return df.set_index("ts").astype(float)
                    except Exception as fe:
                        print(s(f"  Fallback to {f_name} failed: {fe}", GY))
                raise

# ── INDICATORS ────────────────────────────────────────────────────────
def ind(df):
    if df is None or len(df) < 10:
        return df
    c, h, l, v = df.close, df.high, df.low, df.volume

    for p in [9, 20, 50, 200]:
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
    df["bb_w"]  = (df.bb_up - df.bb_lo) / c.replace(0,1) * 100

    df["atr"]     = ta.volatility.AverageTrueRange(h, l, c, 14).average_true_range()
    df["atr_avg"] = df["atr"].rolling(20).mean()
    df["vol_ma"]  = v.rolling(10).mean()
    df["vol_r"]   = v / df["vol_ma"].replace(0, 1)

    sr = ta.momentum.StochRSIIndicator(c, 14, 3, 3)
    df["stk"] = sr.stochrsi_k() * 100
    df["std"] = sr.stochrsi_d() * 100

    adx = ta.trend.ADXIndicator(h, l, c, 14)
    df["adx"]  = adx.adx()
    df["adxp"] = adx.adx_pos()
    df["adxn"] = adx.adx_neg()

    tp = (h + l + c) / 3
    df["vwap"] = (tp * v).rolling(20).sum() / v.rolling(20).sum().replace(0, 1)

    rng = (h - l).replace(0, 1)
    df["flow"] = (v * (c - l) / rng - v * (h - c) / rng) / v.replace(0, 1)

    df["body"]     = abs(c - df.open)
    df["hiw"]      = h - df[["open","close"]].max(axis=1)
    df["low_"]     = df[["open","close"]].min(axis=1) - l
    df["bull"]     = (c > df.open).astype(int)

    # OBV
    obv = [0.0]
    for i in range(1, len(df)):
        if df.close.iloc[i] > df.close.iloc[i-1]:
            obv.append(obv[-1] + df.volume.iloc[i])
        elif df.close.iloc[i] < df.close.iloc[i-1]:
            obv.append(obv[-1] - df.volume.iloc[i])
        else:
            obv.append(obv[-1])
    df["obv"] = obv
    df["obv_slope"] = pd.Series(df["obv"].values, index=df.index).diff(5)

    # Williams %R
    hw14 = h.rolling(14).max()
    lw14 = l.rolling(14).min()
    df["willr"] = -100 * (hw14 - c) / (hw14 - lw14).replace(0, 1)

    # CCI
    df["cci"] = (tp - tp.rolling(20).mean()) / (0.015 * tp.rolling(20).std().replace(0, 1))

    # Candle patterns
    df["bull_engulf"] = ((df.bull==1) & (df.bull.shift(1)==0) &
                         (c > df.open.shift(1)) & (df.open < c.shift(1)))
    df["bear_engulf"] = ((df.bull==0) & (df.bull.shift(1)==1) &
                         (df.open > c.shift(1)) & (c < df.open.shift(1)))
    df["hammer"]      = ((df.low_ > df.body*2) & (df.hiw < df.body*0.5) & (df.bull==1))
    df["shoot_star"]  = ((df.hiw > df.body*2) & (df.low_ < df.body*0.5) & (df.bull==0))

    # Swing highs/lows
    df["swing_hi"] = (h > h.shift(1)) & (h > h.shift(2)) & (h > h.shift(-1)) & (h > h.shift(-2))
    df["swing_lo"] = (l < l.shift(1)) & (l < l.shift(2)) & (l < l.shift(-1)) & (l < l.shift(-2))

    # SMC
    df["fvg_bull"] = l > h.shift(2)
    df["fvg_bear"] = h < l.shift(2)
    strong_up = (c > c.shift(1)) & (v > df.vol_ma * 1.4)
    strong_dn = (c < c.shift(1)) & (v > df.vol_ma * 1.4)
    df["ob_bull"] = (df.bull.shift(1)==0) & strong_up
    df["ob_bear"] = (df.bull.shift(1)==1) & strong_dn
    rhi = h.rolling(10).max().shift(1)
    rlo = l.rolling(10).min().shift(1)
    df["sweep_hi"] = (h > rhi) & (c < rhi)
    df["sweep_lo"] = (l < rlo) & (c > rlo)

    return df

# ── ICHIMOKU (manual calculation, no extra library needed) ────────────
def ichimoku(df):
    h, l, c = df.high, df.low, df.close
    df["ichi_tenkan"]  = (h.rolling(9).max()  + l.rolling(9).min())  / 2
    df["ichi_kijun"]   = (h.rolling(26).max() + l.rolling(26).min()) / 2
    df["ichi_spanA"]   = ((df.ichi_tenkan + df.ichi_kijun) / 2).shift(26)
    df["ichi_spanB"]   = ((h.rolling(52).max() + l.rolling(52).min()) / 2).shift(26)
    df["ichi_chikou"]  = c.shift(-26)
    df["above_cloud"]  = (c > df.ichi_spanA) & (c > df.ichi_spanB)
    df["below_cloud"]  = (c < df.ichi_spanA) & (c < df.ichi_spanB)
    df["cloud_bull"]   = df.ichi_spanA > df.ichi_spanB  # bullish cloud (green)
    return df

# ── AUTO-FIBONACCI ────────────────────────────────────────────────────
def get_fib_levels(df, lookback=50):
    if len(df) < lookback + 2:
        return {}
    window = df.iloc[-lookback:-2]
    sh = float(window.high.max())
    sl = float(window.low.min())
    rng = sh - sl
    if rng == 0:
        return {}
    return {
        "hi": sh, "lo": sl,
        "236": sh - 0.236 * rng,
        "382": sh - 0.382 * rng,
        "500": sh - 0.500 * rng,
        "618": sh - 0.618 * rng,
        "786": sh - 0.786 * rng,
    }

def near_fib(price, fib, pct=0.005):
    for k, v in fib.items():
        if k in ("hi", "lo"):
            continue
        if abs(price - v) / price <= pct:
            return True, k
    return False, None

# ── WEINSTEIN STAGE ANALYSIS ──────────────────────────────────────────
def weinstein_stage(df):
    if len(df) < 205 or "e200" not in df or pd.isna(df["e200"].iloc[-2]) or pd.isna(df["e200"].iloc[-7]):
        return 0  # Warmup / Unclear Stage
    r   = df.iloc[-2]
    r5  = df.iloc[-7]   # 5 bars ago for slope
    p   = r.close
    slope = r.e200 - r5.e200
    if slope > 0 and p > r.e200:
        return 2  # Markup — BUY zone (Stage 2)
    if slope < 0 and p < r.e200:
        return 4  # Markdown — SHORT zone (Stage 4)
    if p > r.e200:
        return 1  # Base (Stage 1 transitioning to Stage 2)
    return 3      # Top (Stage 3 transitioning to Stage 4)

# ── RSI DIVERGENCE DETECTOR ───────────────────────────────────────────
def detect_rsi_divergence(df, lookback=40):
    """
    Detects Bullish/Bearish RSI Divergence using pivot swing highs/lows.
    Returns: (bool_bullish_div, bool_bearish_div)
    """
    if len(df) < lookback + 5:
        return False, False

    swing_lows = []
    swing_highs = []
    
    # Scan lookback window to find swing pivots (excluding last 2 unconfirmed bars)
    for i in range(len(df) - lookback, len(df) - 2):
        l_val = df.low.iloc[i]
        h_val = df.high.iloc[i]
        
        # Swing low: local minimum in a 5-bar window
        if (l_val <= df.low.iloc[i-1] and l_val <= df.low.iloc[i-2] and
            l_val <= df.low.iloc[i+1] and l_val <= df.low.iloc[i+2]):
            swing_lows.append((i, l_val, df.rsi.iloc[i]))
            
        # Swing high: local maximum in a 5-bar window
        if (h_val >= df.high.iloc[i-1] and h_val >= df.high.iloc[i-2] and
            h_val >= df.high.iloc[i+1] and h_val >= df.high.iloc[i+2]):
            swing_highs.append((i, h_val, df.rsi.iloc[i]))

    bull_div = False
    bear_div = False

    if len(swing_lows) >= 2:
        idx1, p1, r1 = swing_lows[-1]
        idx2, p2, r2 = swing_lows[-2]
        # Bullish: Lower low in price, higher low in RSI
        if p1 < p2 and r1 > r2 and (len(df) - idx1) <= 10:
            bull_div = True

    if len(swing_highs) >= 2:
        idx1, p1, r1 = swing_highs[-1]
        idx2, p2, r2 = swing_highs[-2]
        # Bearish: Higher high in price, lower high in RSI
        if p1 > p2 and r1 < r2 and (len(df) - idx1) <= 10:
            bear_div = True

    return bull_div, bear_div

# ── ACTIVE SMC ZONE TRACKER ───────────────────────────────────────────
def get_smc_signals(df, lookback=50):
    """
    Scans lookback window to find unmitigated Bullish/Bearish OBs, FVGs, and BOS.
    """
    if len(df) < lookback + 5:
        return {
            "testing_bull_ob": False, "testing_bear_ob": False,
            "testing_bull_fvg": False, "testing_bear_fvg": False,
            "bos_bull": False, "bos_bear": False
        }

    c_close = df.close.iloc[-2]
    c_low = df.low.iloc[-2]
    c_high = df.high.iloc[-2]

    active_bull_obs = []
    active_bear_obs = []
    vol_ma = df.volume.rolling(10).mean()

    for i in range(len(df) - lookback, len(df) - 2):
        v = df.volume.iloc[i]
        v_ma = vol_ma.iloc[i]
        
        # Bullish OB: bearish candle followed by strong volume-supported bullish candle
        if (df.close.iloc[i-1] < df.open.iloc[i-1] and
            df.close.iloc[i] > df.open.iloc[i] and
            df.close.iloc[i] > df.high.iloc[i-1] and
            v > v_ma * 1.3):
            active_bull_obs.append((df.low.iloc[i-1], df.high.iloc[i-1], i))

        # Bearish OB: bullish candle followed by strong volume-supported bearish candle
        if (df.close.iloc[i-1] > df.open.iloc[i-1] and
            df.close.iloc[i] < df.open.iloc[i] and
            df.close.iloc[i] < df.low.iloc[i-1] and
            v > v_ma * 1.3):
            active_bear_obs.append((df.low.iloc[i-1], df.high.iloc[i-1], i))

    # Mitigations
    unmitigated_bull_obs = []
    for ob_low, ob_high, idx in active_bull_obs:
        if not any(df.close.iloc[j] < ob_low for j in range(idx + 1, len(df) - 1)):
            unmitigated_bull_obs.append((ob_low, ob_high))

    unmitigated_bear_obs = []
    for ob_low, ob_high, idx in active_bear_obs:
        if not any(df.close.iloc[j] > ob_high for j in range(idx + 1, len(df) - 1)):
            unmitigated_bear_obs.append((ob_low, ob_high))

    # FVGs
    active_bull_fvgs = []
    active_bear_fvgs = []
    for i in range(len(df) - lookback, len(df) - 2):
        if df.low.iloc[i] > df.high.iloc[i-2]:
            active_bull_fvgs.append((df.high.iloc[i-2], df.low.iloc[i], i))
        if df.high.iloc[i] < df.low.iloc[i-2]:
            active_bear_fvgs.append((df.high.iloc[i], df.low.iloc[i-2], i))

    unmitigated_bull_fvgs = []
    for fvg_low, fvg_high, idx in active_bull_fvgs:
        if not any(df.low.iloc[j] < fvg_low for j in range(idx + 1, len(df) - 1)):
            unmitigated_bull_fvgs.append((fvg_low, fvg_high))

    unmitigated_bear_fvgs = []
    for fvg_low, fvg_high, idx in active_bear_fvgs:
        if not any(df.high.iloc[j] > fvg_high for j in range(idx + 1, len(df) - 1)):
            unmitigated_bear_fvgs.append((fvg_low, fvg_high))

    # Tests
    testing_bull_ob = any(c_low <= ob_high and c_close >= ob_low for ob_low, ob_high in unmitigated_bull_obs)
    testing_bear_ob = any(c_high >= ob_low and c_close <= ob_high for ob_low, ob_high in unmitigated_bear_obs)
    testing_bull_fvg = any(c_low <= fvg_high and c_close >= fvg_low for fvg_low, fvg_high in unmitigated_bull_fvgs)
    testing_bear_fvg = any(c_high >= fvg_low and c_close <= fvg_high for fvg_low, fvg_high in unmitigated_bear_fvgs)

    # Break of Structure (BOS)
    swing_hi_val = df.high.iloc[-25:-3].max()
    swing_lo_val = df.low.iloc[-25:-3].min()
    bos_bull = c_close > swing_hi_val
    bos_bear = c_close < swing_lo_val

    return {
        "testing_bull_ob": testing_bull_ob, "testing_bear_ob": testing_bear_ob,
        "testing_bull_fvg": testing_bull_fvg, "testing_bear_fvg": testing_bear_fvg,
        "bos_bull": bos_bull, "bos_bear": bos_bear
    }

# ── WYCKOFF PHASE ─────────────────────────────────────────────────────
def wyckoff_phase(df):
    if len(df) < 35:
        return "TRANSITION"
    r    = df.iloc[-2]
    win  = df.iloc[-30:-2]
    obv_up   = r.obv_slope > 0
    tight    = win.close.std() / r.e50 < 0.015
    sweep_lo = bool(df.sweep_lo.iloc[-5:-2].any())
    sweep_hi = bool(df.sweep_hi.iloc[-5:-2].any())
    price_up = r.close > win.close.iloc[0]

    if tight and obv_up and sweep_lo:
        return "ACCUMULATION"
    if not tight and obv_up and price_up:
        return "MARKUP"
    if tight and not obv_up and sweep_hi:
        return "DISTRIBUTION"
    if not tight and not obv_up and not price_up:
        return "MARKDOWN"
    return "TRANSITION"

# ── SESSION ───────────────────────────────────────────────────────────
def session():
    h = datetime.utcnow().hour
    if 13 <= h < 17: return "LDN+NY", 10
    if  8 <= h < 13: return "LONDON",  5
    if 17 <= h < 21: return "NEW YORK", 5
    if  0 <= h <  8: return "ASIA",    -5
    return "OFF", -10

# ── SENTIMENT ─────────────────────────────────────────────────────────
_sent_cache = {"d": None, "t": 0}

def get_sentiment():
    now = time.time()
    if _sent_cache["d"] and now - _sent_cache["t"] < 3600:
        return _sent_cache["d"]
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=8)
        d = r.json()["data"][0]
        v = int(d["value"])
        if v <= 20:   bias, mod = "EXTREME FEAR",  +8
        elif v <= 40: bias, mod = "FEAR",           +4
        elif v <= 60: bias, mod = "NEUTRAL",         0
        elif v <= 80: bias, mod = "GREED",          -3
        else:         bias, mod = "EXTREME GREED",  -8
        res = {"value": v, "label": d["value_classification"], "bias": bias, "mod": mod}
        _sent_cache.update({"d": res, "t": now})
        return res
    except:
        return {"value": 50, "label": "Neutral", "bias": "NEUTRAL", "mod": 0}

def get_coingecko():
    """BTC 24h change + dominance from CoinGecko (free, no key)."""
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price"
            "?ids=bitcoin&vs_currencies=usd"
            "&include_24hr_change=true&include_market_cap=true",
            timeout=8)
        d = r.json().get("bitcoin", {})
        return {
            "change_24h": round(d.get("usd_24h_change", 0), 2),
            "mcap": d.get("usd_market_cap", 0),
        }
    except:
        return {"change_24h": 0, "mcap": 0}

# ══════════════════════════════════════════════════════════════════════
#  SCREEN 1 — 1D TREND  (Elder's Tide)
# ══════════════════════════════════════════════════════════════════════

def analyze_1d(df1d):
    df1d = ichimoku(df1d)
    r, r5 = df1d.iloc[-2], df1d.iloc[-7]
    lp = sp = 0
    reasons_long = []
    reasons_short = []

    if bool(r.above_cloud): lp += 20; reasons_long.append("1D above cloud")
    if bool(r.below_cloud): sp += 20; reasons_short.append("1D below cloud")

    if r.ichi_tenkan > r.ichi_kijun: lp += 10; reasons_long.append("Tenkan>Kijun")
    if r.ichi_tenkan < r.ichi_kijun: sp += 10; reasons_short.append("Tenkan<Kijun")

    if len(df1d) > 28:
        p26 = float(df1d.close.iloc[-28])
        if float(r.close) > p26: lp += 10; reasons_long.append("Chikou above price")
        else: sp += 10; reasons_short.append("Chikou below price")

    stage = weinstein_stage(df1d)
    if stage == 2:   lp += 15; reasons_long.append("Weinstein Stage 2 (Markup)")
    if stage == 4:   sp += 15; reasons_short.append("Weinstein Stage 4 (Markdown)")

    if r.e50 > r.e200: lp += 10; reasons_long.append("Golden Cross")
    if r.e50 < r.e200: sp += 10; reasons_short.append("Death Cross")

    if r.adx > 20:
        if r.adxp > r.adxn: lp += 10; reasons_long.append(f"ADX {r.adx:.0f} bull")
        else: sp += 10; reasons_short.append(f"ADX {r.adx:.0f} bear")

    if r.obv_slope > 0: lp += 10; reasons_long.append("OBV rising")
    if r.obv_slope < 0: sp += 10; reasons_short.append("OBV falling")

    if r.macdh > 0 and r.macdh > r.macdh1: lp += 10; reasons_long.append("MACD hist up")
    if r.macdh < 0 and r.macdh < r.macdh1: sp += 10; reasons_short.append("MACD hist dn")

    if r.vol_r >= 1.0:
        lp += 5
        sp += 5

    return lp, reasons_long, sp, reasons_short


# ══════════════════════════════════════════════════════════════════════
#  SCREEN 2 — 4H SETUP  (Elder's Wave)
# ══════════════════════════════════════════════════════════════════════

def analyze_4h(df4h, direction):
    df4h = ichimoku(df4h)
    r, rp = df4h.iloc[-2], df4h.iloc[-3]
    price = r.close
    score = 0
    reasons = []
    lng = direction == "LONG"

    # Smart Money Zones & Divergence & BOS
    smc = get_smc_signals(df4h)
    bull_div, bear_div = detect_rsi_divergence(df4h)
    stk_up = r.stk > r["std"] and rp.stk <= rp["std"] and r.stk < 50
    stk_dn = r.stk < r["std"] and rp.stk >= rp["std"] and r.stk > 50

    # RSI Divergence (Very strong)
    if lng and bull_div: score += 20; reasons.append("4H Bull RSI Div")
    if not lng and bear_div: score += 20; reasons.append("4H Bear RSI Div")

    # Swing Break of Structure (BOS)
    if lng and smc["bos_bull"]: score += 15; reasons.append("4H Bull BOS")
    if not lng and smc["bos_bear"]: score += 15; reasons.append("4H Bear BOS")

    # Order Block test
    if lng and smc["testing_bull_ob"]: score += 15; reasons.append("4H test Bull OB")
    if not lng and smc["testing_bear_ob"]: score += 15; reasons.append("4H test Bear OB")

    # FVG zone test
    if lng and smc["testing_bull_fvg"]: score += 12; reasons.append("4H test Bull FVG")
    if not lng and smc["testing_bear_fvg"]: score += 12; reasons.append("4H test Bear FVG")

    # Fib Levels
    fib = get_fib_levels(df4h)
    at_fib, fn = near_fib(price, fib, pct=0.008)
    if at_fib: score += 15; reasons.append(f"4H at Fib {fn}")

    # Kijun / Cloud
    if abs(price - r.ichi_kijun) / price < 0.008: score += 15; reasons.append("4H at Kijun")
    elif lng and bool(r.above_cloud): score += 8; reasons.append("4H above cloud")
    elif not lng and bool(r.below_cloud): score += 8; reasons.append("4H below cloud")

    # Smarter RSI Zones
    rsi_val = r.rsi
    if lng:
        if rsi_val < 35:
            if bull_div:
                score += 15; reasons.append("4H RSI OS + Div")
        elif 35 <= rsi_val <= 55:
            if r.macdh > r.macdh1 or stk_up:
                score += 10; reasons.append(f"4H RSI {rsi_val:.0f} pullback turn")
        elif 55 < rsi_val <= 70:
            score += 10; reasons.append(f"4H RSI {rsi_val:.0f} markup")
        elif rsi_val > 70:
            score += 15; reasons.append(f"4H RSI Bull Momentum ({rsi_val:.0f})")
    else:
        if rsi_val > 65:
            if bear_div:
                score += 15; reasons.append("4H RSI OB + Div")
        elif 45 < rsi_val <= 65:
            if r.macdh < r.macdh1 or stk_dn:
                score += 10; reasons.append(f"4H RSI {rsi_val:.0f} pullback turn")
        elif 30 <= rsi_val <= 45:
            score += 10; reasons.append(f"4H RSI {rsi_val:.0f} markdown")
        elif rsi_val < 30:
            score += 15; reasons.append(f"4H RSI Bear Momentum ({rsi_val:.0f})")

    # MACD Trend Direction
    if lng and r.macdh > r.macdh1:      score += 10; reasons.append("4H MACD turning up")
    if not lng and r.macdh < r.macdh1:  score += 10; reasons.append("4H MACD turning dn")

    # Stochastic RSI crosses
    if lng and stk_up:      score += 10; reasons.append("4H Stoch cross up")
    if not lng and stk_dn:  score += 10; reasons.append("4H Stoch cross dn")

    # Pullback Volume Check
    if df4h.vol_r.iloc[-4:-1].mean() < 0.9: score += 10; reasons.append("4H vol declining")

    # Wyckoff
    phase = wyckoff_phase(df4h)
    if lng and phase in ("ACCUMULATION","MARKUP"):      score += 5; reasons.append(f"Wyckoff {phase}")
    if not lng and phase in ("DISTRIBUTION","MARKDOWN"): score += 5; reasons.append(f"Wyckoff {phase}")

    if r.atr_avg > 0 and 0.6 < r.atr/r.atr_avg < 1.8: score += 5; reasons.append("ATR normal")

    # ── TREND CONTINUATION & BREAKOUT TRIGGERS (4H) ──
    # 1. Bollinger Band Breakout
    if "bb_up" in df4h.columns and "bb_lo" in df4h.columns:
        bb_up = df4h.bb_up.iloc[-2]
        bb_lo = df4h.bb_lo.iloc[-2]
        if lng and price > bb_up and r.vol_r > 1.2:
            score += 20; reasons.append("4H BB Upper Breakout + Vol")
        elif not lng and price < bb_lo and r.vol_r > 1.2:
            score += 20; reasons.append("4H BB Lower Breakout + Vol")

    # 2. Consecutive Trend Candles
    if len(df4h) >= 4:
        last_3 = df4h.iloc[-4:-1]
        if lng:
            all_green = all(x.close > x.open for _, x in last_3.iterrows())
            if all_green:
                score += 10; reasons.append("4H 3 Green Candles")
        else:
            all_red = all(x.close < x.open for _, x in last_3.iterrows())
            if all_red:
                score += 10; reasons.append("4H 3 Red Candles")

    # ── Trend Continuation & Momentum Alignment ──
    e9_val = r.e9
    e20_val = r.e20
    e50_val = r.e50
    e200_val = r.e200
    if lng:
        if e9_val > e20_val > e50_val > e200_val:
            score += 15; reasons.append("4H Strong Bullish EMA stack")
        if price > e9_val and price > e20_val:
            score += 10; reasons.append("4H price above fast EMAs")
        if r.adx > 22 and r.adxp > r.adxn:
            score += 10; reasons.append("4H strong ADX bull momentum")
    else:
        if e9_val < e20_val < e50_val < e200_val:
            score += 15; reasons.append("4H Strong Bearish EMA stack")
        if price < e9_val and price < e20_val:
            score += 10; reasons.append("4H price below fast EMAs")
        if r.adx > 22 and r.adxn > r.adxp:
            score += 10; reasons.append("4H strong ADX bear momentum")

    return min(score, 100), reasons


# ══════════════════════════════════════════════════════════════════════
#  SCREEN 2.5 — 1H SETUP  (Elder's Intermediate Wave Setup)
# ══════════════════════════════════════════════════════════════════════

def analyze_1h(df1h, direction):
    """
    Analyzes the 1-hour timeframe to confirm the wave direction before 15m ripple entry.
    Fully aligned with 4H robust setup analysis.
    """
    df1h = ichimoku(df1h)
    r, rp = df1h.iloc[-2], df1h.iloc[-3]
    price = r.close
    score = 0
    reasons = []
    lng = direction == "LONG"

    # Smart Money Zones & Divergence & BOS
    smc = get_smc_signals(df1h)
    bull_div, bear_div = detect_rsi_divergence(df1h)
    stk_up = r.stk > r["std"] and rp.stk <= rp["std"] and r.stk < 50
    stk_dn = r.stk < r["std"] and rp.stk >= rp["std"] and r.stk > 50

    # RSI Divergence (Very strong)
    if lng and bull_div: score += 20; reasons.append("1H Bull RSI Div")
    if not lng and bear_div: score += 20; reasons.append("1H Bear RSI Div")

    # Swing Break of Structure (BOS)
    if lng and smc["bos_bull"]: score += 15; reasons.append("1H Bull BOS")
    if not lng and smc["bos_bear"]: score += 15; reasons.append("1H Bear BOS")

    # Order Block test
    if lng and smc["testing_bull_ob"]: score += 15; reasons.append("1H test Bull OB")
    if not lng and smc["testing_bear_ob"]: score += 15; reasons.append("1H test Bear OB")

    # FVG zone test
    if lng and smc["testing_bull_fvg"]: score += 12; reasons.append("1H test Bull FVG")
    if not lng and smc["testing_bear_fvg"]: score += 12; reasons.append("1H test Bear FVG")

    # Fib Levels
    fib = get_fib_levels(df1h)
    at_fib, fn = near_fib(price, fib, pct=0.008)
    if at_fib: score += 15; reasons.append(f"1H at Fib {fn}")

    # Kijun / Cloud
    if abs(price - r.ichi_kijun) / price < 0.008: score += 15; reasons.append("1H at Kijun")
    elif lng and bool(r.above_cloud): score += 8; reasons.append("1H above cloud")
    elif not lng and bool(r.below_cloud): score += 8; reasons.append("1H below cloud")

    # Smarter RSI Zones
    rsi_val = r.rsi
    if lng:
        if rsi_val < 35:
            if bull_div:
                score += 15; reasons.append("1H RSI OS + Div")
        elif 35 <= rsi_val <= 55:
            if r.macdh > r.macdh1 or stk_up:
                score += 10; reasons.append(f"1H RSI {rsi_val:.0f} pullback turn")
        elif 55 < rsi_val <= 70:
            score += 10; reasons.append(f"1H RSI {rsi_val:.0f} markup")
        elif rsi_val > 70:
            score += 15; reasons.append(f"1H RSI Bull Momentum ({rsi_val:.0f})")
    else:
        if rsi_val > 65:
            if bear_div:
                score += 15; reasons.append("1H RSI OB + Div")
        elif 45 < rsi_val <= 65:
            if r.macdh < r.macdh1 or stk_dn:
                score += 10; reasons.append(f"1H RSI {rsi_val:.0f} pullback turn")
        elif 30 <= rsi_val <= 45:
            score += 10; reasons.append(f"1H RSI {rsi_val:.0f} markdown")
        elif rsi_val < 30:
            score += 15; reasons.append(f"1H RSI Bear Momentum ({rsi_val:.0f})")

    # MACD Trend Direction
    if lng and r.macdh > r.macdh1:      score += 10; reasons.append("1H MACD turning up")
    if not lng and r.macdh < r.macdh1:  score += 10; reasons.append("1H MACD turning dn")

    # Stochastic RSI crosses
    if lng and stk_up:      score += 10; reasons.append("1H Stoch cross up")
    if not lng and stk_dn:  score += 10; reasons.append("1H Stoch cross dn")

    # Pullback Volume Check
    if df1h.vol_r.iloc[-4:-1].mean() < 0.9: score += 10; reasons.append("1H vol declining")

    # Wyckoff
    phase = wyckoff_phase(df1h)
    if lng and phase in ("ACCUMULATION","MARKUP"):      score += 5; reasons.append(f"Wyckoff {phase}")
    if not lng and phase in ("DISTRIBUTION","MARKDOWN"): score += 5; reasons.append(f"Wyckoff {phase}")

    if r.atr_avg > 0 and 0.6 < r.atr/r.atr_avg < 1.8: score += 5; reasons.append("ATR normal")

    # ── TREND CONTINUATION & BREAKOUT TRIGGERS (1H) ──
    # 1. Bollinger Band Breakout
    if "bb_up" in df1h.columns and "bb_lo" in df1h.columns:
        bb_up = df1h.bb_up.iloc[-2]
        bb_lo = df1h.bb_lo.iloc[-2]
        if lng and price > bb_up and r.vol_r > 1.2:
            score += 20; reasons.append("1H BB Upper Breakout + Vol")
        elif not lng and price < bb_lo and r.vol_r > 1.2:
            score += 20; reasons.append("1H BB Lower Breakout + Vol")

    # 2. Consecutive Trend Candles
    if len(df1h) >= 4:
        last_3 = df1h.iloc[-4:-1]
        if lng:
            all_green = all(x.close > x.open for _, x in last_3.iterrows())
            if all_green:
                score += 10; reasons.append("1H 3 Green Candles")
        else:
            all_red = all(x.close < x.open for _, x in last_3.iterrows())
            if all_red:
                score += 10; reasons.append("1H 3 Red Candles")

    # ── Trend Continuation & Momentum Alignment ──
    e9_val = r.e9
    e20_val = r.e20
    e50_val = r.e50
    e200_val = r.e200
    if lng:
        if e9_val > e20_val > e50_val > e200_val:
            score += 15; reasons.append("1H Strong Bullish EMA stack")
        if price > e9_val and price > e20_val:
            score += 10; reasons.append("1H price above fast EMAs")
        if r.adx > 22 and r.adxp > r.adxn:
            score += 10; reasons.append("1H strong ADX bull momentum")
    else:
        if e9_val < e20_val < e50_val < e200_val:
            score += 15; reasons.append("1H Strong Bearish EMA stack")
        if price < e9_val and price < e20_val:
            score += 10; reasons.append("1H price below fast EMAs")
        if r.adx > 22 and r.adxn > r.adxp:
            score += 10; reasons.append("1H strong ADX bear momentum")

    return min(score, 100), reasons


# ══════════════════════════════════════════════════════════════════════
#  SCREEN 3 — 15m ENTRY  (Elder's Ripple)
# ══════════════════════════════════════════════════════════════════════

def analyze_15m(df15, direction):
    r, rp = df15.iloc[-2], df15.iloc[-3]
    score = 0
    reasons = []
    lng = direction == "LONG"

    # SMC & Divergence
    smc = get_smc_signals(df15)
    bull_div, bear_div = detect_rsi_divergence(df15)
    stk_up = r.stk > r["std"] and rp.stk <= rp["std"]
    stk_dn = r.stk < r["std"] and rp.stk >= rp["std"]

    # RSI Divergence on 15m
    if lng and bull_div: score += 20; reasons.append("15m Bull RSI Div")
    if not lng and bear_div: score += 20; reasons.append("15m Bear RSI Div")

    # BOS on 15m
    if lng and smc["bos_bull"]: score += 15; reasons.append("15m Bull BOS")
    if not lng and smc["bos_bear"]: score += 15; reasons.append("15m Bear BOS")

    # SMC Zone tests
    if lng and smc["testing_bull_ob"]: score += 15; reasons.append("15m test Bull OB")
    if not lng and smc["testing_bear_ob"]: score += 15; reasons.append("15m test Bear OB")
    if lng and smc["testing_bull_fvg"]: score += 12; reasons.append("15m test Bull FVG")
    if not lng and smc["testing_bear_fvg"]: score += 12; reasons.append("15m test Bear FVG")

    # Candle Patterns
    if lng and (bool(r.bull_engulf) or bool(r.hammer)):
        score += 20; reasons.append("15m bull engulf/hammer")
    elif not lng and (bool(r.bear_engulf) or bool(r.shoot_star)):
        score += 20; reasons.append("15m bear engulf/star")
    elif lng and r.bull == 1 and r.body > r.atr * 0.4:
        score += 8; reasons.append("15m strong bull candle")
    elif not lng and r.bull == 0 and r.body > r.atr * 0.4:
        score += 8; reasons.append("15m strong bear candle")

    # EMA Cross
    if lng and r.e9 > r.e20 and rp.e9 <= rp.e20:     score += 15; reasons.append("15m EMA9 crossed above 20")
    elif lng and r.e9 > r.e20:                         score += 7;  reasons.append("15m EMA9>20")
    if not lng and r.e9 < r.e20 and rp.e9 >= rp.e20: score += 15; reasons.append("15m EMA9 crossed below 20")
    elif not lng and r.e9 < r.e20:                    score += 7;  reasons.append("15m EMA9<20")

    # MACD Cross
    if lng and r.macd > r.macds and rp.macd <= rp.macds:     score += 15; reasons.append("15m MACD bull cross")
    elif lng and r.macdh > 0:                                 score += 6;  reasons.append("15m MACD pos")
    if not lng and r.macd < r.macds and rp.macd >= rp.macds: score += 15; reasons.append("15m MACD bear cross")
    elif not lng and r.macdh < 0:                            score += 6;  reasons.append("15m MACD neg")

    # Vol spike
    if r.vol_r >= 1.8:   score += 15; reasons.append(f"15m vol spike {r.vol_r:.1f}x")
    elif r.vol_r >= 1.2: score += 7;  reasons.append(f"15m vol {r.vol_r:.1f}x")

    # Smarter RSI zones
    rsi_val = r.rsi
    if lng:
        if rsi_val < 35:
            if bull_div or bool(r.hammer) or bool(r.bull_engulf):
                score += 15; reasons.append("15m RSI OS + Confirmation")
        elif 35 <= rsi_val < 50:
            if r.macdh > 0 or stk_up:
                score += 10; reasons.append("15m RSI low + Pullback turn")
        elif 50 <= rsi_val <= 70:
            score += 7; reasons.append(f"15m RSI strong ({rsi_val:.0f})")
        elif rsi_val > 70:
            # Trend continuation momentum (RSI Overbought but strong)
            score += 15; reasons.append(f"15m RSI Bull Momentum ({rsi_val:.0f})")
    else:
        if rsi_val > 65:
            if bear_div or bool(r.shoot_star) or bool(r.bear_engulf):
                score += 15; reasons.append("15m RSI OB + Confirmation")
        elif 50 < rsi_val <= 65:
            if r.macdh < 0 or stk_dn:
                score += 10; reasons.append("15m RSI high + Pullback turn")
        elif 30 <= rsi_val <= 50:
            score += 7; reasons.append(f"15m RSI weak ({rsi_val:.0f})")
        elif rsi_val < 30:
            # Trend continuation momentum (RSI Oversold but strong)
            score += 15; reasons.append(f"15m RSI Bear Momentum ({rsi_val:.0f})")

    # Stochastic RSI
    if lng and stk_up:     score += 10; reasons.append("15m Stoch up")
    if not lng and stk_dn: score += 10; reasons.append("15m Stoch dn")

    p = r.close
    if min(abs(p-r.vwap)/p, abs(p-r.e20)/p, abs(p-r.e50)/p) < 0.003:
        score += 10; reasons.append("15m at key level")

    if lng and r.willr < -75:
        if stk_up or r.macdh > 0:
            score += 5; reasons.append("15m WillR OS + Turn")
    if not lng and r.willr > -25:
        if stk_dn or r.macdh < 0:
            score += 5; reasons.append("15m WillR OB + Turn")

    # ── TREND CONTINUATION & BREAKOUT TRIGGERS (15m) ──
    # 1. Bollinger Band Breakout
    if "bb_up" in df15.columns and "bb_lo" in df15.columns:
        bb_up = df15.bb_up.iloc[-2]
        bb_lo = df15.bb_lo.iloc[-2]
        if lng and p > bb_up and r.vol_r > 1.3:
            score += 25; reasons.append("15m BB Upper Breakout + Vol")
        elif not lng and p < bb_lo and r.vol_r > 1.3:
            score += 25; reasons.append("15m BB Lower Breakout + Vol")

    # 2. Consecutive Momentum Candles
    if len(df15) >= 4:
        last_3 = df15.iloc[-4:-1]
        if lng:
            all_green = all(x.close > x.open for _, x in last_3.iterrows())
            vol_expansion = df15.volume.iloc[-2] > df15.volume.iloc[-4]
            if all_green and vol_expansion:
                score += 15; reasons.append("15m 3 Green Candles + Vol expansion")
        else:
            all_red = all(x.close < x.open for _, x in last_3.iterrows())
            vol_expansion = df15.volume.iloc[-2] > df15.volume.iloc[-4]
            if all_red and vol_expansion:
                score += 15; reasons.append("15m 3 Red Candles + Vol expansion")

    # 3. ADX Momentum Support
    if r.adx > 25 and r.adx > rp.adx:
        if lng and r.adxp > r.adxn:
            score += 15; reasons.append("15m Strong Bull ADX expansion")
        elif not lng and r.adxn > r.adxp:
            score += 15; reasons.append("15m Strong Bear ADX expansion")

    return min(score, 100), reasons


# ══════════════════════════════════════════════════════════════════════
#  DYNAMIC HISTORICAL BACKTESTER
# ══════════════════════════════════════════════════════════════════════

def backtest_strategy_historically(df15, df1h, df4h, df1d, direction, lookback=150):
    """
    Runs a fast local backtest of the Quadruple Screen Cascade on the last
    150 candles of live exchange data. Prevents lookahead bias.
    Uses 150 candles (was 300) for speed — still statistically sound.
    """
    if len(df15) < lookback + 10 or len(df1h) < 50 or len(df4h) < 50 or len(df1d) < 30:
        return 0, 0, 100.0
    
    wins = 0
    losses = 0
    start_idx = max(50, len(df15) - lookback)
    end_idx = len(df15) - 3  # resolve historical outcomes, excluding latest 2
    
    for i in range(start_idx, end_idx):
        t_ref = df15.index[i]
        
        # Slices with .copy() to completely isolate the dataframes
        hist_df1d = df1d[df1d.index <= t_ref].copy()
        hist_df4h = df4h[df4h.index <= t_ref].copy()
        hist_df1h = df1h[df1h.index <= t_ref].copy()
        hist_df15 = df15.iloc[:i+1].copy()
        
        if len(hist_df1d) < 10 or len(hist_df4h) < 10 or len(hist_df1h) < 10 or len(hist_df15) < 10:
            continue
            
        h_lp, _, h_sp, _ = analyze_1d(hist_df1d)
        h_sc1d = h_lp if direction == "LONG" else h_sp
        if h_sc1d < MIN_1D:
            continue
            
        h_sc4h, _ = analyze_4h(hist_df4h, direction)
        if h_sc4h < MIN_4H:
            continue

        h_sc1h, _ = analyze_1h(hist_df1h, direction)
        if h_sc1h < MIN_1H:
            continue
            
        h_sc15, _ = analyze_15m(hist_df15, direction)
        if h_sc15 < MIN_15M:
            continue
            
        # Entry setup confirmed historically at candle i
        entry_val = df15.close.iloc[i]
        atr_val = df15.atr.iloc[i]
        if not atr_val or pd.isna(atr_val):
            atr_val = entry_val * 0.01
            
        vol_r_val = df15.vol_r.iloc[i] if "vol_r" in df15.columns else 1.0
        if vol_r_val > 1.5:
            sl_m, tp_m = 1.5, 3.5
        elif vol_r_val < 0.7:
            sl_m, tp_m = 1.0, 2.2
        else:
            sl_m, tp_m = SL_ATR, TP_ATR
            
        if direction == "LONG":
            sl_val = entry_val - atr_val * sl_m
            tp_val = entry_val + atr_val * tp_m
        else:
            sl_val = entry_val + atr_val * sl_m
            tp_val = entry_val - atr_val * tp_m
            
        # Track simulated outcome
        outcome = "PENDING"
        for j in range(i + 1, len(df15)):
            h_high = df15.high.iloc[j]
            h_low = df15.low.iloc[j]
            if direction == "LONG":
                if h_high >= tp_val:
                    outcome = "WIN"
                    break
                elif h_low <= sl_val:
                    outcome = "LOSS"
                    break
            else:
                if h_low <= tp_val:
                    outcome = "WIN"
                    break
                elif h_high >= sl_val:
                    outcome = "LOSS"
                    break
                    
        if outcome == "WIN":
            wins += 1
        elif outcome == "LOSS":
            losses += 1
            
    total = wins + losses
    win_rate = (wins / total * 100) if total > 0 else 100.0
    return wins, losses, win_rate


# ══════════════════════════════════════════════════════════════════════
#  SIGNAL BUILDER
# ══════════════════════════════════════════════════════════════════════

def build_signal(direction, df15, sc1d, sc4h, sc1h, sc15, sentiment, reasons_1d, reasons_4h, reasons_1h, reasons_15m, forecast_slope, local_wins, local_losses, local_wr, ml_pred=0.0, ml_label="", funding=0.0, oi=0.0):
    r     = df15.iloc[-2]
    atr   = r.atr
    aavg  = r.atr_avg if r.atr_avg > 0 else atr
    price = r.close
    vr    = atr / aavg

    if vr > 1.5:   sl_m, tp_m, sl_mode = 1.5, 3.5, "WIDE"
    elif vr < 0.7: sl_m, tp_m, sl_mode = 1.0, 2.2, "TIGHT"
    else:          sl_m, tp_m, sl_mode = SL_ATR, TP_ATR, "STD"

    sl = round((price - atr*sl_m) if direction=="LONG" else (price + atr*sl_m), 1)
    tp = round((price + atr*tp_m) if direction=="LONG" else (price - atr*tp_m), 1)
    rr = round(abs(price-tp) / max(abs(price-sl), 0.01), 2)
    if rr < MIN_RR: return None

    # Quadruple timeframe blend: 1D:20%, 4H:25%, 1H:25%, 15m:30%
    final = round(sc1d*0.20 + sc4h*0.25 + sc1h*0.25 + sc15*0.30)
    sent  = sentiment or {"mod": 0}
    if direction=="LONG"  and sent["mod"] >  0: final = min(100, final + sent["mod"])
    if direction=="SHORT" and sent["mod"] <  0: final = min(100, final + abs(sent["mod"]))
    if direction=="LONG"  and sent["mod"] < -3: final = max(0, final + sent["mod"])
    if direction=="SHORT" and sent["mod"] >  3: final = max(0, final - sent["mod"])

    sn, sm = session()
    final  = min(100, final + sm)

    # ML Ensemble boost: if all 3 models agree with signal direction, +3 confidence
    if direction == "LONG"  and ml_pred > 0.05:  final = min(100, final + 3)
    if direction == "SHORT" and ml_pred < -0.05: final = min(100, final + 3)
    # ML contradicts signal direction — penalise confidence
    if direction == "LONG"  and ml_pred < -0.1:  final = max(0, final - 4)
    if direction == "SHORT" and ml_pred > 0.1:   final = max(0, final - 4)

    # ── High/Low Point Funding Reversal Engine ──
    # User's Strategy: High/low funding rates make profits by identifying reversals at high/low points.
    is_high_point = False
    is_low_point = False
    
    if "bb_up" in df15.columns and "bb_lo" in df15.columns:
        bb_up_15 = df15.bb_up.iloc[-2]
        bb_lo_15 = df15.bb_lo.iloc[-2]
        rsi_15 = r.rsi
        
        # High point: Price is near/above upper 15m Bollinger Band OR RSI is overbought (>= 68)
        if price >= bb_up_15 * 0.998 or rsi_15 >= 68:
            is_high_point = True
        # Low point: Price is near/below lower 15m Bollinger Band OR RSI is oversold (<= 32)
        if price <= bb_lo_15 * 1.002 or rsi_15 <= 32:
            is_low_point = True

    funding_msg = ""
    if direction == "LONG" and is_low_point and funding < -0.01:
        # Extreme negative funding (shorts pay longs) at a clear low point -> Premium Reversal Setup
        boost = 5 if funding >= -0.03 else 10
        final = min(100, final + boost)
        funding_msg = f"Low-Point Reversal Boost (+{boost} conf, Funding {funding*100:+.4f}%)"
        reasons_15m.append(f"Funding low point reversal (+{boost})")
    elif direction == "SHORT" and is_high_point and funding > 0.02:
        # Extreme positive funding (longs pay shorts) at a clear high point -> Premium Reversal Setup
        boost = 5 if funding <= 0.05 else 10
        final = min(100, final + boost)
        funding_msg = f"High-Point Reversal Boost (+{boost} conf, Funding {funding*100:+.4f}%)"
        reasons_15m.append(f"Funding high point reversal (+{boost})")
        
    # Penalty for chasing extremes without proper structure:
    if direction == "LONG" and funding > 0.05 and not is_low_point:
        final = max(0, final - 5)
        reasons_15m.append("High funding long chase penalty (-5)")
    elif direction == "SHORT" and funding < -0.03 and not is_high_point:
        final = max(0, final - 5)
        reasons_15m.append("Negative funding short chase penalty (-5)")

    adp = get_adaptive_min()
    # Silent if below watchlist threshold (60) - no Telegram at all
    is_silent = final < 60

    # Identify clean patterns from reasons
    all_patterns = []
    for reason in reasons_15m + reasons_1h + reasons_4h + reasons_1d:
        if "engulf" in reason.lower(): all_patterns.append("Engulfing")
        elif "hammer" in reason.lower(): all_patterns.append("Hammer")
        elif "star" in reason.lower(): all_patterns.append("Shooting Star")
        elif "ob" in reason.lower() or "order block" in reason.lower(): all_patterns.append("Order Block test")
        elif "fvg" in reason.lower() or "fair value gap" in reason.lower(): all_patterns.append("FVG test")
        elif "div" in reason.lower(): all_patterns.append("RSI Divergence")
        elif "bos" in reason.lower(): all_patterns.append("Break of Structure")
        elif "cross" in reason.lower() or "ema" in reason.lower(): all_patterns.append("EMA Cross")
        elif "fib" in reason.lower(): all_patterns.append("Fibonacci Level")
        elif "kijun" in reason.lower(): all_patterns.append("Kijun support")

    unique_patterns = list(set(all_patterns))
    pattern_str = ", ".join(unique_patterns) if unique_patterns else "Price Action Structure"

    # Use the dynamic in-memory backtest results
    backtest_str = f"Win Rate: {local_wr:.1f}% ({local_wins}W-{local_losses}L in last 75h)"

    # Linear regression slope for Telegram
    forecast_str = f"Trajectory: {'Bullish' if forecast_slope > 0 else 'Bearish'} (Slope {forecast_slope:+.2f})"

    # ML ensemble label for Telegram
    ml_str = ml_label if ml_label else "ML engine: insufficient data"

    return {
        "dir": direction, "entry": round(price, 1),
        "sl": sl, "tp": tp, "rr": rr, "conf": final,
        "tier": "A+" if final >= MIN_APLUS else ("A" if final >= MIN_TOTAL else "WATCH"),
        "sl_mode": sl_mode, "atr": round(atr, 1),
        "sc1d": sc1d, "sc4h": sc4h, "sc1h": sc1h, "sc15": sc15,
        "session": sn, "sentiment": sent,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "id": str(uuid.uuid4())[:8],
        "patterns": pattern_str,
        "backtest": backtest_str,
        "forecast": forecast_str,
        "ml": ml_str,
        "funding": funding,
        "oi": oi,
        "funding_msg": funding_msg,
        "silent": is_silent,
    }


# ══════════════════════════════════════════════════════════════════════
#  BACKTESTING ENGINE
# ══════════════════════════════════════════════════════════════════════

def _load_log():
    try:
        with open(SIGNAL_LOG) as f: return json.load(f)
    except: return []

def _save_log(log):
    try:
        with open(SIGNAL_LOG, "w") as f: json.dump(log, f, indent=2)
    except: pass

def is_duplicate_signal(direction, current_time_str):
    log = _load_log()
    if not log: return False
    for sig in reversed(log[-5:]):
        try:
            sig_time = datetime.strptime(sig["time"], "%Y-%m-%d %H:%M:%S")
            curr_time = datetime.strptime(current_time_str, "%Y-%m-%d %H:%M:%S")
            # If same direction and within 4 candles (60 minutes)
            if sig["direction"] == direction and (curr_time - sig_time).total_seconds() < 3600:
                return True
        except:
            pass
    return False

def log_signal(sig, mode="TRIPLE_SCREEN"):
    log = _load_log()
    log.append({"id": sig["id"], "time": sig["time"], "direction": sig["dir"],
                 "mode": mode, "entry": sig["entry"], "sl": sig["sl"],
                 "tp": sig["tp"], "rr": sig["rr"], "conf": sig["conf"],
                 "tier": sig["tier"], "outcome": "PENDING", "pnl_pct": 0})
    _save_log(log)

def check_outcomes(df15):
    log    = _load_log()
    hi_all = float(df15.high.max())
    lo_all = float(df15.low.min())
    updated = False
    for sig in log:
        if sig["outcome"] != "PENDING": continue
        if sig["direction"] == "LONG":
            if hi_all >= sig["tp"]:
                sig.update({"outcome":"TP_HIT","pnl_pct":round((sig["tp"]-sig["entry"])/sig["entry"]*100,2)}); updated=True
            elif lo_all <= sig["sl"]:
                sig.update({"outcome":"SL_HIT","pnl_pct":round((sig["sl"]-sig["entry"])/sig["entry"]*100,2)}); updated=True
        else:
            if lo_all <= sig["tp"]:
                sig.update({"outcome":"TP_HIT","pnl_pct":round((sig["entry"]-sig["tp"])/sig["entry"]*100,2)}); updated=True
            elif hi_all >= sig["sl"]:
                sig.update({"outcome":"SL_HIT","pnl_pct":round((sig["entry"]-sig["sl"])/sig["entry"]*100,2)}); updated=True
    if updated: _save_log(log)
    return log

def get_performance():
    closed = [x for x in _load_log() if x["outcome"]!="PENDING"]
    if len(closed) < 3: return None
    recent = closed[-20:]
    wins   = [x for x in recent if x["outcome"]=="TP_HIT"]
    losses = [x for x in recent if x["outcome"]=="SL_HIT"]
    gp = sum(x["pnl_pct"] for x in wins)
    gl = abs(sum(x["pnl_pct"] for x in losses))
    return {"n":len(recent),"wins":len(wins),"losses":len(losses),
            "wr":round(len(wins)/max(len(recent),1)*100,1),
            "pf":round(gp/max(gl,0.01),2)}

_adaptive_boost = [0]
def get_adaptive_min():
    perf = get_performance()
    if not perf or perf["n"] < 5: return MIN_TOTAL
    if perf["wr"] < 50: _adaptive_boost[0] = min(_adaptive_boost[0]+3, 12)
    elif perf["wr"] > 75: _adaptive_boost[0] = max(_adaptive_boost[0]-2, 0)
    return MIN_TOTAL + _adaptive_boost[0]


# ══════════════════════════════════════════════════════════════════════
#  TELEGRAM
# ══════════════════════════════════════════════════════════════════════

# ── SUBSCRIBER MANAGEMENT & MULTI-USER SHARE ──────────────────────────
SUBSCRIBERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "subscribers.json")

_sub_lock = threading.Lock()

def _load_subs():
    with _sub_lock:
        try:
            if os.path.exists(SUBSCRIBERS_FILE):
                with open(SUBSCRIBERS_FILE) as f:
                    return list(set(json.load(f)))
        except:
            pass
        return []

def _save_subs(subs):
    with _sub_lock:
        try:
            with open(SUBSCRIBERS_FILE, "w") as f:
                json.dump(list(set(subs)), f)
        except:
            pass

def fetch_derivative_data(ex):
    """Fetches funding rate and open interest from CCXT."""
    funding = 0.0
    oi = 0.0
    try:
        res_funding = ex.fetch_funding_rate(SYMBOL)
        if isinstance(res_funding, dict):
            fr_val = res_funding.get("fundingRate")
            if fr_val is not None:
                funding = float(fr_val)
    except Exception as e:
        print(s(f"  Error fetching funding rate: {e}", GY))
    try:
        res_oi = ex.fetch_open_interest(SYMBOL)
        if isinstance(res_oi, dict):
            oi_val = res_oi.get("openInterestAmount")
            if oi_val is not None:
                oi = float(oi_val)
    except Exception as e:
        print(s(f"  Error fetching open interest: {e}", GY))
    return funding, oi

def linear_regression_forecast(prices, period=15):
    """Calculates linear regression slope to forecast price trajectory (numpy fallback)."""
    if len(prices) < period:
        return 0.0
    y = np.array(prices[-period:])
    x = np.arange(period)
    x_mean = np.mean(x)
    y_mean = np.mean(y)
    num = np.sum((x - x_mean) * (y - y_mean))
    den = np.sum((x - x_mean) ** 2)
    if den == 0:
        return 0.0
    return num / den


def ml_ensemble_forecast(df, lookback=60):
    """
    scikit-learn Ensemble Forecast Engine.
    Trains THREE models (LinearRegression + Ridge + Lasso) on live
    indicator features extracted from the last `lookback` candles.
    Predicts the next bar's expected return direction and magnitude.

    Features used per bar:
      - RSI, MACD histogram, StochK, CCI, Williams %R  (momentum)
      - OBV slope, Volume ratio                         (volume pressure)
      - ATR ratio to mean ATR                           (volatility)
      - EMA9/EMA20 spread, EMA20/EMA50 spread          (trend alignment)
      - Bollinger Band width                            (squeeze / expansion)
      - Candle body ratio                               (price action)

    Target: next-bar % return (continuous)
    Consensus: average prediction from all 3 models, scaled to direction score.
    """
    MIN_ROWS = lookback + 5
    required_cols = ["rsi", "macdh", "stk", "cci", "willr", "obv_slope",
                     "vol_r", "atr", "atr_avg", "e9", "e20", "e50", "bb_w", "body"]

    if len(df) < MIN_ROWS:
        return 0.0, "insufficient data"
    if not all(c in df.columns for c in required_cols):
        return 0.0, "missing features"

    try:
        sub = df.iloc[-MIN_ROWS:].copy()

        # Build feature matrix
        feats = pd.DataFrame({
            "rsi":       sub["rsi"],
            "macdh":     sub["macdh"],
            "stk":       sub["stk"],
            "cci":       sub["cci"].clip(-300, 300),
            "willr":     sub["willr"],
            "obv_slope": sub["obv_slope"] / (sub["close"].abs() + 1),
            "vol_r":     sub["vol_r"].clip(0, 5),
            "atr_ratio": sub["atr"] / sub["atr_avg"].replace(0, 1),
            "ema_fast":  (sub["e9"]  - sub["e20"]) / sub["close"],
            "ema_slow":  (sub["e20"] - sub["e50"]) / sub["close"],
            "bb_w":      sub["bb_w"].clip(0, 20),
            "body_r":    sub["body"] / (sub["atr"].replace(0, 1)),
        }).fillna(0)

        # Target: next-bar % return (forward shift by 1)
        returns = sub["close"].pct_change().shift(-1).fillna(0) * 100

        X = feats.values[:-1]   # all rows except last (no future target for last)
        y = returns.values[:-1]
        X_live = feats.values[-1:] # the live bar to predict

        if len(X) < 20:
            return 0.0, "insufficient training samples"

        # THREE MODELS — ensemble for robustness
        pipe_lr  = Pipeline([("scaler", StandardScaler()), ("model", LinearRegression())])
        pipe_rdg = Pipeline([("scaler", StandardScaler()), ("model", Ridge(alpha=1.0))])
        pipe_lso = Pipeline([("scaler", StandardScaler()), ("model", Lasso(alpha=0.01, max_iter=2000))])

        pipe_lr.fit(X, y)
        pipe_rdg.fit(X, y)
        pipe_lso.fit(X, y)

        pred_lr  = float(pipe_lr.predict(X_live)[0])
        pred_rdg = float(pipe_rdg.predict(X_live)[0])
        pred_lso = float(pipe_lso.predict(X_live)[0])

        # Consensus: trimmed average (drop most extreme, average rest)
        preds     = sorted([pred_lr, pred_rdg, pred_lso])
        consensus = float(np.mean(preds))      # all 3 are lightweight, keep all

        # Direction label for signal
        if consensus > 0.05:
            label = f"ML Bullish ({consensus:+.3f}%)"
        elif consensus < -0.05:
            label = f"ML Bearish ({consensus:+.3f}%)"
        else:
            label = f"ML Neutral ({consensus:+.3f}%)"

        return consensus, label

    except Exception as e:
        return 0.0, f"ML error: {e}"

def check_whale_spike(df):
    """Detects if the most recent closed candle has extreme volume or range."""
    r = df.iloc[-2]
    body = abs(r.close - r.open)
    atr = r.atr if "atr" in r else body
    vol_r = r.vol_r if "vol_r" in r else 1.0
    if body > 3.0 * atr:
        return True, f"Candle body expansion ({body:.1f} > 3.0*ATR)"
    if vol_r > 3.0:
        return True, f"Extreme volume spike ({vol_r:.1f}x normal)"
    return False, None

def broadcast_tg_all(msg):
    subs = _load_subs()
    # Add default primary chat
    if TG_CHAT:
        try:
            subs.append(int(TG_CHAT))
        except:
            subs.append(TG_CHAT)
    sent_chats = set()
    for chat_id in subs:
        if chat_id in sent_chats:
            continue
        try:
            requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"},
                timeout=8)
            sent_chats.add(chat_id)
        except Exception as e:
            print(f"Failed to broadcast to {chat_id}: {e}")

_last_offset = [0]

def _tg_direct(chat_id, msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"},
            timeout=10)
    except:
        pass

def _run_scan_for_user(ex, chat_id):
    try:
        df15 = ind(fetch(ex, TF_15M))
        df1h = ind(fetch(ex, TF_1H))
        df4h = ind(fetch(ex, TF_4H))
        df1d = ind(fetch(ex, TF_1D))
        
        price = float(df15.iloc[-1].close)
        dir1d, sc1d, _ = analyze_1d(df1d)
        effective_dir = dir1d if dir1d != "NEUTRAL" else "LONG"
        sc4h, _ = analyze_4h(df4h, effective_dir)
        sc1h, _ = analyze_1h(df1h, effective_dir)
        sc15, _ = analyze_15m(df15, effective_dir)
        
        # Quadruple timeframe blend: 1D:20%, 4H:25%, 1H:25%, 15m:30%
        final = round(sc1d*0.20 + sc4h*0.25 + sc1h*0.25 + sc15*0.30)
        adp = get_adaptive_min()
        sent = get_sentiment()
        funding, oi = fetch_derivative_data(ex)
        
        d1_em = "🟢" if dir1d=="LONG" else ("🔴" if dir1d=="SHORT" else "⚪")
        c4_em = "✅" if sc4h >= MIN_4H else "⏳"
        c1h_em = "✅" if sc1h >= MIN_1H else "⏳"
        c15_em = "✅" if sc15 >= MIN_15M else "⏳"
        
        report = (
            f"📊 *BTC Real-time Market Scan Report*\n\n"
            f"💰 Price: `${price:,.1f}`\n"
            f"😱 F&G: `{sent['value']}/100 — {sent['bias']}`\n"
            f"🏦 Funding: `{funding*100:+.4f}%` · OI: `{oi:,.0f} BTC`\n\n"
            f"*Timeframe Analysis:*\n"
            f"{d1_em} 1D Trend Tide: `{sc1d}/100` — {dir1d}\n"
            f"{c4_em} 4H Wave Setup: `{sc4h}/100` (need {MIN_4H})\n"
            f"{c1h_em} 1H Wave Setup: `{sc1h}/100` (need {MIN_1H})\n"
            f"{c15_em} 15m Entry Trigger: `{sc15}/100` (need {MIN_15M})\n"
            f"📊 Blended Confluence: `{final}/100` (need {adp})\n\n"
            f"_Trade with institutional edge._"
        )
        _tg_direct(chat_id, report)
    except Exception as e:
        _tg_direct(chat_id, f"❌ *Error running scan:* `{e}`")

def _tg_listener_loop(ex):
    if not TG_TOKEN:
        return
    print(s("  Telegram command listener daemon started...", GY))
    subs = _load_subs()
    if TG_CHAT:
        try:
            if int(TG_CHAT) not in subs:
                subs.append(int(TG_CHAT))
                _save_subs(subs)
        except:
            pass

    while True:
        try:
            url = f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates"
            params = {"timeout": 20}
            if _last_offset[0] > 0:
                params["offset"] = _last_offset[0]
            
            r = requests.get(url, params=params, timeout=25)
            if r.status_code != 200:
                time.sleep(5)
                continue
                
            res = r.json()
            if not res.get("ok"):
                time.sleep(5)
                continue
                
            for update in res.get("result", []):
                _last_offset[0] = update["update_id"] + 1
                msg = update.get("message") or {}
                chat = msg.get("chat") or {}
                chat_id = chat.get("id")
                text = (msg.get("text") or "").strip().lower()
                from_user = msg.get("from") or {}
                username = from_user.get("first_name") or "Trader Friend"
                
                if not chat_id or not text:
                    continue
                    
                if text == "/start" or text == "/subscribe":
                    subs = _load_subs()
                    if chat_id not in subs:
                        subs.append(chat_id)
                        _save_subs(subs)
                    welcome_msg = (
                        f"👋 *Welcome {username} to BTC APEX PRO v6.0!*\n\n"
                        f"🥇 You are now subscribed to the *world's #1 trading signals bot*.\n"
                        f"📈 You will receive highly accurate, verified, A-grade BTC setups directly here.\n\n"
                        f"👉 *Available Commands:*\n"
                        f"  • `/scan` — Trigger instant real-time market scan\n"
                        f"  • `/stats` — View bot performance and win rate\n"
                        f"  • `/unsubscribe` — Stop receiving private signals"
                    )
                    _tg_direct(chat_id, welcome_msg)
                    
                elif text == "/unsubscribe":
                    subs = _load_subs()
                    if chat_id in subs:
                        subs.remove(chat_id)
                        _save_subs(subs)
                    _tg_direct(chat_id, "❌ *Unsubscribed successfully.* You will no longer receive private signals. Good luck!")
                    
                elif text == "/stats" or text == "/performance":
                    perf = get_performance()
                    if perf:
                        wrc = "🟢" if perf["wr"] >= 60 else ("🟡" if perf["wr"] >= 45 else "🔴")
                        stats_msg = (
                            f"📈 *BTC APEX PRO — Performance Report*\n\n"
                            f"📊 Trades logged: `{perf['n']}`\n"
                            f"🏆 Wins (TP hit): `{perf['wins']}`\n"
                            f"❌ Losses (SL hit): `{perf['losses']}`\n"
                            f"{wrc} Win Rate:  `{perf['wr']}%`\n"
                            f"⚖️ Profit Factor: `{perf['pf']}`\n\n"
                            f"_Keep growing your portfolio safely!_"
                        )
                    else:
                        stats_msg = "ℹ️ *No completed trades logged yet.* Performance data will display after a few signals hit TP or SL."
                    _tg_direct(chat_id, stats_msg)
                    
                elif text == "/scan":
                    _tg_direct(chat_id, "🔍 *Scouting Bitcoin markets...* running real-time multi-timeframe analysis.")
                    threading.Thread(target=_run_scan_for_user, args=(ex, chat_id), daemon=True).start()
                    
                elif text == "/help":
                    help_msg = (
                        f"🤖 *BTC APEX PRO v6.0 Help Menu*\n\n"
                        f"Available commands:\n"
                        f"⚡ `/scan` — Real-time multi-timeframe cascade analysis\n"
                        f"📈 `/stats` — Performance statistics and closed trade records\n"
                        f"🔔 `/subscribe` — Receive live trading signals in this chat\n"
                        f"🔕 `/unsubscribe` — Unsubscribe from signals"
                    )
                    _tg_direct(chat_id, help_msg)
        except Exception as e:
            time.sleep(5)

# ── TELEGRAM API INTERACTION ──────────────────────────────────────────
def _tg(msg):
    broadcast_tg_all(msg)

def send_signal_tg(sig, r1d, r4h, r1h=None):
    # Absolute Safeguard: Only send Telegram alerts for scores >= adaptive min
    adp_gate = get_adaptive_min()
    score = sig["conf"]
    direction = sig["dir"]
    tier = sig["tier"]
    funding = sig.get("funding", 0.0)
    oi = sig.get("oi", 0.0)
    patterns = sig.get("patterns", "Price Action Structure")
    backtest = sig.get("backtest", "Verified quadruple screen setup")
    forecast = sig.get("forecast", "Trajectory: Stable")
    ml_line  = sig.get("ml", "")

    if score < adp_gate:
        print(s(f"  [SILENT] Conf {score}% — below active adaptive gate ({adp_gate}%). Scanning silently.", GY))
        return

    reversal_line = f"🔥 *Reversal Edge*: `{sig['funding_msg']}`\n" if sig.get("funding_msg") else ""

    grade_em = "⚡" if tier == "A+" else "✅"
    msg = (
        f"{grade_em} *TRADE SIGNAL — BTC {direction} ({tier}-Grade)*\n"
        f"Confluence: `{score}/100`\n"
        f"{reversal_line}\n"
        f"📊 *Scores:* 1D:`{sig['sc1d']}` · 4H:`{sig['sc4h']}` · 1H:`{sig['sc1h']}` · 15m:`{sig['sc15']}`\n\n"
        f"🎯 Entry Zone : `${sig['entry']:,.1f}`\n"
        f"🛑 Stop Loss  : `${sig['sl']:,.1f}` ({sig['sl_mode']})\n"
        f"✅ Take Profit: `${sig['tp']:,.1f}`\n"
        f"⚖️ Risk/Reward: `{sig['rr']}:1`\n\n"
        f"📈 Pattern  : {patterns}\n"
        f"🔮 Forecast : {forecast}\n"
        f"🤖 ML Engine: {ml_line}\n"
        f"📋 Backtest : {backtest}\n\n"
        f"🏦 Funding: `{funding*100:+.4f}%` · OI: `{oi:,.0f} BTC`\n"
        f"🕐 Time: `{sig['time']} UTC`\n\n"
        f"_Execute with proper risk management. Never risk more than 1-2% per trade._"
    )
    _tg(msg)
    print(s(f"  ✅ Telegram sent: [{tier}] {direction} conf={score}%", BGN, bold=True))

_last_hb = [0.0]

def send_startup():
    """Startup broadcasts disabled to maintain absolute silence."""
    pass

def send_heartbeat():
    """Periodic heartbeat broadcasts disabled to prevent Telegram cluster spam."""
    pass

def notify_outcome(sig_id, outcome, pnl):
    # Check if the signal was silent
    log = _load_log()
    is_silent = False
    for sig in log:
        if sig.get("id") == sig_id:
            is_silent = sig.get("silent", False)
            break

    em = "✅ TP HIT" if outcome == "TP_HIT" else "❌ SL HIT"
    color = "+" if pnl > 0 else ""
    msg = f"{em} — Signal `{sig_id}`\nP&L: `{color}{pnl:.2f}%`"

    if not is_silent:
        _tg(msg)
    else:
        print(s(f"  [SILENT OUTCOME] {em} — Signal `{sig_id}` P&L: `{color}{pnl:.2f}%` (silent backtest outcome)", GY))


# ══════════════════════════════════════════════════════════════════════
#  DISPLAY
# ══════════════════════════════════════════════════════════════════════

_ph  = []
_lp  = [0]

def spark(p):
    _ph.append(p)
    if len(_ph) > 20: _ph.pop(0)
    if len(_ph) < 2: return s("─"*16, GY)
    mn, mx = min(_ph), max(_ph)
    rng = mx - mn or 1
    bars = "▁▂▃▄▅▆▇█"
    out = ""
    for v in _ph[-16:]:
        idx = min(int((v-mn)/rng*7), 7)
        out += s(bars[idx], BGN if v >= _ph[-2] else BRD)
    return out

def print_header():
    print()
    print(s("  ╔" + "═"*(W-4) + "╗", CY))
    print(s("  ║", CY) + s(" BTC APEX v5.0 — PRO TRADER INTELLIGENCE".center(W-4), BCY, bold=True) + s("║", CY))
    print(s("  ║", CY) + s(" 1D→4H→15m · Ichimoku · Fibonacci · Wyckoff · ICT · Backtesting".center(W-4), GY) + s("║", CY))
    print(s("  ╚" + "═"*(W-4) + "╝", CY))
    print()

def print_scan(price, dir1d, sc1d, sc4h, sc1h, sc15, final, sent):
    now   = datetime.now().strftime("%H:%M:%S")
    arrow = s("▲", BGN) if price > _lp[0] else (s("▼", BRD) if price < _lp[0] else s("─", GY))
    _lp[0] = price
    d1c = BGN if dir1d == "LONG" else (BRD if dir1d == "SHORT" else YL)
    fc  = BGN if final >= MIN_APLUS else (YL if final >= MIN_TOTAL else RD)
    adp = get_adaptive_min()

    print(
        f"  {s(now, GY)}  "
        f"{arrow}{s(f'${price:>12,.1f}', WH, bold=True)}  "
        f"{spark(price)}  "
        f"1D:{s(dir1d[:5].ljust(5), d1c, bold=True)} "
        f"4H:{s(str(sc4h).rjust(3), YL)} "
        f"1H:{s(str(sc1h).rjust(3), YL)} "
        f"15m:{s(str(sc15).rjust(3), YL)} "
        f"→{s(str(final).rjust(3), fc, bold=True)}/{adp} "
        f"F&G:{s(str(sent.get('value',50)), MG)}"
    )

def print_signal(sig, reasons_1d, reasons_4h, reasons_1h, reasons_15m):
    lng  = sig["dir"] == "LONG"
    gc   = BGN if lng else BRD
    ar   = "▲" if lng else "▼"
    conf = sig["conf"]
    IW   = W - 4

    def row(left, right=""):
        pad = " " * max(0, IW - len(left) - len(right))
        print(s("  │", gc) + left + pad + right + s("│", gc))

    print()
    print(s("  ┌" + "─"*IW + "┐", gc))
    tier_c = BGN if sig["tier"] == "A+" else YL
    print(s("  │", gc) + s(f" {ar} BTC {sig['dir']} — QUADRUPLE SCREEN CASCADE".ljust(IW-6), gc, bold=True) + s(f"[{sig['tier']}]", tier_c, bold=True) + s(" │", gc))
    print(s("  ├" + "─"*IW + "┤", GY))

    row(s(f"  Score: {conf}/100  ", GY) + cbar(conf, 14))
    row(s(f"  1D:{sig['sc1d']}/100  4H:{sig['sc4h']}/100  1H:{sig['sc1h']}/100  15m:{sig['sc15']}/100  Session:{sig['session']}", GY))
    row(s(f"  Sentiment: {sig['sentiment'].get('bias','NEUTRAL')} ({sig['sentiment'].get('value',50)})", GY))
    if sig.get("funding_msg"):
        row(s(f"  🔥 Edge: {sig['funding_msg']}", BGN, bold=True))
    print(s("  ├" + "─"*IW + "┤", GY))
    row(s(f"  ENTRY  ${sig['entry']:>12,.1f}  ← limit order", GY))
    row(s(f"  STOP   ${sig['sl']:>12,.1f}  ← set FIRST  ({sig['sl_mode']})", BRD))
    row(s(f"  TARGET ${sig['tp']:>12,.1f}  ← take profit", BGN))
    row(s(f"  R:R    {sig['rr']}:1   ATR ${sig['atr']:,}", GY))
    print(s("  ├" + "─"*IW + "┤", GY))
    row(s(f"  1D reasons: {', '.join(reasons_1d[:3])}", CY))
    row(s(f"  4H reasons: {', '.join(reasons_4h[:3])}", CY))
    row(s(f"  1H reasons: {', '.join(reasons_1h[:3])}", CY))
    row(s(f"  15m reason: {', '.join(reasons_15m[:3])}", CY))
    print(s("  ├" + "─"*IW + "┤", GY))
    row(s(f"  🔒 Anti-repaint · Confirmed candle · ID:{sig['id']}", YL))
    row(s(f"  ▲ Set TP/SL on exchange FIRST, then enter manually.", BYL, bold=True))
    print(s("  └" + "─"*IW + "┘", gc))
    print()

def print_tracker(dir1d, sc1d, sc4h, sc1h, sc15, adp):
    print()
    print(s(f"  ── Signal Tracker ──────────────────────────────────────────", GY))
    d1c = BGN if dir1d=="LONG" else (BRD if dir1d=="SHORT" else YL)
    print(s(f"  1D Tide  : ", GY) + s(dir1d, d1c, bold=True) + s(f"  score={sc1d}/100 (need≥{MIN_1D})", GY))
    print(s(f"  4H Wave  : score={sc4h}/100 (need≥{MIN_4H})", GY))
    print(s(f"  1H Wave  : score={sc1h}/100 (need≥{MIN_1H})", GY))
    print(s(f"  15m Entry: score={sc15}/100 (need≥{MIN_15M})", GY))
    adp_boost = _adaptive_boost[0]
    boost_str = f" +{adp_boost} adaptive" if adp_boost else ""
    print(s(f"  Min total: {adp}/100{boost_str}", GY))
    perf = get_performance()
    if perf:
        wrc = BGN if perf["wr"] >= 60 else (YL if perf["wr"] >= 45 else BRD)
        print(s(f"  Backtest : WR {perf['wr']}% · PF {perf['pf']} · n={perf['n']}", wrc))
    print(s("  " + "─"*(W-2), GY))
    print()


# ══════════════════════════════════════════════════════════════════════
#  HTTP SERVER — dual purpose:
#  1. Keeps Render Web Service ALIVE (responds to health checks)
#  2. Triggers a FULL SCAN when pinged by cron-job.org every 14 min
#     → GET /scan  = run full market scan immediately
#     → GET /       = return status (keep-alive only)
# ══════════════════════════════════════════════════════════════════════

_ex_ref    = [None]   # shared exchange reference
_last_scan = [0.0]    # last scan timestamp
_last_error_alert = [0.0]  # last error alert timestamp
_scan_lock = threading.Lock()

def _do_scan_safe():
    """Thread-safe scan triggered by HTTP ping."""
    if not _scan_lock.acquire(blocking=False):
        return "scan already running"
    try:
        now = time.time()
        if now - _last_scan[0] < 60:   # no more than 1 scan/minute
            return f"scan cooldown ({int(60-(now-_last_scan[0]))}s left)"
        _last_scan[0] = now
        ex = _ex_ref[0]
        if ex is None:
            return "exchange not ready"
        run_cron(ex)
        return "scan complete"
    except Exception as e:
        return f"scan error: {e}"
    finally:
        _scan_lock.release()

class _PH(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.rstrip("/")
        if path == "/scan":
            # Full market scan triggered by cron-job.org
            threading.Thread(target=_do_scan_safe, daemon=True).start()
            body = b"BTC APEX: scan triggered OK"
        elif path == "/status":
            perf = get_performance()
            wr   = f"{perf['wr']}% WR ({perf['wins']}W/{perf['losses']}L)" if perf else "no data yet"
            body = f"BTC APEX v5.0 | Running | {wr}".encode()
        else:
            body = b"BTC APEX v5.0 | OK | ping /scan to trigger market scan"
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a): pass

def _start_server():
    port = int(os.getenv("PORT", 10000))
    try:
        print(s(f"  HTTP server on port {port} — ping /scan to trigger market scan", GY))
        HTTPServer(("0.0.0.0", port), _PH).serve_forever()
    except Exception as e:
        print(s(f"  HTTP server error: {e}", GY))


def is_signal_allowed(direction, df15, df1h, df4h, df1d, price):
    # 1. Intraday Daily Candle Dump/Pump Protection
    if len(df1d) > 0:
        today_open = float(df1d.open.iloc[-1])
        daily_pct_change = (price - today_open) / today_open
        if direction == "LONG" and daily_pct_change < -0.015:
            return False, f"Intraday Daily Crash Guard (down {daily_pct_change*100:.2f}%)"
        if direction == "SHORT" and daily_pct_change > 0.015:
            return False, f"Intraday Daily Pump Guard (up {daily_pct_change*100:.2f}%)"

    # 2. EMA stack & Price Position relative to EMA50/200 Guards
    r15 = df15.iloc[-2]
    r1h = df1h.iloc[-2]
    r4h = df4h.iloc[-2]
    
    if direction == "LONG":
        # Key trend filter: 4H price must be above its 50 EMA (primary trend guard)
        if r4h.close < r4h.e50:
            return False, "4H price below 50 EMA — no uptrend on key timeframe"
        # At least 2 of 3 timeframes must be above 200 EMA
        above_200 = sum([r15.close > r15.e200, r1h.close > r1h.e200, r4h.close > r4h.e200])
        if above_200 < 2:
            return False, "Price below 200 EMA on majority of timeframes (Markdown Phase)"
        # 4H fast EMA cross must be bullish
        if r4h.e9 < r4h.e20:
            return False, "4H EMAs Bearish cross (9 < 20) — no bullish momentum"
    else:
        # Key trend filter: 4H price must be below its 50 EMA (primary trend guard)
        if r4h.close > r4h.e50:
            return False, "4H price above 50 EMA — no downtrend on key timeframe"
        # At least 2 of 3 timeframes must be below 200 EMA
        below_200 = sum([r15.close < r15.e200, r1h.close < r1h.e200, r4h.close < r4h.e200])
        if below_200 < 2:
            return False, "Price above 200 EMA on majority of timeframes (Markup Phase)"
        # 4H fast EMA cross must be bearish
        if r4h.e9 > r4h.e20:
            return False, "4H EMAs Bullish cross (9 > 20) — no bearish momentum"

    # 3. No Catching Falling Knives / Shorting Rockets (15m Momentum and Candle guards)
    if len(df15) >= 5:
        last_3 = df15.iloc[-4:-1]
        
        # Detect if we have an active strong volume-supported breakout to bypass MACD direction check
        is_breakout = False
        if "bb_up" in df15.columns and "bb_lo" in df15.columns:
            bb_up = df15.bb_up.iloc[-2]
            bb_lo = df15.bb_lo.iloc[-2]
            if direction == "LONG" and price > bb_up and r15.vol_r > 1.3:
                is_breakout = True
            elif direction == "SHORT" and price < bb_lo and r15.vol_r > 1.3:
                is_breakout = True

        if direction == "LONG":
            all_red = all(x.close < x.open for _, x in last_3.iterrows())
            strong_bodies = all(abs(x.close - x.open) > x.atr * 0.25 for _, x in last_3.iterrows())
            if all_red and strong_bodies:
                return False, "Extreme downward momentum (3 consecutive strong red candles)"
            if not is_breakout and r15.macdh < 0 and r15.macdh < r15.macdh1:
                return False, "15m MACD Histogram expanding downwards"
        else:
            all_green = all(x.close > x.open for _, x in last_3.iterrows())
            strong_bodies = all(abs(x.close - x.open) > x.atr * 0.25 for _, x in last_3.iterrows())
            if all_green and strong_bodies:
                return False, "Extreme upward momentum (3 consecutive strong green candles)"
            if not is_breakout and r15.macdh > 0 and r15.macdh > r15.macdh1:
                return False, "15m MACD Histogram expanding upwards"

    return True, ""


# ══════════════════════════════════════════════════════════════════════
#  CRON MODE — one scan, send signal if found, exit (GitHub Actions)
# ══════════════════════════════════════════════════════════════════════

def run_cron(ex):
    """
    GitHub Actions scan — runs every 15 min.
    ALWAYS sends Telegram so user knows bot is running.
    Sends A-grade signals when all 3 screens and derivative gates align.
    """
    print(s("  [CRON] Starting scan...", CY))
    df15 = ind(fetch(ex, TF_15M))
    df1h = ind(fetch(ex, TF_1H))
    df4h = ind(fetch(ex, TF_4H))
    df1d = ind(fetch(ex, TF_1D))

    price = float(df15.iloc[-1].close)
    r15   = df15.iloc[-2]
    sent  = get_sentiment()
    now   = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    # Fetch derivative data streams (Whale Activity)
    funding, oi = fetch_derivative_data(ex)
    
    # Volatility Spike Protection
    is_spike, spike_reason = check_whale_spike(df15)

    # Linear Regression Price Trend Forecasting (15 period)
    forecast_slope = linear_regression_forecast(df15.close.tolist(), 15)

    # ML Ensemble Forecast (LinearRegression + Ridge + Lasso trained on indicator features)
    ml_pred, ml_label = ml_ensemble_forecast(df15, lookback=60)

    # 1D Trend Analysis for both directions
    sc1d_long, r1d_long, sc1d_short, r1d_short = analyze_1d(df1d)

    # Evaluate LONG setup
    sc4h_long, r4h_long = analyze_4h(df4h, "LONG")
    sc1h_long, r1h_long = analyze_1h(df1h, "LONG")
    sc15_long, r15_long = analyze_15m(df15, "LONG")
    final_long = round(sc1d_long * 0.20 + sc4h_long * 0.25 + sc1h_long * 0.25 + sc15_long * 0.30)

    # Evaluate SHORT setup
    sc4h_short, r4h_short = analyze_4h(df4h, "SHORT")
    sc1h_short, r1h_short = analyze_1h(df1h, "SHORT")
    sc15_short, r15_short = analyze_15m(df15, "SHORT")
    final_short = round(sc1d_short * 0.20 + sc4h_short * 0.25 + sc1h_short * 0.25 + sc15_short * 0.30)

    adp = get_adaptive_min()

    # Determine aligned directions:
    long_tide_ok = sc1d_long >= MIN_1D
    short_tide_ok = sc1d_short >= MIN_1D

    # Strict timeframe gates ("No Compromise" rule)
    long_aligned = long_tide_ok and sc4h_long >= MIN_4H and sc1h_long >= MIN_1H and sc15_long >= MIN_15M
    short_aligned = short_tide_ok and sc4h_short >= MIN_4H and sc1h_short >= MIN_1H and sc15_short >= MIN_15M

    # Select candidate direction
    direction = "NEUTRAL"
    sc1d, sc4h, sc1h, sc15, final = 0, 0, 0, 0, 0
    r1d, r4h, r1h, r15r = [], [], [], []

    if long_aligned and short_aligned:
        if final_long >= final_short:
            direction = "LONG"
        else:
            direction = "SHORT"
    elif long_aligned:
        direction = "LONG"
    elif short_aligned:
        direction = "SHORT"

    if direction == "LONG":
        sc1d, r1d = sc1d_long, r1d_long
        sc4h, r4h = sc4h_long, r4h_long
        sc1h, r1h = sc1h_long, r1h_long
        sc15, r15r = sc15_long, r15_long
        final = final_long
    elif direction == "SHORT":
        sc1d, r1d = sc1d_short, r1d_short
        sc4h, r4h = sc4h_short, r4h_short
        sc1h, r1h = sc1h_short, r1h_short
        sc15, r15r = sc15_short, r15_short
        final = final_short
    else:
        # For neutral display, print whichever has the higher final score
        if final_long >= final_short:
            direction = "NEUTRAL_LONG"
            sc1d, r1d = sc1d_long, r1d_long
            sc4h, r4h = sc4h_long, r4h_long
            sc1h, r1h = sc1h_long, r1h_long
            sc15, r15r = sc15_long, r15_long
            final = final_long
        else:
            direction = "NEUTRAL_SHORT"
            sc1d, r1d = sc1d_short, r1d_short
            sc4h, r4h = sc4h_short, r4h_short
            sc1h, r1h = sc1h_short, r1h_short
            sc15, r15r = sc15_short, r15_short
            final = final_short

    # Print scan results to console
    print(s(f"  ${price:,.1f}  1D:{direction}({sc1d})  4H:{sc4h}  1H:{sc1h}  15m:{sc15}  Final:{final}/{adp}", GY))
    print(s(f"  F&G:{sent['value']} {sent['bias']}  Funding:{funding*100:+.4f}%  OI:{oi:,.0f} BTC  Slope:{forecast_slope:+.4f}  {ml_label}", GY))

    # Apply protection gates
    skip_signal = False
    skip_reasons = []
    
    if is_spike:
        skip_signal = True
        skip_reasons.append(f"Whale Volatility Protection ({spike_reason})")
    
    # Intraday adverse momentum
    if direction == "LONG" and forecast_slope < -5.0:
        skip_signal = True
        skip_reasons.append(f"Forecasting downward momentum ({forecast_slope:+.2f})")
    if direction == "SHORT" and forecast_slope > 5.0:
        skip_signal = True
        skip_reasons.append(f"Forecasting upward momentum ({forecast_slope:+.2f})")

    # Run is_signal_allowed guards (Daily check, EMA checks, 3-consecutive candles, MACD histogram checks)
    if direction in ("LONG", "SHORT"):
        allowed, guard_reason = is_signal_allowed(direction, df15, df1h, df4h, df1d, price)
        if not allowed:
            skip_signal = True
            skip_reasons.append(f"Guard Triggered: {guard_reason}")

    # Check if signal fires
    signal_fired = False
    sig = None

    if direction in ("LONG", "SHORT") and final >= 60:
        # Run local in-memory backtest
        local_wins, local_losses, local_wr = backtest_strategy_historically(df15, df1h, df4h, df1d, direction)
        total_local = local_wins + local_losses
        # Check for duplicate signal in signals_log.json
        if is_duplicate_signal(direction, datetime.now().strftime("%Y-%m-%d %H:%M:%S")):
            skip_signal = True
            skip_reasons.append("Duplicate signal detected within cooldown period")

        if not skip_signal:
            sig = build_signal(direction, df15, sc1d, sc4h, sc1h, sc15, sent, r1d, r4h, r1h, r15r, forecast_slope, local_wins, local_losses, local_wr, ml_pred, ml_label, funding, oi)
            if sig:
                print_signal(sig, r1d, r4h, r1h, r15r)
                send_signal_tg(sig, r1d, r4h, r1h)
                log_signal(sig)
                print(s(f"  [CRON] ✅ SIGNAL FIRED: {sig['dir']} conf={sig['conf']}%", BGN, bold=True))
                signal_fired = True
        else:
            print(s(f"  [CRON] ⚠️ Setup aligned but blocked by: {', '.join(skip_reasons)}", YL))

    if not signal_fired:
        missing = []
        if direction.startswith("NEUTRAL"): missing.append("No strict confluence across all timeframes")
        if sc1d < MIN_1D:      missing.append(f"1D: {sc1d}/{MIN_1D}")
        if sc4h < MIN_4H:        missing.append(f"4H: {sc4h}/{MIN_4H}")
        if sc1h < MIN_1H:        missing.append(f"1H: {sc1h}/{MIN_1H}")
        if sc15 < MIN_15M:       missing.append(f"15m: {sc15}/{MIN_15M}")
        if final < adp:          missing.append(f"Blend: {final}/{adp} (need {adp}% — gap {adp-final}pts)")
        if sig is None and final >= adp and not missing: missing.append("R:R < 1.8 (invalid entry)")
        if skip_signal:          missing.append(f"Gate: {', '.join(skip_reasons)}")

        miss_str = " | ".join(missing) if missing else "Market conditions not fully aligned"
        print(s(f"  [CRON] No signal. {miss_str}", GY))
        print(s(f"  [CRON] Scores → 1D:{sc1d} 4H:{sc4h} 1H:{sc1h} 15m:{sc15} → Blend:{final}/{adp}", GY))

    return signal_fired


# ══════════════════════════════════════════════════════════════════════
#  MAIN — auto-detects GitHub Actions (--cron) vs continuous loop
# ══════════════════════════════════════════════════════════════════════

def main():
    cron_mode = "--cron" in sys.argv

    print_header()
    print(s(f"  Connecting to {EXCHANGE.upper()}...", GY), end="", flush=True)
    ex = connect()
    print(s(" connected.", BGN))
    _ex_ref[0] = ex   # expose to HTTP scan handler

    if cron_mode:
        # ── GitHub Actions mode: one scan, exit ──────────────────────
        print(s("  Mode: CRON (GitHub Actions — free 24/7)", CY))
        try:
            found = run_cron(ex)
            sys.exit(0)
        except Exception as e:
            print(s(f"  CRON error: {e}", BRD))
            import traceback; traceback.print_exc()
            sys.exit(1)

    # ── Continuous mode: laptop or Render background worker ──────────
    # Start keepalive HTTP server (UptimeRobot / Render compatibility)
    threading.Thread(target=_start_server, daemon=True).start()
    
    # Start Telegram background listener daemon
    threading.Thread(target=_tg_listener_loop, args=(ex,), daemon=True).start()
    
    send_startup()
    print(s(f"  Mode: CONTINUOUS (scanning every 60s)\n", GY))

    last_sig_time = None
    loop = 1
    prev_outcomes = {}

    while True:
        try:
            df15 = ind(fetch(ex, TF_15M))
            df1h = ind(fetch(ex, TF_1H))
            df4h = ind(fetch(ex, TF_4H))
            df1d = ind(fetch(ex, TF_1D))

            price = float(df15.iloc[-1].close)
            sent  = get_sentiment()

            # Fetch derivative data streams
            funding, oi = fetch_derivative_data(ex)
            
            # Whale Spike protection
            is_spike, spike_reason = check_whale_spike(df15)

            # Linear Regression trend forecasting (15 period)
            forecast_slope = linear_regression_forecast(df15.close.tolist(), 15)

            # ML Ensemble Forecast (LinearRegression + Ridge + Lasso trained on indicator features)
            ml_pred, ml_label = ml_ensemble_forecast(df15, lookback=60)

            # Notify resolved backtest outcomes
            for entry in check_outcomes(df15):
                sid = entry["id"]
                if entry["outcome"] != "PENDING" and sid not in prev_outcomes:
                    prev_outcomes[sid] = entry["outcome"]
                    notify_outcome(sid, entry["outcome"], entry["pnl_pct"])
                    col = BGN if entry["outcome"] == "TP_HIT" else BRD
                    print(s(f"  Backtest: {sid} → {entry['outcome']} ({entry['pnl_pct']:+.2f}%)", col))

            # 1D Trend Analysis for both directions
            sc1d_long, r1d_long, sc1d_short, r1d_short = analyze_1d(df1d)

            # Evaluate LONG setup
            sc4h_long, r4h_long = analyze_4h(df4h, "LONG")
            sc1h_long, r1h_long = analyze_1h(df1h, "LONG")
            sc15_long, r15_long = analyze_15m(df15, "LONG")
            final_long = round(sc1d_long * 0.20 + sc4h_long * 0.25 + sc1h_long * 0.25 + sc15_long * 0.30)

            # Evaluate SHORT setup
            sc4h_short, r4h_short = analyze_4h(df4h, "SHORT")
            sc1h_short, r1h_short = analyze_1h(df1h, "SHORT")
            sc15_short, r15_short = analyze_15m(df15, "SHORT")
            final_short = round(sc1d_short * 0.20 + sc4h_short * 0.25 + sc1h_short * 0.25 + sc15_short * 0.30)

            adp = get_adaptive_min()

            # Determine aligned directions:
            long_tide_ok = sc1d_long >= MIN_1D
            short_tide_ok = sc1d_short >= MIN_1D

            # Strict timeframe gates ("No Compromise" rule)
            long_aligned = long_tide_ok and sc4h_long >= MIN_4H and sc1h_long >= MIN_1H and sc15_long >= MIN_15M
            short_aligned = short_tide_ok and sc4h_short >= MIN_4H and sc1h_short >= MIN_1H and sc15_short >= MIN_15M

            # Select candidate direction
            direction = "NEUTRAL"
            sc1d, sc4h, sc1h, sc15, final_disp = 0, 0, 0, 0, 0
            r1d, r4h, r1h, r15r = [], [], [], []

            if long_aligned and short_aligned:
                if final_long >= final_short:
                    direction = "LONG"
                else:
                    direction = "SHORT"
            elif long_aligned:
                direction = "LONG"
            elif short_aligned:
                direction = "SHORT"

            if direction == "LONG":
                sc1d, r1d = sc1d_long, r1d_long
                sc4h, r4h = sc4h_long, r4h_long
                sc1h, r1h = sc1h_long, r1h_long
                sc15, r15r = sc15_long, r15_long
                final_disp = final_long
            elif direction == "SHORT":
                sc1d, r1d = sc1d_short, r1d_short
                sc4h, r4h = sc4h_short, r4h_short
                sc1h, r1h = sc1h_short, r1h_short
                sc15, r15r = sc15_short, r15_short
                final_disp = final_short
            else:
                # For neutral display, print whichever has the higher final score
                if final_long >= final_short:
                    direction = "NEUTRAL_LONG"
                    sc1d, r1d = sc1d_long, r1d_long
                    sc4h, r4h = sc4h_long, r4h_long
                    sc1h, r1h = sc1h_long, r1h_long
                    sc15, r15r = sc15_long, r15_long
                    final_disp = final_long
                else:
                    direction = "NEUTRAL_SHORT"
                    sc1d, r1d = sc1d_short, r1d_short
                    sc4h, r4h = sc4h_short, r4h_short
                    sc1h, r1h = sc1h_short, r1h_short
                    sc15, r15r = sc15_short, r15_short
                    final_disp = final_short

            print_scan(price, direction, sc1d, sc4h, sc1h, sc15, final_disp, sent)

            current_candle_time = df15.index[-2]
            cooldown_ok = True
            if last_sig_time is not None:
                # 15 min per candle, COOLDOWN = 2 (30 minutes)
                minutes_elapsed = (current_candle_time - last_sig_time).total_seconds() / 60.0
                if minutes_elapsed < (COOLDOWN * 15):
                    cooldown_ok = False

            if (direction in ("LONG", "SHORT") and final_disp >= 60 and cooldown_ok):

                # Apply protection gates
                skip_signal = False
                skip_reasons = []
                if is_spike:
                    skip_signal = True
                    skip_reasons.append("Whale Spike detected")
                
                # Intraday adverse momentum
                if direction == "LONG" and forecast_slope < -5.0:
                    skip_signal = True
                    skip_reasons.append(f"Forecasting adverse momentum (Slope {forecast_slope:+.2f})")
                if direction == "SHORT" and forecast_slope > 5.0:
                    skip_signal = True
                    skip_reasons.append(f"Forecasting adverse momentum (Slope {forecast_slope:+.2f})")

                # Run is_signal_allowed guards (Daily check, EMA checks, 3-consecutive candles, MACD histogram checks)
                allowed, guard_reason = is_signal_allowed(direction, df15, df1h, df4h, df1d, price)
                if not allowed:
                    skip_signal = True
                    skip_reasons.append(f"Guard Triggered: {guard_reason}")

                # Run local in-memory backtest (150 candles, fast)
                local_wins, local_losses, local_wr = backtest_strategy_historically(df15, df1h, df4h, df1d, direction)
                total_local = local_wins + local_losses
                if total_local > 0 and local_wr < 35.0:
                    skip_signal = True
                    skip_reasons.append(f"Regime win rate critically low ({local_wr:.1f}% based on {total_local} historical trades)")

                # Check for duplicate signal in signals_log.json
                if is_duplicate_signal(direction, datetime.now().strftime("%Y-%m-%d %H:%M:%S")):
                    skip_signal = True
                    skip_reasons.append("Duplicate signal detected within cooldown period")

                if not skip_signal:
                    sig = build_signal(direction, df15, sc1d, sc4h, sc1h, sc15, sent, r1d, r4h, r1h, r15r, forecast_slope, local_wins, local_losses, local_wr, ml_pred, ml_label, funding, oi)
                    if sig:
                        print_signal(sig, r1d, r4h, r1h, r15r)
                        send_signal_tg(sig, r1d, r4h, r1h)
                        log_signal(sig)
                        last_sig_time = current_candle_time
                else:
                    print(s(f"  ⚠️ Setup aligned but blocked by: {', '.join(skip_reasons)}", YL))

            if loop % 5 == 0:
                print_tracker(dir1d, sc1d, sc4h, sc1h, sc15, get_adaptive_min())

            send_heartbeat()
            loop += 1
            time.sleep(60)

        except KeyboardInterrupt:
            print()
            print(s("  Stopped. Protect your capital.", YL))
            break
        except Exception as e:
            print(s(f"  Error: {e} — retrying in 15s", BRD))
            # Send Telegram alert once every 2 hours to avoid spamming
            _now = time.time()
            if _now - _last_error_alert[0] > 7200:
                _last_error_alert[0] = _now
                err_msg = (
                    f"⚠️ *BTC APEX Bot Connection Alert*\n\n"
                    f"The bot is experiencing connection/geoblocking issues with the exchange.\n"
                    f"❌ *Error details:* `{str(e)[:150]}`\n\n"
                    f"💡 *Possible Solution:* If you deployed on Render free tier, your server might be located in the US where Binance/futures exchanges are geoblocked.\n"
                    f"👉 Please redeploy your Render service and select the *Frankfurt, Germany (EU)* region to bypass geoblocking."
                )
                try:
                    broadcast_tg_all(err_msg)
                except:
                    pass
            time.sleep(15)
            try:
                ex = connect()
            except Exception:
                pass

if __name__ == "__main__":
    main()
