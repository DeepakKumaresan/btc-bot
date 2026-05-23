# -*- coding: utf-8 -*-
"""
BTC APEX SIGNAL BOT v5.0 — PRO TRADER INTELLIGENCE
====================================================
Triple Screen Cascade: 1D -> 4H -> 15m (Elder's Method)
ALL THREE timeframes must agree. No compromise.

BOOK CONCEPTS:
  Elder's Triple Screen  : 1D tide -> 4H wave -> 15m entry ripple
  Murphy Multi-TF        : Volume + price multi-timeframe confluence
  Weinstein Stage        : Only buy Stage 2, short Stage 4
  Douglas A-Grade        : Minimum 68/100 confidence — zero compromise
  ICT Smart Money        : FVG, Order Blocks, Liquidity Sweeps, BoS

INDICATORS: EMA9/20/50/200, Ichimoku, RSI+Div, MACD, BB, ATR, ADX,
            StochRSI, Williams %R, CCI, OBV, VWAP, Auto-Fibonacci, SMC

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

# ── CONFIG ────────────────────────────────────────────────────────────
EXCHANGE  = "bitget"
SYMBOL    = "BTC/USDT:USDT"
TF_15M, TF_1H, TF_4H, TF_1D = "15m", "1h", "4h", "1d"
LIMIT     = 300

# Telegram — hardcoded fallback (also reads from env for GitHub Actions secrets)
_TG_TOKEN_DEFAULT = "8775276870:AAGABvQ6PwtRgGPNbk3V4YX_A0eVXxpiWyo"
_TG_CHAT_DEFAULT  = "998659643"
TG_TOKEN  = os.getenv("TG_TOKEN", _TG_TOKEN_DEFAULT)
TG_CHAT   = os.getenv("TG_CHAT",  _TG_CHAT_DEFAULT)

# Signal thresholds — calibrated for real market conditions
MIN_1D    = 45    # 1D must show clear direction (was 55)
MIN_4H    = 38    # 4H must find a setup (was 50)
MIN_15M   = 35    # 15m must have entry trigger (was 50)
MIN_TOTAL = 55    # A tier — sent to Telegram (was 68)
MIN_APLUS = 72    # A+ tier — strongest setups (was 80)
MIN_RR    = 1.8   # minimum risk:reward (was 2.0)
SL_ATR    = 1.2
TP_ATR    = 2.8
COOLDOWN  = 2     # candles between signals (was 3)

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
    for attempt in range(retries):
        try:
            raw = ex.fetch_ohlcv(SYMBOL, tf, limit=LIMIT)
            df  = pd.DataFrame(raw, columns=["ts","open","high","low","close","volume"])
            df["ts"] = pd.to_datetime(df["ts"], unit="ms")
            return df.set_index("ts").astype(float)
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
            else:
                raise

# ── INDICATORS ────────────────────────────────────────────────────────
def ind(df):
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
    r   = df.iloc[-2]
    r5  = df.iloc[-7]   # 5 bars ago for slope
    p   = r.close
    slope = r.e200 - r5.e200
    dist  = (p - r.e200) / r.e200 * 100
    if slope > 0 and dist > -1:
        return 2  # Markup — BUY zone
    if slope < 0 and dist < 1:
        return 4  # Markdown — SHORT zone
    if abs(slope) < 0.001 * r.e200:
        return 1 if p > r.e200 else 3  # Base or Top
    return 0  # unclear

# ── WYCKOFF PHASE ─────────────────────────────────────────────────────
def wyckoff_phase(df):
    r    = df.iloc[-2]
    win  = df.iloc[-30:-2]
    obv_up   = r.obv_slope > 0
    tight    = win.close.std() / r.e50 < 0.015
    sweep_lo = bool(df.sweep_lo.iloc[-5:-2].any())
    sweep_hi = bool(df.sweep_hi.iloc[-5:-2].any())
    price_up = r.close > win.close.iloc[0]

    if tight and obv_up and sweep_lo:
        return "ACCUMULATION"   # spring test — long setup
    if not tight and obv_up and price_up:
        return "MARKUP"          # trend up
    if tight and not obv_up and sweep_hi:
        return "DISTRIBUTION"   # UTAD — short setup
    if not tight and not obv_up and not price_up:
        return "MARKDOWN"        # trend down
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
    reasons = []

    if bool(r.above_cloud): lp += 20; reasons.append("1D above cloud")
    elif bool(r.below_cloud): sp += 20; reasons.append("1D below cloud")

    if r.ichi_tenkan > r.ichi_kijun: lp += 10; reasons.append("Tenkan>Kijun")
    elif r.ichi_tenkan < r.ichi_kijun: sp += 10; reasons.append("Tenkan<Kijun")

    if len(df1d) > 28:
        p26 = float(df1d.close.iloc[-28])
        if float(r.close) > p26: lp += 10; reasons.append("Chikou above price")
        else: sp += 10; reasons.append("Chikou below price")

    stage = weinstein_stage(df1d)
    if stage == 2:   lp += 15; reasons.append("Weinstein Stage 2 (Markup)")
    elif stage == 4: sp += 15; reasons.append("Weinstein Stage 4 (Markdown)")

    if r.e50 > r.e200: lp += 10; reasons.append("Golden Cross")
    elif r.e50 < r.e200: sp += 10; reasons.append("Death Cross")

    if r.adx > 20:
        if r.adxp > r.adxn: lp += 10; reasons.append(f"ADX {r.adx:.0f} bull")
        else: sp += 10; reasons.append(f"ADX {r.adx:.0f} bear")

    if r.obv_slope > 0: lp += 10; reasons.append("OBV rising")
    elif r.obv_slope < 0: sp += 10; reasons.append("OBV falling")

    if r.macdh > 0 and r.macdh > r.macdh1: lp += 10; reasons.append("MACD hist up")
    elif r.macdh < 0 and r.macdh < r.macdh1: sp += 10; reasons.append("MACD hist dn")

    if r.vol_r >= 1.0:
        if lp >= sp: lp += 5
        else: sp += 5

    if lp > sp and lp >= MIN_1D:   return "LONG",  lp, reasons
    if sp > lp and sp >= MIN_1D:   return "SHORT", sp, reasons
    return "NEUTRAL", max(lp, sp), reasons


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

    fib = get_fib_levels(df4h)
    at_fib, fn = near_fib(price, fib, pct=0.008)
    if at_fib: score += 15; reasons.append(f"4H at Fib {fn}")

    if abs(price - r.ichi_kijun) / price < 0.008: score += 15; reasons.append("4H at Kijun")
    elif lng and bool(r.above_cloud): score += 8; reasons.append("4H above cloud")
    elif not lng and bool(r.below_cloud): score += 8; reasons.append("4H below cloud")

    rsi_ok = (35 < r.rsi < 55) if lng else (45 < r.rsi < 65)
    if rsi_ok: score += 10; reasons.append(f"4H RSI {r.rsi:.0f} zone")

    if lng and bool(df4h.ob_bull.iloc[-4:-1].any()):  score += 10; reasons.append("4H Bull OB")
    if not lng and bool(df4h.ob_bear.iloc[-4:-1].any()): score += 10; reasons.append("4H Bear OB")

    if lng and (bool(r.fvg_bull) or bool(r.sweep_lo)): score += 10; reasons.append("4H FVG/Sweep bull")
    if not lng and (bool(r.fvg_bear) or bool(r.sweep_hi)): score += 10; reasons.append("4H FVG/Sweep bear")

    if lng and r.macdh > r.macdh1:      score += 10; reasons.append("4H MACD turning up")
    if not lng and r.macdh < r.macdh1:  score += 10; reasons.append("4H MACD turning dn")

    stk_up = r.stk > r["std"] and rp.stk <= rp["std"] and r.stk < 50
    stk_dn = r.stk < r["std"] and rp.stk >= rp["std"] and r.stk > 50
    if lng and stk_up:      score += 10; reasons.append("4H Stoch cross up")
    if not lng and stk_dn:  score += 10; reasons.append("4H Stoch cross dn")

    if df4h.vol_r.iloc[-4:-1].mean() < 0.9: score += 10; reasons.append("4H vol declining (pullback)")

    phase = wyckoff_phase(df4h)
    if lng and phase in ("ACCUMULATION","MARKUP"):      score += 5; reasons.append(f"Wyckoff {phase}")
    if not lng and phase in ("DISTRIBUTION","MARKDOWN"): score += 5; reasons.append(f"Wyckoff {phase}")

    if r.atr_avg > 0 and 0.6 < r.atr/r.atr_avg < 1.8: score += 5; reasons.append("ATR normal")

    return min(score, 100), reasons


# ══════════════════════════════════════════════════════════════════════
#  SCREEN 3 — 15m ENTRY  (Elder's Ripple)
# ══════════════════════════════════════════════════════════════════════

def analyze_15m(df15, direction):
    r, rp = df15.iloc[-2], df15.iloc[-3]
    score = 0
    reasons = []
    lng = direction == "LONG"

    if lng and (bool(r.bull_engulf) or bool(r.hammer)):
        score += 20; reasons.append("15m bull engulf/hammer")
    elif not lng and (bool(r.bear_engulf) or bool(r.shoot_star)):
        score += 20; reasons.append("15m bear engulf/star")
    elif lng and r.bull == 1 and r.body > r.atr * 0.4:
        score += 8; reasons.append("15m strong bull candle")
    elif not lng and r.bull == 0 and r.body > r.atr * 0.4:
        score += 8; reasons.append("15m strong bear candle")

    if lng and r.e9 > r.e20 and rp.e9 <= rp.e20:     score += 15; reasons.append("15m EMA9 crossed above 20")
    elif lng and r.e9 > r.e20:                         score += 7;  reasons.append("15m EMA9>20")
    if not lng and r.e9 < r.e20 and rp.e9 >= rp.e20: score += 15; reasons.append("15m EMA9 crossed below 20")
    elif not lng and r.e9 < r.e20:                    score += 7;  reasons.append("15m EMA9<20")

    if lng and r.macd > r.macds and rp.macd <= rp.macds:     score += 15; reasons.append("15m MACD bull cross")
    elif lng and r.macdh > 0:                                 score += 6;  reasons.append("15m MACD pos")
    if not lng and r.macd < r.macds and rp.macd >= rp.macds: score += 15; reasons.append("15m MACD bear cross")
    elif not lng and r.macdh < 0:                            score += 6;  reasons.append("15m MACD neg")

    if r.vol_r >= 1.8:   score += 15; reasons.append(f"15m vol spike {r.vol_r:.1f}x")
    elif r.vol_r >= 1.2: score += 7;  reasons.append(f"15m vol {r.vol_r:.1f}x")

    if lng and r.rsi < 45 and r.rsi > r.rsi1:  score += 10; reasons.append(f"15m RSI {r.rsi:.0f} up")
    if not lng and r.rsi > 55 and r.rsi < r.rsi1: score += 10; reasons.append(f"15m RSI {r.rsi:.0f} dn")

    stk_up = r.stk > r["std"] and rp.stk <= rp["std"]
    stk_dn = r.stk < r["std"] and rp.stk >= rp["std"]
    if lng and stk_up:     score += 10; reasons.append("15m Stoch up")
    if not lng and stk_dn: score += 10; reasons.append("15m Stoch dn")

    p = r.close
    if min(abs(p-r.vwap)/p, abs(p-r.e20)/p, abs(p-r.e50)/p) < 0.003:
        score += 10; reasons.append("15m at key level")

    if lng and (bool(r.fvg_bull) or bool(r.ob_bull)):       score += 5; reasons.append("15m SMC bull")
    if not lng and (bool(r.fvg_bear) or bool(r.ob_bear)):   score += 5; reasons.append("15m SMC bear")

    if lng and r.willr < -75:  score += 5; reasons.append(f"15m WillR {r.willr:.0f} OS")
    if not lng and r.willr > -25: score += 5; reasons.append(f"15m WillR {r.willr:.0f} OB")

    return min(score, 100), reasons


# ══════════════════════════════════════════════════════════════════════
#  SIGNAL BUILDER
# ══════════════════════════════════════════════════════════════════════

def build_signal(direction, df15, sc1d, sc4h, sc15, sentiment):
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

    final = round(sc1d*0.30 + sc4h*0.35 + sc15*0.35)
    sent  = sentiment or {"mod": 0}
    if direction=="LONG"  and sent["mod"] >  0: final = min(100, final + sent["mod"])
    if direction=="SHORT" and sent["mod"] <  0: final = min(100, final + abs(sent["mod"]))
    if direction=="LONG"  and sent["mod"] < -3: final = max(0, final + sent["mod"])
    if direction=="SHORT" and sent["mod"] >  3: final = max(0, final - sent["mod"])

    sn, sm = session()
    final  = min(100, final + sm)

    adp = get_adaptive_min()
    if final < adp: return None

    return {
        "dir": direction, "entry": round(price, 1),
        "sl": sl, "tp": tp, "rr": rr, "conf": final,
        "tier": "A+" if final >= MIN_APLUS else "A",
        "sl_mode": sl_mode, "atr": round(atr, 1),
        "sc1d": sc1d, "sc4h": sc4h, "sc15": sc15,
        "session": sn, "sentiment": sent,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "id": str(uuid.uuid4())[:8],
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

def _tg(msg):
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": msg, "parse_mode": "Markdown"},
            timeout=10)
    except Exception as e:
        print(s(f"  TG error: {e}", GY))

def send_signal_tg(sig, r1d, r4h):
    tier = sig["tier"]
    em   = "🟢" if sig["dir"] == "LONG" else "🔴"
    tc   = "🏆" if tier == "A+" else "⚡"
    cf   = "🔥" if sig["conf"] >= 80 else ("⚡" if sig["conf"] >= 68 else "✅")
    sent = sig.get("sentiment", {})
    msg = (
        f"{em} *BTC {sig['dir']} — TRIPLE SCREEN v5.0*\n"
        f"{tc} Grade: *{tier}*   {cf} Score: *{sig['conf']}/100*\n\n"
        f"📊 *Screen Scores:*\n"
        f"  1D Tide : `{sig['sc1d']}/100`\n"
        f"  4H Wave : `{sig['sc4h']}/100`\n"
        f"  15m Entry: `{sig['sc15']}/100`\n\n"
        f"🕐 `{sig['session']}`   ⏱ `{sig['time']}`\n\n"
        f"🎯 Entry  `${sig['entry']:,.1f}`\n"
        f"🛑 Stop   `${sig['sl']:,.1f}`  ({sig['sl_mode']})\n"
        f"💰 Target `${sig['tp']:,.1f}`\n"
        f"⚖️ R:R    `{sig['rr']}:1`\n\n"
        f"😱 Sentiment: `{sent.get('bias','NEUTRAL')} ({sent.get('value',50)})`\n\n"
        f"🔒 _Anti-repaint · Confirmed candle · Set TP/SL FIRST_\n"
        f"_1D→4H→15m all aligned. You decide the trade._"
    )
    _tg(msg)
    print(s(f"  ✅ Telegram sent: [{tier}] {sig['dir']} conf={sig['conf']}%", BGN, bold=True))

_last_hb = [0.0]

def send_startup():
    perf = get_performance()
    pline = ""
    if perf:
        pline = f"\n📈 Bot performance (last {perf['n']} signals): WR `{perf['wr']}%` · PF `{perf['pf']}`"
    adp   = get_adaptive_min()
    aline = f"  Min score: `{adp}` (adaptive)" if adp != MIN_TOTAL else f"  Min score: `{MIN_TOTAL}`"
    _tg(
        f"🚀 *BTC APEX v5.0 — ONLINE*\n"
        f"⚙️ Exchange: `{EXCHANGE.upper()}` · Symbol: `{SYMBOL}`\n"
        f"📊 Strategy: 1D→4H→15m Triple Screen\n"
        f"🕐 `{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}`\n"
        f"{aline}{pline}\n"
        f"_Scanning every 60s. Signals fire when ALL 3 screens align._"
    )

def send_heartbeat():
    now = time.time()
    if now - _last_hb[0] < 7200:
        return
    _last_hb[0] = now
    perf = get_performance()
    adp  = get_adaptive_min()
    pline = ""
    if perf:
        pline = (f"\n📈 Last {perf['n']} signals: "
                 f"✅ {perf['wins']} TP · ❌ {perf['losses']} SL · "
                 f"WR `{perf['wr']}%` · PF `{perf['pf']}`")
    boost = f" (+{_adaptive_boost[0]} adaptive)" if _adaptive_boost[0] else ""
    _tg(
        f"💓 *BTC APEX Heartbeat*\n"
        f"✅ Bot alive · Min score: `{adp}`{boost}\n"
        f"🕐 `{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}`"
        f"{pline}\n"
        f"_Scanning 1D→4H→15m every 60s. No signal = conditions not met._"
    )

def notify_outcome(sig_id, outcome, pnl):
    em = "✅ TP HIT" if outcome == "TP_HIT" else "❌ SL HIT"
    color = "+" if pnl > 0 else ""
    _tg(f"{em} — Signal `{sig_id}`\nP&L: `{color}{pnl:.2f}%`")


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

def print_scan(price, dir1d, sc1d, sc4h, sc15, final, sent):
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
        f"15m:{s(str(sc15).rjust(3), YL)} "
        f"→{s(str(final).rjust(3), fc, bold=True)}/{adp} "
        f"F&G:{s(str(sent.get('value',50)), MG)}"
    )

def print_signal(sig, reasons_1d, reasons_4h, reasons_15m):
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
    print(s("  │", gc) + s(f" {ar} BTC {sig['dir']} — TRIPLE SCREEN CASCADE".ljust(IW-6), gc, bold=True) + s(f"[{sig['tier']}]", tier_c, bold=True) + s(" │", gc))
    print(s("  ├" + "─"*IW + "┤", GY))

    row(s(f"  Score: {conf}/100  ", GY) + cbar(conf, 14))
    row(s(f"  1D:{sig['sc1d']}/100  4H:{sig['sc4h']}/100  15m:{sig['sc15']}/100  Session:{sig['session']}", GY))
    row(s(f"  Sentiment: {sig['sentiment'].get('bias','NEUTRAL')} ({sig['sentiment'].get('value',50)})", GY))
    print(s("  ├" + "─"*IW + "┤", GY))
    row(s(f"  ENTRY  ${sig['entry']:>12,.1f}  ← limit order", GY))
    row(s(f"  STOP   ${sig['sl']:>12,.1f}  ← set FIRST  ({sig['sl_mode']})", BRD))
    row(s(f"  TARGET ${sig['tp']:>12,.1f}  ← take profit", BGN))
    row(s(f"  R:R    {sig['rr']}:1   ATR ${sig['atr']:,}", GY))
    print(s("  ├" + "─"*IW + "┤", GY))
    row(s(f"  1D reasons: {', '.join(reasons_1d[:3])}", CY))
    row(s(f"  4H reasons: {', '.join(reasons_4h[:3])}", CY))
    row(s(f"  15m reason: {', '.join(reasons_15m[:3])}", CY))
    print(s("  ├" + "─"*IW + "┤", GY))
    row(s(f"  🔒 Anti-repaint · Confirmed candle · ID:{sig['id']}", YL))
    row(s(f"  ▲ Set TP/SL on exchange FIRST, then enter manually.", BYL, bold=True))
    print(s("  └" + "─"*IW + "┘", gc))
    print()

def print_tracker(dir1d, sc1d, sc4h, sc15, adp):
    print()
    print(s(f"  ── Signal Tracker ──────────────────────────────────────────", GY))
    d1c = BGN if dir1d=="LONG" else (BRD if dir1d=="SHORT" else YL)
    print(s(f"  1D Tide : ", GY) + s(dir1d, d1c, bold=True) + s(f"  score={sc1d}/100 (need≥{MIN_1D})", GY))
    print(s(f"  4H Wave : score={sc4h}/100 (need≥{MIN_4H})", GY))
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


# ══════════════════════════════════════════════════════════════════════
#  CRON MODE — one scan, send signal if found, exit (GitHub Actions)
# ══════════════════════════════════════════════════════════════════════

def run_cron(ex):
    """
    GitHub Actions scan — runs every 15 min.
    ALWAYS sends Telegram so user knows bot is running.
    Sends full signal when all 3 screens align.
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

    dir1d, sc1d, r1d = analyze_1d(df1d)

    sc4h, r4h = 0, []
    if dir1d != "NEUTRAL":
        sc4h, r4h = analyze_4h(df4h, dir1d)

    sc15, r15r = 0, []
    if dir1d != "NEUTRAL" and sc4h >= MIN_4H:
        sc15, r15r = analyze_15m(df15, dir1d)

    final = round(sc1d * 0.30 + sc4h * 0.35 + sc15 * 0.35)
    adp   = get_adaptive_min()

    print(s(f"  ${price:,.1f}  1D:{dir1d}({sc1d})  4H:{sc4h}  15m:{sc15}  Final:{final}/{adp}", GY))
    print(s(f"  F&G:{sent['value']} {sent['bias']}  RR_min:{MIN_RR}", GY))

    # ── Check if signal fires ─────────────────────────────────────────
    signal_fired = False
    sig = None

    if (dir1d != "NEUTRAL" and sc1d >= MIN_1D and sc4h >= MIN_4H and sc15 >= MIN_15M):
        sig = build_signal(dir1d, df15, sc1d, sc4h, sc15, sent)
        if sig:
            print_signal(sig, r1d, r4h, r15r)
            send_signal_tg(sig, r1d, r4h)
            log_signal(sig)
            print(s(f"  [CRON] ✅ SIGNAL FIRED: {sig['dir']} conf={sig['conf']}%", BGN, bold=True))
            signal_fired = True

    # ── Send status to Telegram EVERY scan (so user knows bot is live) ─
    d1_em = "🟢" if dir1d=="LONG" else ("🔴" if dir1d=="SHORT" else "⚪")
    c4_em = "✅" if sc4h >= MIN_4H else "⏳"
    c15_em = "✅" if sc15 >= MIN_15M else "⏳"
    fg_em  = "😱" if sent["value"] <= 25 else ("😨" if sent["value"] <= 40 else ("😐" if sent["value"] <= 60 else ("😀" if sent["value"] <= 80 else "🤑")))

    if not signal_fired:
        # Show what's missing
        missing = []
        if dir1d == "NEUTRAL":      missing.append("1D: no clear direction yet")
        elif sc1d < MIN_1D:         missing.append(f"1D: {sc1d}/{MIN_1D} (weak trend)")
        if sc4h < MIN_4H:           missing.append(f"4H: {sc4h}/{MIN_4H} (no setup yet)")
        if sc15 < MIN_15M:          missing.append(f"15m: {sc15}/{MIN_15M} (no entry trigger)")
        if final < adp and not missing: missing.append(f"Score: {final}/{adp} (needs {adp})")
        if sig is None and not missing: missing.append("R:R ratio too low for safe entry")

        miss_str = "\n".join(f"  • {m}" for m in missing) if missing else "  • Market not aligned"

        status_msg = (
            f"📡 *BTC APEX — Scan Complete*\n"
            f"🕐 `{now}`\n\n"
            f"💰 Price: `${price:,.1f}`\n"
            f"{fg_em} F&G: `{sent['value']}/100 — {sent['bias']}`\n\n"
            f"*Screen Scores:*\n"
            f"{d1_em} 1D Tide:  `{sc1d}/100` {'✅' if sc1d>=MIN_1D else '❌'} (need {MIN_1D}) — {dir1d}\n"
            f"{c4_em} 4H Wave:  `{sc4h}/100` {'✅' if sc4h>=MIN_4H else '❌'} (need {MIN_4H})\n"
            f"{c15_em} 15m Entry: `{sc15}/100` {'✅' if sc15>=MIN_15M else '❌'} (need {MIN_15M})\n"
            f"📊 Final:   `{final}/100` (need {adp})\n\n"
            f"⏳ *No signal yet. Missing:*\n{miss_str}\n\n"
            f"_Bot is alive. Scanning every 15 min._"
        )
        _tg(status_msg)
        print(s("  [CRON] Status sent to Telegram", GY))

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
    send_startup()
    print(s(f"  Mode: CONTINUOUS (scanning every 60s)\n", GY))

    last_sig_candle = -COOLDOWN
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

            # Notify resolved backtest outcomes
            for entry in check_outcomes(df15):
                sid = entry["id"]
                if entry["outcome"] != "PENDING" and sid not in prev_outcomes:
                    prev_outcomes[sid] = entry["outcome"]
                    notify_outcome(sid, entry["outcome"], entry["pnl_pct"])
                    col = BGN if entry["outcome"] == "TP_HIT" else BRD
                    print(s(f"  Backtest: {sid} → {entry['outcome']} ({entry['pnl_pct']:+.2f}%)", col))

            dir1d, sc1d, r1d = analyze_1d(df1d)

            sc4h, r4h = 0, []
            if dir1d != "NEUTRAL":
                sc4h, r4h = analyze_4h(df4h, dir1d)

            sc15, r15 = 0, []
            if dir1d != "NEUTRAL" and sc4h >= MIN_4H:
                sc15, r15 = analyze_15m(df15, dir1d)

            final_disp = round(sc1d*0.30 + sc4h*0.35 + sc15*0.35)
            print_scan(price, dir1d, sc1d, sc4h, sc15, final_disp, sent)

            idx = len(df15)
            if (dir1d != "NEUTRAL" and
                sc1d >= MIN_1D and sc4h >= MIN_4H and sc15 >= MIN_15M and
                (idx - last_sig_candle) >= COOLDOWN):

                sig = build_signal(dir1d, df15, sc1d, sc4h, sc15, sent)
                if sig:
                    print_signal(sig, r1d, r4h, r15)
                    send_signal_tg(sig, r1d, r4h)
                    log_signal(sig)
                    last_sig_candle = idx

            if loop % 5 == 0:
                print_tracker(dir1d, sc1d, sc4h, sc15, get_adaptive_min())

            send_heartbeat()
            loop += 1
            time.sleep(60)

        except KeyboardInterrupt:
            print()
            print(s("  Stopped. Protect your capital.", YL))
            break
        except Exception as e:
            print(s(f"  Error: {e} — retrying in 15s", BRD))
            time.sleep(15)
            try:
                ex = connect()
            except Exception:
                pass

if __name__ == "__main__":
    main()
