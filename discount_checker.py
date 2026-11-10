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
DISCOUNT_PERCENT = 0.30 # lower 30% of the range
def send_notification(message):
    if not NTFY_TOPIC:
        print("ERROR: NTFY_TOPIC secret is missing")
        return
    print("Sending notification...")
    print(message)
    try:
        r = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={
                "Title": "Discount Zone Alert",
                "Priority": "high",
                "Tags": "chart_with_upwards_trend"
            },
            timeout=15
        )
        print(f"ntfy response: {r.status_code}")
    except Exception as e:
        print(f"Failed to send notification: {e}")

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
            time.sleep(0.8)

    if alerts:
        msg = f"{len(alerts)} stock(s) in Discount Zone\n{datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC\n\n"
        for a in alerts:
            msg += f"{a['ticker']}: ${a['price']} | Zone {a['zone_low']} - {a['zone_top']}\n"
    else:
        msg = f"No stocks currently in the discount zone.\n{datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC"

    # Always send a notification so we can test the phone
    send_notification(msg)

if __name__ == "__main__":
    main()
