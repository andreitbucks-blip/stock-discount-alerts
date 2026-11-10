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
LOOKBACK_DAYS = 300
SWING_LENGTH = 40
DISCOUNT_PERCENT = 0.35
# ====================================================

def load_tickers():
    with open(TICKERS_FILE) as f:
        tickers = [line.strip().upper() for line in f if line.strip() and not line.startswith("#")]
    return list(dict.fromkeys(tickers))

def analyze_ticker(ticker):
    try:
        df = yf.download(ticker, period=f"{LOOKBACK_DAYS}d", progress=False, auto_adjust=True, threads=False)
        if df is None or df.empty or len(df) < SWING_LENGTH + 5:
            return None

        df = df.dropna()
        close = float(df["Close"].iloc[-1])
        high = df["High"]
        low = df["Low"]

        swing_high = float(high.rolling(SWING_LENGTH).max().iloc[-1])
        swing_low = float(low.rolling(SWING_LENGTH).min().iloc[-1])

        if swing_high <= swing_low:
            return None

        range_size = swing_high - swing_low
        discount_top = swing_low + (DISCOUNT_PERCENT * range_size)
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
        print("ERROR: NTFY_TOPIC secret is missing")
        return
    print(message)
    try:
        r = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={
                "Title": "Closest to Discount Zone",
                "Priority": "high"
            },
            timeout=15
        )
        print(f"ntfy status: {r.status_code}")
    except Exception as e:
        print(f"Send error: {e}")

def main():
    print(f"Scan started: {datetime.utcnow()} UTC")
    tickers = load_tickers()
    print(f"Tickers loaded: {len(tickers)}")

    results = []
    success = 0
    for i, ticker in enumerate(tickers):
        res = analyze_ticker(ticker)
        if res:
            results.append(res)
            success += 1
        if (i + 1) % 10 == 0:
            time.sleep(0.8)

    print(f"Successfully analyzed: {success} tickers")

    if not results:
        msg = f"No valid data returned from any ticker.\n{datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC"
        send_notification(msg)
        return

    results.sort(key=lambda x: x["distance_pct"])
    top5 = results[:5]

    msg = f"Top 5 closest to Discount Zone\n{datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC\n\n"
    for r in top5:
        status = "INSIDE" if r["distance_pct"] <= 0 else f"+{r['distance_pct']}%"
        msg += f"{r['ticker']}: ${r['price']} | {r['zone_low']}-{r['zone_top']} | {status}\n"

    send_notification(msg)

if __name__ == "__main__":
    main()
