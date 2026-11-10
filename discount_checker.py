import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import requests
import os
import time

# ====================== CONFIG ======================
TICKERS_FILE = "tickers.txt"
NTFY_TOPIC = os.getenv("NTFY_TOPIC")
LOOKBACK_DAYS = 400
SWING_LENGTH = 50
DISCOUNT_PERCENT = 0.30          # lower 30% of the range
# ====================================================

def load_tickers():
    with open(TICKERS_FILE) as f:
        tickers = [line.strip().upper() for line in f if line.strip() and not line.startswith("#")]
    return list(dict.fromkeys(tickers))

def get_swing_high_low(df, length=50):
    high = df["High"]
    low = df["Low"]
    swing_high = high.rolling(window=length).max().iloc[-1]
    swing_low = low.rolling(window=length).min().iloc[-1]
    return float(swing_high), float(swing_low)

def analyze_ticker(ticker):
    try:
        df = yf.download(ticker, period=f"{LOOKBACK_DAYS}d", progress=False, auto_adjust=True, threads=False)
        if df is None or df.empty or len(df) < SWING_LENGTH + 10:
            return None

        df = df.dropna()
        close = float(df["Close"].iloc[-1])
        swing_high, swing_low = get_swing_high_low(df, SWING_LENGTH)

        if swing_high <= swing_low or np.isnan(swing_high) or np.isnan(swing_low):
            return None

        range_size = swing_high - swing_low
        discount_top = swing_low + (DISCOUNT_PERCENT * range_size)

        # How far is current price above the discount zone (in %)
        distance_pct = ((close - discount_top) / discount_top) * 100

        return {
            "ticker": ticker,
            "price": round(close, 2),
            "zone_low": round(swing_low, 2),
            "zone_top": round(discount_top, 2),
            "distance_pct": round(distance_pct, 1)
        }
    except Exception:
        return None

def send_notification(message):
    if not NTFY_TOPIC:
        print("ERROR: NTFY_TOPIC secret missing")
        return
    print(message)
    try:
        r = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={
                "Title": "Closest to Discount Zone",
                "Priority": "high",
                "Tags": "chart_with_upwards_trend"
            },
            timeout=15
        )
        print(f"ntfy response: {r.status_code}")
    except Exception as e:
        print(f"Send failed: {e}")

def main():
    print(f"Starting scan at {datetime.utcnow()} UTC")
    tickers = load_tickers()
    print(f"Loaded {len(tickers)} tickers")

    results = []
    for i, ticker in enumerate(tickers):
        res = analyze_ticker(ticker)
        if res:
            results.append(res)
        if (i + 1) % 12 == 0:
            time.sleep(0.6)

    # Sort by how close they are to the discount zone (smallest distance first)
    results.sort(key=lambda x: x["distance_pct"])

    # Take the top 5 closest
    top5 = results[:5]

    msg = f"Top 5 closest to Discount Zone\n{datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC\n\n"
    for r in top5:
        status = "INSIDE" if r["distance_pct"] <= 0 else f"+{r['distance_pct']}%"
        msg += f"{r['ticker']}: ${r['price']} | Zone {r['zone_low']}-{r['zone_top']} | {status}\n"

    send_notification(msg)

if __name__ == "__main__":
    main()
