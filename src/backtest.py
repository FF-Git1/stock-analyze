from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .indicators import add_moving_averages


TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True)
class Trade:
    trade_id: int
    buy_date: str
    buy_price: float
    buy_shares: int
    buy_amount: float
    buy_fee: float
    sell_date: str
    sell_price: float
    sell_amount: float
    sell_fee: float
    profit: float
    return_pct: float
    holding_days: int
    sell_reason: str


def add_strategy_signals(data: pd.DataFrame) -> pd.DataFrame:
    df = add_moving_averages(data, windows=(20, 60))
    df["buy_signal"] = (df["close"] > df["ma20"]) & (df["close"].shift(1) <= df["ma20"].shift(1)) & (df["ma20"] > df["ma60"])
    df["sell_signal"] = ((df["close"] < df["ma20"]) & (df["close"].shift(1) >= df["ma20"].shift(1))) | (df["ma20"] < df["ma60"])
    return df


def run_ma_cross_backtest(data: pd.DataFrame, initial_cash: float, position_ratio: float, commission_rate: float) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    df = add_strategy_signals(data)
    cash = initial_cash
    shares = 0
    position_cost = 0.0
    buy_date = None
    buy_price = buy_amount = buy_fee = 0.0
    pending_signal = None
    trades: list[Trade] = []
    equity_rows = []

    for _, row in df.iterrows():
        date = row["date"]
        open_price = float(row["open"])
        close_price = float(row["close"])

        if pending_signal == "sell" and shares > 0:
            sell_amount = shares * open_price
            sell_fee = sell_amount * commission_rate
            cash += sell_amount - sell_fee
            profit = sell_amount - sell_fee - position_cost
            trades.append(_make_trade(len(trades) + 1, buy_date, buy_price, shares, buy_amount, buy_fee, date, open_price, sell_amount, sell_fee, profit, position_cost, "跌破MA20或MA20小于MA60"))
            shares = 0
            position_cost = buy_price = buy_amount = buy_fee = 0.0
            buy_date = None

        if pending_signal == "buy" and shares == 0:
            budget = cash * position_ratio
            buyable_shares = int((budget / (open_price * (1 + commission_rate))) // 100 * 100)
            if buyable_shares > 0:
                buy_amount = buyable_shares * open_price
                buy_fee = buy_amount * commission_rate
                cash -= buy_amount + buy_fee
                shares = buyable_shares
                position_cost = buy_amount + buy_fee
                buy_date = date
                buy_price = open_price

        equity_rows.append({"date": date, "cash": cash, "shares": shares, "close": close_price, "market_value": shares * close_price, "equity": cash + shares * close_price})

        pending_signal = None
        if not pd.isna(row["ma20"]) and not pd.isna(row["ma60"]):
            if shares == 0 and bool(row["buy_signal"]):
                pending_signal = "buy"
            elif shares > 0 and bool(row["sell_signal"]):
                pending_signal = "sell"

    equity_curve = pd.DataFrame(equity_rows)
    if shares > 0:
        last = df.iloc[-1]
        sell_amount = shares * float(last["close"])
        sell_fee = sell_amount * commission_rate
        cash += sell_amount - sell_fee
        profit = sell_amount - sell_fee - position_cost
        trades.append(_make_trade(len(trades) + 1, buy_date, buy_price, shares, buy_amount, buy_fee, last["date"], float(last["close"]), sell_amount, sell_fee, profit, position_cost, "回测结束强制平仓"))
        equity_curve.loc[equity_curve.index[-1], ["cash", "shares", "market_value", "equity"]] = [cash, 0, 0.0, cash]

    trades_df = pd.DataFrame([asdict(trade) for trade in trades])
    summary = summarize_backtest(df, equity_curve, trades_df, initial_cash)
    return summary, trades_df, equity_curve


def _make_trade(trade_id: int, buy_date, buy_price: float, shares: int, buy_amount: float, buy_fee: float, sell_date, sell_price: float, sell_amount: float, sell_fee: float, profit: float, position_cost: float, reason: str) -> Trade:
    holding_days = int((sell_date - buy_date).days) if buy_date is not None else 0
    return Trade(
        trade_id=trade_id,
        buy_date=buy_date.strftime("%Y-%m-%d") if buy_date is not None else "",
        buy_price=round(buy_price, 4),
        buy_shares=shares,
        buy_amount=round(buy_amount, 2),
        buy_fee=round(buy_fee, 2),
        sell_date=sell_date.strftime("%Y-%m-%d"),
        sell_price=round(sell_price, 4),
        sell_amount=round(sell_amount, 2),
        sell_fee=round(sell_fee, 2),
        profit=round(profit, 2),
        return_pct=round(profit / position_cost * 100, 4) if position_cost else 0.0,
        holding_days=holding_days,
        sell_reason=reason,
    )


def summarize_backtest(data: pd.DataFrame, equity_curve: pd.DataFrame, trades_df: pd.DataFrame, initial_cash: float) -> dict:
    final_equity = float(equity_curve["equity"].iloc[-1])
    total_return = final_equity / initial_cash - 1
    days = max((data["date"].iloc[-1] - data["date"].iloc[0]).days, 1)
    annual_return = (final_equity / initial_cash) ** (365 / days) - 1
    drawdown = equity_curve["equity"] / equity_curve["equity"].cummax() - 1
    daily_returns = equity_curve["equity"].pct_change().dropna()
    std = float(daily_returns.std(ddof=1)) if len(daily_returns) > 1 else 0.0
    sharpe = 0.0 if std == 0 or np.isnan(std) else float(daily_returns.mean() / std * np.sqrt(TRADING_DAYS_PER_YEAR))
    trade_count = len(trades_df)
    win_rate = 0.0 if trade_count == 0 else float((trades_df["profit"] > 0).mean())
    return {
        "start_date": data["date"].iloc[0].strftime("%Y-%m-%d"),
        "end_date": data["date"].iloc[-1].strftime("%Y-%m-%d"),
        "initial_cash": round(initial_cash, 2),
        "final_equity": round(final_equity, 2),
        "total_return_pct": round(total_return * 100, 4),
        "annual_return_pct": round(annual_return * 100, 4),
        "max_drawdown_pct": round(float(drawdown.min()) * 100, 4),
        "sharpe_ratio": round(sharpe, 4),
        "trade_count": trade_count,
        "win_rate_pct": round(win_rate * 100, 4),
    }


def save_backtest_outputs(symbol: str, summary: dict, trades: pd.DataFrame, equity_curve: pd.DataFrame, output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "summary": output_dir / f"{symbol}_summary.csv",
        "trades": output_dir / f"{symbol}_trades.csv",
        "equity_curve": output_dir / f"{symbol}_equity_curve.csv",
    }
    pd.DataFrame([{**{"symbol": symbol}, **summary}]).to_csv(paths["summary"], index=False, encoding="utf-8-sig")
    trades.to_csv(paths["trades"], index=False, encoding="utf-8-sig")
    equity_curve.to_csv(paths["equity_curve"], index=False, encoding="utf-8-sig")
    return paths
