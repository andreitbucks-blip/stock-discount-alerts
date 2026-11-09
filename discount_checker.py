import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import requests
import os
import time

# ====================== CONFIG ======================
TICKERS_FILE = "tickers.txt"          # one ticker per line
NTFY_TOPIC = os.getenv("NTFY_TOPIC")  # set this as GitHub Secret
LOOKBACK_DAYS = 500
SWING_LENGTH = 50                     # same as LuxAlgo default
REQUIRE_SUPERTREND = False            # set True if you want Supertrend filter
# ====================================================

def load_tickers():
    if not os.path.exists(TICKERS_FILE):
        raise FileNotFoundError(f"{TICKERS_FILE} not found")
    with open(TICKERS_FILE) as f:
        tickers = [line.strip().upper() for line in f if line.strip() and not line.startswith("#")]
    return list(dict.fromkeys(tickers))  # remove duplicates while keeping order

def get_swing_high_low(df, length=50):
    """
    Approximation of LuxAlgo swing logic.
    Returns the most recent confirmed swing high and swing low.
    """
    high = df["High"]
    low = df["Low"]
    
    # Simple but effective rolling extreme (close to LuxAlgo length-based swings)
    swing_high = high.rolling(window=length, center=False).max().iloc[-1]
    swing_low = low.rolling(window=length, center=False).min().iloc[-1]
    
    return float(swing_high), float(swing_low)

def is_in_discount_zone(close, swing_high, swing_low):
    """
    LuxAlgo-style Discount Zone:
    Lower part of the range between recent swing high and swing low.
    Original Pine uses roughly the bottom \~5% of the range.
    """
    if swing_high <= swing_low or np.isnan(swing_high) or np.isnan(swing_low):
        return False, None, None
    
    # Exact formula matching the spirit of the Pine script
    discount_top = 0.95 * swing_low + 0.05 * swing_high
    in_zone = (close >= swing_low) and (close <= discount_top)
    
    return in_zone, swing_low, discount_top

def check_ticker(ticker):
    try:
        df = yf.download(ticker, period=f"{LOOKBACK_DAYS}d", progress=False, auto_adjust=True, threads=False)
        
        if df.empty or len(df) < SWING_LENGTH + 10:
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
            "swing_low": round(zone_low, 2),
            "discount_top": round(zone_top, 2),
            "swing_high": round(swing_high, 2)
        }
    except Exception as e:
        print(f"Error {ticker}: {e}")
        return None

def send_notification(alerts):
    if not NTFY_TOPIC:
        print("NTFY_TOPIC not set – printing only")
        return
    
    header = f"{len(alerts)} stock(s) currently in Discount Zone\n{datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC\n\n"
    body = ""
    for a in alerts:
        body += f"{a['ticker']}: ${a['price']}  | Zone: {a['swing_low']} – {a['discount_top']}\n"
    
    full_msg = header + body
    print(full_msg)
    
    requests.post(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=full_msg.encode("utf-8"),
        headers={
            "Title": "Discount Zone Alert – Exact Stocks",
            "Priority": "high",
            "Tags": "chart_with_upwards_trend,moneybag"
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
        
        # polite rate limiting
        if (i + 1) % 12 == 0:
            time.sleep(1.2)
    
    if alerts:
        send_notification(alerts)
    else:
        print("No stocks currently in the discount zone.")

if __name__ == "__main__":
    main()

