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
SWING_LENGTH = 50          # same idea as LuxAlgo
DISCOUNT_PERCENT = 0.30    # lower 30% of the swing range = Discount zone
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

def is_in_discount_zone(close, swing_high, swing_low):
    if swing_high <= swing_low or np.isnan(swing_high) or np.isnan(swing_low):
        return False, None, None

    range_size = swing_high - swing_low
    discount_top = swing_low + (DISCOUNT_PERCENT * range_size)

    in_zone = close <= discount_top
    return in_zone, swing_low, discount_top

def check_ticker(ticker):
    try:
        df = yf.download(ticker, period=f"{LOOKBACK_DAYS}d", progress=False, auto_adjust=True, threads=False)
        if df is None or df.empty or len(df) < SWING_LENGTH + 10:
            return None

        df = df.dropna()
        close = float(df["Close"].iloc[-1])
        swing_high, swing_low = get_swing_high_low(df, SWING_LENGTH)
        in_zone, zone_low, zone_top = is_in_discount_zone(close, swing_high, swing_low)

        if not in_zone:
            return None

        return {
            "ticker": ticker,
            "price": round(close, 2),
            "zone_low": round(zone_low, 2),
            "zone_top": round(zone_top, 2)
        }
    except Exception:
        return None

def send_notification(alerts):
    if not NTFY_TOPIC:
        print("NTFY_TOPIC secret is missing")
        return

    header = f"{len(alerts)} stock(s) in Discount Zone\n{datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC\n\n"
    body = ""
    for a in alerts:
        body += f"{a['ticker']}: ${a['price']} | Zone {a['zone_low']} - {a['zone_top']}\n"

    full_msg = header + body
    print(full_msg)

    # Important: use only normal ASCII characters
    requests.post(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=full_msg.encode("utf-8"),
        headers={
            "Title": "Discount Zone Alert",
            "Priority": "high",
            "Tags": "chart_with_upwards_trend"
        }
    )

def main():
    print(f"Starting scan at {datetime.utcnow()} UTC")
    tickers = load_tickers()
    print(f"Loaded {len(tickers)} tickers")

    alerts = []
    for i, ticker in enumerate(tickers):
        result = check_ticker(ticker)
        if result:
            alerts.append(result)
        if (i + 1) % 15 == 0:
            time.sleep(1.0)

    if alerts:
        send_notification(alerts)
    else:
        print("No stocks currently in the discount zone.")

if __name__ == "__main__":
    main()
