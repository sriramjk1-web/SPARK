"""Summarise paper-trading results from trades.csv."""
import os
import pandas as pd

path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trades.csv")
if not os.path.exists(path):
    print("No trades yet. trades.csv appears after the first SELL.")
    raise SystemExit

df = pd.read_csv(path)
wins = df[df["pnl_rs"] > 0]
losses = df[df["pnl_rs"] <= 0]

print(f"Total trades : {len(df)}")
print(f"Winning      : {len(wins)}  ({len(wins) / len(df) * 100:.1f}%)")
print(f"Losing       : {len(losses)}")
print(f"Net P&L      : Rs {df['pnl_rs'].sum():,.2f}")
print(f"Average win  : Rs {wins['pnl_rs'].mean() if len(wins) else 0:,.2f}")
print(f"Average loss : Rs {losses['pnl_rs'].mean() if len(losses) else 0:,.2f}")
print(f"Biggest loss : Rs {df['pnl_rs'].min():,.2f}")
print("\nP&L by stock:")
print(df.groupby("symbol")["pnl_rs"].agg(["count", "sum"]).round(2))
