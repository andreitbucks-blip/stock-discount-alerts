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
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

LOOKBACK = "5y"
SWING_LENGTH = 50
DISCOUNT_PCT = 0.05

ST_LENGTH = 7
ST_MULT = 2.0

SL_PCT = 0.035
TP1_PCT = 0.05
TP2_PCT = 0.10
TOP_N = 5

MAX_RETRIES = 6
RETRY_DELAY = 15
# ====================================================

def load_tickers():
    with open(TICKERS_FILE) as f:
        tickers = [line.strip().upper() for line in f if line.strip() and not line.startswith("#")]
    return list(dict.fromkeys(tickers))


def find_trailing_extremes(df, size=50):
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
    tr = pd.concat([high - low, abs(high - close.shift()), abs(low - close.shift())], axis=1).max(axis=1)
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

        if not calculate_supertrend(df, ST_LENGTH, ST_MULT):
            return None

        swing_high, swing_low = find_trailing_extremes(df, SWING_LENGTH)
        if swing_high is None or swing_low is None or swing_high <= swing_low:
            return None

        swing_high = float(swing_high)
        swing_low = float(swing_low)
        discount_top = (1 - DISCOUNT_PCT) * swing_low + DISCOUNT_PCT * swing_high
        in_zone = close <= discount_top

        volume_ma20 = float(df["Volume"].rolling(20).mean().iloc[-1])
        volume_ok = volume > volume_ma20
        bullish_candle = close > open_
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


def call_groq(tickers_data):
    """Call Groq with retries"""
    if not GROQ_API_KEY:
        return "GROQ_API_KEY is missing"

    ticker_list = ""
    for item in tickers_data:
        ticker_list += f"- {item['ticker']}: ${item['price']} | Zone {item['zone_low']}-{item['zone_top']}\n"

    prompt = f"""Act as an institutional swing trader. Evaluate the following stocks for a short-to-mid-term swing trade (1 day – 1 month) while they are near a discount zone.

For EACH ticker provide:

**VERDICT: BUY / WAIT / PASS**
- 2-sentence summary of main risk & reward
- Catalyst (temporary panic or broken business?)
- Next binary event in the next 30 days
- Exact invalidation (stop) price
- Suggested target (1-month)
- Risk:Reward ratio
- Liquidity note

Keep the answer concise and structured.

Stocks to analyze:
{ticker_list}
"""

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "llama-3.1-70b-versatile",
        "messages": [
            {"role": "system", "content": "You are a professional equity research analyst and swing trader."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "max_tokens": 3000
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            print(f"Groq attempt {attempt}/{MAX_RETRIES}...")
            response = requests.post(url, headers=headers, json=payload, timeout=60)

            if response.status_code == 200:
                data = response.json()
                text = data["choices"][0]["message"]["content"]
                print("Groq success!")
                return text

            if response.status_code in [429, 503, 502]:
                print(f"Groq {response.status_code} - retrying in {RETRY_DELAY}s...")
                time.sleep(RETRY_DELAY)
                continue
            else:
                return f"Groq error {response.status_code}: {response.text[:400]}"

        except Exception as e:
            print(f"Error on attempt {attempt}: {e}")
            time.sleep(RETRY_DELAY)

    return "Groq failed after multiple retries."


def send_notification(message):
    if not NTFY_TOPIC:
        print("NTFY_TOPIC missing")
        return
    print(message[:1500] + ("..." if len(message) > 1500 else ""))
    try:
        if len(message) > 3800:
            message = message[:3700] + "\n\n[AI analysis truncated]"
        r = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={"Title": "Daily Discount + AI Analysis", "Priority": "high"},
            timeout=20
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
        if (i + 1) % 8 == 0:
            time.sleep(1.0)

    print(f"Valid: {len(results)} | Buys: {len(buys)}")
    stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M")

    if buys:
        to_analyze = buys
        header = f"{len(buys)} BUY SIGNAL(S) + AI Analysis\n{stamp} UTC\n\n"
    elif results:
        results.sort(key=lambda x: x["distance_pct"])
        to_analyze = results[:TOP_N]
        header = f"Top {TOP_N} closest + AI Analysis\n{stamp} UTC\n\n"
    else:
        send_notification(f"No valid data\n{stamp} UTC")
        return

    basic = ""
    for item in to_analyze:
        status = "BUY" if item["is_buy"] else f"+{item['distance_pct']}%"
        basic += f"{item['ticker']}: ${item['price']} | Zone {item['zone_low']}-{item['zone_top']} | {status}\n"
        if item["is_buy"]:
            basic += f"  SL: ${item['stop_loss']} | TP1: ${item['tp1']} | TP2: ${item['tp2']}\n"
        basic += "\n"

    print("Calling Groq...")
    ai_analysis = call_groq(to_analyze)

    full_message = header + basic + "\n——— AI ANALYSIS ——\n\n" + ai_analysis
    send_notification(full_message)


if __name__ == "__main__":
    main()
