import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import requests
import os
import time
import json

# ====================== CONFIG ======================
TICKERS_FILE = "tickers.txt"
NTFY_TOPIC = os.getenv("NTFY_TOPIC")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

LOOKBACK = "5y"
SWING_LENGTH = 50
DISCOUNT_PCT = 0.05

ST_LENGTH = 7
ST_MULT = 2.0

SL_PCT = 0.035
TP1_PCT = 0.05
TP2_PCT = 0.10
TOP_N = 5
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


def call_gemini(tickers_data):
    """Send the exact user prompt + tickers to Gemini and return the analysis"""
    if not GEMINI_API_KEY:
        return "Gemini API key missing"

    # Build the list of tickers with current data
    ticker_list = ""
    for item in tickers_data:
        ticker_list += f"- {item['ticker']}: Current Price ${item['price']}, Discount Zone {item['zone_low']}-{item['zone_top']}\n"

    full_prompt = f"""Act as an institutional swing trader and equity research analyst. Your objective is to evaluate the stock tickers provided at the end of this prompt for a short-to-mid-term swing trade (holding period: 1 day to 1 month) at a discounted price zone.

### Ground Rules
- Use live web search/market data to verify all current prices, dates, and news. State the timestamp of the price data used.
- Do not invent or guess technical indicators, volume figures, or dates. If real-time chart data is unavailable, explicitly state what you cannot see.
- Separate verified facts from your trading assumptions.

### Analysis Checklist
Evaluate each stock across these 8 criteria:

1. **Catalyst Check (Temporary Panic vs. Broken Business):** Identify why the stock recently dropped. Determine if the sell-off is driven by temporary market overreaction/macro drag or a structural business breakdown.
2. **Binary Event Risk:** Check the calendar for the next 30 days. List any upcoming earnings dates, FDA rulings, court verdicts, or major product/macro events that create overnight gap risk.
3. **Volume & Support Validation:** Identify the current discounted support zone. Verify whether past price action shows heavy historical buying volume at this exact price level.
4. **Reversal Confirmation:** Assess whether selling pressure is exhausted. Look for bullish reversal signals.
5. **Broader Market Context:** Check the current short-term trend of the S&P 500 (SPY) and the stock's specific sector ETF.
6. **Exact Invalidation Point:** Define the exact price level below the support zone where the bounce thesis fails.
7. **Risk-to-Reward Ratio (Minimum 1:2):** Calculate a concrete trade setup.
8. **Liquidity Check:** Verify average daily trading volume.

### Final Output Format
For each ticker start with a bold **VERDICT: BUY / WAIT / PASS**, followed by a 2-sentence summary, then the numbered breakdown 1–8.

Analyze these Stock Tickers:
{ticker_list}
"""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    
    payload = {
        "contents": [{
            "parts": [{"text": full_prompt}]
        }],
        "generationConfig": {
            "temperature": 0.4,
            "maxOutputTokens": 4096
        }
    }

    try:
        response = requests.post(url, json=payload, timeout=90)
        if response.status_code == 200:
            data = response.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]
        else:
            return f"Gemini error {response.status_code}: {response.text[:300]}"
    except Exception as e:
        return f"Gemini call failed: {str(e)}"


def send_notification(message):
    if not NTFY_TOPIC:
        print("NTFY_TOPIC missing")
        return
    print(message[:2000] + ("..." if len(message) > 2000 else ""))
    try:
        # ntfy has a practical size limit, so we truncate very long AI answers
        if len(message) > 3500:
            message = message[:3400] + "\n\n[AI analysis truncated]"
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

    # Decide what to analyze with AI
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

    # Basic info first
    basic = ""
    for item in to_analyze:
        status = "BUY" if item["is_buy"] else f"+{item['distance_pct']}%"
        basic += f"{item['ticker']}: ${item['price']} | Zone {item['zone_low']}-{item['zone_top']} | {status}\n"
        if item["is_buy"]:
            basic += f"  SL: ${item['stop_loss']} | TP1: ${item['tp1']} | TP2: ${item['tp2']}\n"
        basic += "\n"

    print("Calling Gemini for analysis...")
    ai_analysis = call_gemini(to_analyze)

    full_message = header + basic + "\n——— AI ANALYSIS ——\n\n" + ai_analysis
    send_notification(full_message)


if __name__ == "__main__":
    main()
