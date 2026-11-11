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

LOOKBACK = "5y"              # long history so swings match TradingView better
SWING_LENGTH = 50            # LuxAlgo default swing length
DISCOUNT_PCT = 0.05          # exact LuxAlgo bottom 5%

# Supertrend filter
ST_LENGTH = 7
ST_MULT = 2.0

# Risk management
SL_PCT = 0.035               # 3.5% stop
TP1_PCT = 0.05               # +5%
TP2_PCT = 0.10               # +10%

TOP_N = 5
# ====================================================

def load_tickers():
    with open(TICKERS_FILE) as f:
        tickers = [line.strip().upper() for line in f if line.strip() and not line.startswith("#")]
    return list(dict.fromkeys(tickers))


def find_trailing_extremes(df, size=50):
    """
    Improved LuxAlgo-style trailing high / low (from your attached script).
    Detects confirmed swing points and then trails them.
    """
    high = df["High"].values
    low = df["Low"].values
    n = len(df)

    leg = 0
    top = None
    bottom = None

    for i in range(n):
        prev_leg = leg

        if i >= size:
            if high[i - size] > high[i - size + 1 : i + 1].max():
                leg = 0
            elif low[i - size] < low[i - size + 1 : i + 1].min():
                leg = 1

        if leg != prev_leg:
            if leg == 1:
                bottom = low[i - size]
            else:
                top = high[i - size]

        if top is not None:
            top = max(top, high[i])
        if bottom is not None:
            bottom = min(bottom, low[i])

    return top, bottom


def calculate_supertrend(df, length=7, multiplier=2.0):
    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    tr = pd.concat([
        high - low,
        abs(high - close.shift()),
        abs(low - close.shift())
    ], axis=1).max(axis=1)

    atr = tr.rolling(length).mean()
    hl2 = (high + low) / 2
    upper = hl2 + multiplier * atr
    lower = hl2 - multiplier * atr

    direction = pd.Series(1, index=df.index)
    final_upper = upper.copy()
    final_lower = lower.copy()

    for i in range(1, len(df)):
        if close.iloc[i] > final_upper.iloc[i-1]:
            direction.iloc[i] = 1
        elif close.iloc[i] < final_lower.iloc[i-1]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = direction.iloc[i-1]
            if direction.iloc[i] == 1 and final_lower.iloc[i] < final_lower.iloc[i-1]:
                final_lower.iloc[i] = final_lower.iloc[i-1]
            if direction.iloc[i] == -1 and final_upper.iloc[i] > final_upper.iloc[i-1]:
                final_upper.iloc[i] = final_upper.iloc[i-1]

    return direction.iloc[-1] == 1


def analyze_ticker(ticker):
    try:
        t = yf.Ticker(ticker)
        df = t.history(period=LOOKBACK, auto_adjust=False)

        if df is None or df.empty or len(df) < SWING_LENGTH + 30:
            return None

        df = df.dropna(subset=["High", "Low", "Close", "Volume", "Open"])
        close = float(df["Close"].iloc[-1])
        open_ = float(df["Open"].iloc[-1])
        volume = float(df["Volume"].iloc[-1])

        # Supertrend must be bullish
        if not calculate_supertrend(df, ST_LENGTH, ST_MULT):
            return None

        # LuxAlgo-style trailing extremes
        swing_high, swing_low = find_trailing_extremes(df, SWING_LENGTH)
        if swing_high is None or swing_low is None or swing_high <= swing_low:
            return None

        swing_high = float(swing_high)
        swing_low = float(swing_low)

        # Exact LuxAlgo discount zone (bottom 5%)
        discount_top = (1 - DISCOUNT_PCT) * swing_low + DISCOUNT_PCT * swing_high
        in_zone = close <= discount_top

        # Volume confirmation
        volume_ma20 = float(df["Volume"].rolling(20).mean().iloc[-1])
        volume_ok = volume > volume_ma20

        # Bullish candle
        bullish_candle = close > open_

        # Final buy signal
        is_buy = in_zone and bullish_candle and volume_ok

        distance_pct = ((close - discount_top) / discount_top) * 100 if discount_top > 0 else 999

        return {
            "ticker": ticker,
            "price": round(close, 2),
            "zone_low": round(swing_low, 2),
            "zone_top": round(discount_top, 2),
            "distance_pct": round(distance_pct, 1),
            "in_zone": in_zone,
            "is_buy": is_buy,
            "stop_loss": round(close * (1 - SL_PCT), 2),
            "tp1": round(close * (1 + TP1_PCT), 2),
            "tp2": round(close * (1 + TP2_PCT), 2)
        }

    except Exception as e:
        print(f"{ticker} error: {e}")
        return None


def send_notification(message):
    if not NTFY_TOPIC:
        print("NTFY_TOPIC missing")
        return
    print(message)
    try:
        r = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={"Title": "Daily Discount Strategy", "Priority": "high"},
            timeout=15
        )
        print(f"ntfy: {r.status_code}")
    except Exception as e:
        print(f"Send failed: {e}")


def main():
    print(f"Scan started: {datetime.utcnow()} UTC")
    tickers = load_tickers()
    print(f"Tickers loaded: {len(tickers)}")

    results = []
    buys = []

    for i, ticker in enumerate(tickers):
        res = analyze_ticker(ticker)
        if res:
            results.append(res)
            if res["is_buy"]:
                buys.append(res)
                print(f"BUY → {ticker}")
        if (i + 1) % 8 == 0:
            time.sleep(1.0)

    print(f"Valid: {len(results)} | Buys: {len(buys)}")
    stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M")

    # Case A – Buy signals
    if buys:
        msg = f"{len(buys)} BUY SIGNAL(S)\n{stamp} UTC\n\n"
        for b in buys:
            msg += (
                f"{b['ticker']}: ${b['price']}\n"
                f"  Zone: {b['zone_low']} - {b['zone_top']}\n"
                f"  SL: ${b['stop_loss']} | TP1: ${b['tp1']} | TP2: ${b['tp2']}\n\n"
            )
        send_notification(msg)
        return

    # Case B – Top 5 closest
    if not results:
        send_notification(f"No valid data\n{stamp} UTC")
        return

    results.sort(key=lambda x: x["distance_pct"])
    top5 = results[:TOP_N]

    msg = f"No buys – Top {TOP_N} closest to Discount Zone\n{stamp} UTC\n\n"
    for r in top5:
        status = "INSIDE" if r["in_zone"] else f"+{r['distance_pct']}%"
        msg += f"{r['ticker']}: ${r['price']} | {r['zone_low']}-{r['zone_top']} | {status}\n"

    send_notification(msg)


if __name__ == "__main__":
    main()
