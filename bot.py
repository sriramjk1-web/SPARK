"""
Paper-trading signal bot for NSE (no real money).
- Runs Mon-Fri, 09:15-15:30 IST
- Every 5 minutes fetches 5-minute candles via yfinance
- Signal: SMA 9 / SMA 21 crossover, filtered by RSI(14)
- Sends signals to Telegram, logs closed trades with P&L to trades.csv
Educational use only. Not financial advice.
"""
import csv
import json
import os
import time
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf

# ---------------- Settings ----------------
SYMBOLS = {
    "NIFTYBEES": "NIFTYBEES.NS",
    "RELIANCE": "RELIANCE.NS",
    "HDFCBANK": "HDFCBANK.NS",
    "INFY": "INFY.NS",
}
CAPITAL_PER_TRADE = 10000          # virtual rupees per trade
SMA_FAST, SMA_SLOW, RSI_LEN = 9, 21, 14
RSI_BUY_MAX = 70                   # don't buy when overbought
CHECK_EVERY_SEC = 300              # 5 minutes
IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN, MARKET_CLOSE = dtime(9, 15), dtime(15, 30)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

BASE = os.path.dirname(os.path.abspath(__file__))
TRADES_CSV = os.path.join(BASE, "trades.csv")
STATE_FILE = os.path.join(BASE, "positions.json")


# ---------------- Helpers ----------------
def now_ist():
    return datetime.now(IST)


def market_is_open(t=None):
    t = t or now_ist()
    return t.weekday() < 5 and MARKET_OPEN <= t.time() <= MARKET_CLOSE
    # Note: NSE holidays are not checked; yfinance simply returns no new data.


def send_telegram(text):
    print(text)
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": text},
            timeout=10,
        )
    except Exception as e:
        print("Telegram error:", e)


def load_positions():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_positions(pos):
    with open(STATE_FILE, "w") as f:
        json.dump(pos, f, indent=2)


def log_trade(row):
    new = not os.path.exists(TRADES_CSV)
    with open(TRADES_CSV, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if new:
            w.writeheader()
        w.writerow(row)


# ---------------- Indicators ----------------
def rsi(close, length=RSI_LEN):
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / length, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / length, adjust=False).mean()
    rs = gain / loss.replace(0, 1e-10)
    return 100 - 100 / (1 + rs)


def get_signal(df):
    """Return ('BUY' | 'SELL' | 'WAIT', price, rsi_value)."""
    close = df["Close"]
    fast = close.rolling(SMA_FAST).mean()
    slow = close.rolling(SMA_SLOW).mean()
    r = rsi(close)
    price, r_now = float(close.iloc[-1]), float(r.iloc[-1])
    crossed_up = fast.iloc[-2] <= slow.iloc[-2] and fast.iloc[-1] > slow.iloc[-1]
    crossed_down = fast.iloc[-2] >= slow.iloc[-2] and fast.iloc[-1] < slow.iloc[-1]
    if crossed_up and r_now < RSI_BUY_MAX:
        return "BUY", price, r_now
    if crossed_down:
        return "SELL", price, r_now
    return "WAIT", price, r_now


def fetch(ticker):
    df = yf.download(ticker, period="5d", interval="5m",
                     progress=False, auto_adjust=False)
    if isinstance(df.columns, pd.MultiIndex):        # newer yfinance versions
        df.columns = df.columns.get_level_values(0)
    df = df.dropna()
    # drop the still-forming candle so signals use completed bars only
    if len(df) > 1 and df.index[-1].tz_convert(IST) > now_ist().replace(second=0) - pd.Timedelta(minutes=5):
        df = df.iloc[:-1]
    return df


# ---------------- Main logic ----------------
def process(name, df, positions, ts):
    if len(df) < SMA_SLOW + 2:
        return
    signal, price, r_now = get_signal(df)
    pos = positions.get(name)

    if signal == "BUY" and pos is None:
        qty = max(1, int(CAPITAL_PER_TRADE // price))
        positions[name] = {"qty": qty, "entry": price, "time": ts}
        send_telegram(f"🟢 BUY {name} @ ₹{price:.2f} | qty {qty} | RSI {r_now:.1f}")
    elif signal == "SELL" and pos is not None:
        pnl = (price - pos["entry"]) * pos["qty"]
        pct = (price / pos["entry"] - 1) * 100
        log_trade({
            "symbol": name, "entry_time": pos["time"], "exit_time": ts,
            "qty": pos["qty"], "entry_price": round(pos["entry"], 2),
            "exit_price": round(price, 2), "pnl_rs": round(pnl, 2),
            "pnl_pct": round(pct, 2), "reason": "SMA cross down",
        })
        del positions[name]
        send_telegram(f"🔴 SELL {name} @ ₹{price:.2f} | P&L ₹{pnl:.2f} ({pct:+.2f}%)")
    else:
        print(f"{ts} {name}: WAIT ₹{price:.2f} RSI {r_now:.1f}")


def square_off_all(positions, ts):
    """Close every open paper position at day end (intraday style)."""
    for name in list(positions):
        df = fetch(SYMBOLS[name])
        if df.empty:
            continue
        price = float(df["Close"].iloc[-1])
        pos = positions.pop(name)
        pnl = (price - pos["entry"]) * pos["qty"]
        log_trade({
            "symbol": name, "entry_time": pos["time"], "exit_time": ts,
            "qty": pos["qty"], "entry_price": round(pos["entry"], 2),
            "exit_price": round(price, 2), "pnl_rs": round(pnl, 2),
            "pnl_pct": round((price / pos["entry"] - 1) * 100, 2),
            "reason": "End of day square-off",
        })
        send_telegram(f"⏹ EOD exit {name} @ ₹{price:.2f} | P&L ₹{pnl:.2f}")
    save_positions(positions)


def main():
    send_telegram("🤖 Paper-trading bot started")
    positions = load_positions()
    squared_today = None
    while True:
        t = now_ist()
        ts = t.strftime("%Y-%m-%d %H:%M")
        if market_is_open(t):
            for name, ticker in SYMBOLS.items():
                try:
                    process(name, fetch(ticker), positions, ts)
                except Exception as e:
                    print(f"{name} error: {e}")
            save_positions(positions)
            # square off at 15:20
            if t.time() >= dtime(15, 20) and squared_today != t.date():
                square_off_all(positions, ts)
                squared_today = t.date()
            time.sleep(CHECK_EVERY_SEC)
        else:
            time.sleep(60)
def run_once():
    positions = load_positions()
    t = now_ist()
    ts = t.strftime("%Y-%m-%d %H:%M")
    if market_is_open(t):
        for name, ticker in SYMBOLS.items():
            try:
                process(name, fetch(ticker), positions, ts)
            except Exception as e:
                print(f"{name} error: {e}")
        save_positions(positions)
    if t.weekday() < 5 and t.time() >= dtime(15, 20) and positions:
        square_off_all(positions, ts)


if __name__ == "__main__":
    import sys
    if "--once" in sys.argv:
        run_once()
    else:
        main()
