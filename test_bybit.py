"""Quick sanity check — verifies Bybit data fetch works."""
import ccxt, sys

print("Testing Bybit ETH/USDT:USDT connection...")
try:
    ex = ccxt.bybit({"enableRateLimit": True, "options": {"defaultType": "linear"}})
    ohlcv = ex.fetch_ohlcv("ETH/USDT:USDT", "15m", limit=5)
    if ohlcv and len(ohlcv) >= 5:
        price = ohlcv[-1][4]
        print(f"  SUCCESS — ETH/USDT price: ${price:,.2f}")
        print(f"  Got {len(ohlcv)} candles OK")
    else:
        print("  FAIL — no data returned")
        sys.exit(1)
except Exception as e:
    print(f"  FAIL — {e}")
    sys.exit(1)

print("\nTesting Bybit sentiment fetch (Fear & Greed)...")
try:
    import requests
    r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
    d = r.json()["data"][0]
    print(f"  F&G: {d['value']} ({d['value_classification']})")
except Exception as e:
    print(f"  Warning: F&G fetch failed: {e} (non-critical)")

print("\nAll checks passed. Bybit is ready for GitHub Actions.")
