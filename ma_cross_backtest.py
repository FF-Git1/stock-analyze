from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

try:
    import numpy as np
    import pandas as pd
except ModuleNotFoundError as exc:
    np = None
    pd = None
    DEPENDENCY_IMPORT_ERROR = exc
else:
    DEPENDENCY_IMPORT_ERROR = None


DEFAULT_YEARS = 5
INITIAL_CASH = 100_000.0
POSITION_RATIO = 0.90
COMMISSION_RATE = 0.001
TRADING_DAYS_PER_YEAR = 252
MA20_WINDOW = 20
MA60_WINDOW = 60


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


@dataclass(frozen=True)
class BacktestSummary:
    symbol: str
    start_date: str
    end_date: str
    initial_cash: float
    final_equity: float
    total_return_pct: float
    annual_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    trade_count: int
    win_rate_pct: float
    equity_curve_path: str
    trades_path: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="A股 MA20/MA60 策略回测，数据源 akshare，使用前复权日线。")
    parser.add_argument("--symbol", required=True, help="A股代码，例如 000001、600519、300750。")
    parser.add_argument("--start-date", help="开始日期，格式 YYYYMMDD。默认近 5 年。")
    parser.add_argument("--end-date", help="结束日期，格式 YYYYMMDD。默认今天。")
    parser.add_argument("--initial-cash", type=float, default=INITIAL_CASH, help="初始资金，默认 100000。")
    parser.add_argument("--output-dir", default="backtest_output", help="输出目录。")
    return parser.parse_args()


def ensure_dependencies() -> None:
    if DEPENDENCY_IMPORT_ERROR is not None:
        raise RuntimeError("请先安装依赖：pip install akshare pandas numpy matplotlib") from DEPENDENCY_IMPORT_ERROR


def get_default_date_range() -> tuple[str, str]:
    end = datetime.now()
    start = end - timedelta(days=365 * DEFAULT_YEARS + 10)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def fetch_daily_data(symbol: str, start_date: str, end_date: str):
    import akshare as ak

    raw = ak.stock_zh_a_hist(
        symbol=symbol,
        period="daily",
        start_date=start_date,
        end_date=end_date,
        adjust="qfq",
    )
    if raw.empty:
        raise ValueError(f"未获取到 {symbol} 的行情数据，请检查股票代码、日期范围或 akshare 数据源状态。")

    data = raw.rename(
        columns={
            "日期": "date",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
            "成交额": "amount",
            "涨跌幅": "pct_change",
        }
    )
    data["date"] = pd.to_datetime(data["date"])
    for column in ["open", "close", "high", "low", "volume", "amount", "pct_change"]:
        if column in data.columns:
            data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.dropna(subset=["open", "close", "high", "low"]).sort_values("date").reset_index(drop=True)
    if len(data) < MA60_WINDOW + 2:
        raise ValueError(f"有效交易日不足 {MA60_WINDOW + 2} 天，无法完成 MA60 策略回测。")
    return add_indicators(data)


def add_indicators(data):
    df = data.copy()
    df["ma20"] = df["close"].rolling(MA20_WINDOW).mean()
    df["ma60"] = df["close"].rolling(MA60_WINDOW).mean()
    df["close_above_ma20"] = df["close"] > df["ma20"]
    df["prev_close_above_ma20"] = df["close_above_ma20"].shift(1)
    df["buy_signal"] = (
        (df["close"] > df["ma20"])
        & (df["close"].shift(1) <= df["ma20"].shift(1))
        & (df["ma20"] > df["ma60"])
    )
    df["sell_signal"] = (
        ((df["close"] < df["ma20"]) & (df["close"].shift(1) >= df["ma20"].shift(1)))
        | (df["ma20"] < df["ma60"])
    )
    return df


def run_backtest(data, symbol: str, initial_cash: float, output_dir: Path) -> tuple[BacktestSummary, list[Trade], object]:
    cash = initial_cash
    shares = 0
    position_cost = 0.0
    buy_date = None
    buy_price = 0.0
    buy_fee = 0.0
    buy_amount = 0.0
    trades: list[Trade] = []
    equity_rows = []
    pending_signal = None

    for index, row in data.iterrows():
        date = row["date"]
        open_price = float(row["open"])
        close_price = float(row["close"])

        if pending_signal == "sell" and shares > 0:
            sell_amount = shares * open_price
            sell_fee = sell_amount * COMMISSION_RATE
            cash += sell_amount - sell_fee
            profit = sell_amount - sell_fee - position_cost
            holding_days = int((date - buy_date).days) if buy_date is not None else 0
            trades.append(
                Trade(
                    trade_id=len(trades) + 1,
                    buy_date=buy_date.strftime("%Y-%m-%d") if buy_date is not None else "",
                    buy_price=round(buy_price, 4),
                    buy_shares=shares,
                    buy_amount=round(buy_amount, 2),
                    buy_fee=round(buy_fee, 2),
                    sell_date=date.strftime("%Y-%m-%d"),
                    sell_price=round(open_price, 4),
                    sell_amount=round(sell_amount, 2),
                    sell_fee=round(sell_fee, 2),
                    profit=round(profit, 2),
                    return_pct=round(profit / position_cost * 100, 4) if position_cost else 0.0,
                    holding_days=holding_days,
                    sell_reason="跌破MA20或MA20小于MA60",
                )
            )
            shares = 0
            position_cost = 0.0
            buy_date = None
            buy_price = 0.0
            buy_fee = 0.0
            buy_amount = 0.0

        if pending_signal == "buy" and shares == 0:
            budget = cash * POSITION_RATIO
            buyable_shares = int((budget / (open_price * (1 + COMMISSION_RATE))) // 100 * 100)
            if buyable_shares > 0:
                buy_amount = buyable_shares * open_price
                buy_fee = buy_amount * COMMISSION_RATE
                cash -= buy_amount + buy_fee
                shares = buyable_shares
                position_cost = buy_amount + buy_fee
                buy_date = date
                buy_price = open_price

        market_value = shares * close_price
        equity = cash + market_value
        equity_rows.append(
            {
                "date": date,
                "cash": cash,
                "shares": shares,
                "close": close_price,
                "market_value": market_value,
                "equity": equity,
                "ma20": row["ma20"],
                "ma60": row["ma60"],
            }
        )

        pending_signal = None
        if not pd.isna(row["ma20"]) and not pd.isna(row["ma60"]):
            if shares == 0 and bool(row["buy_signal"]):
                pending_signal = "buy"
            elif shares > 0 and bool(row["sell_signal"]):
                pending_signal = "sell"

    if shares > 0:
        last = data.iloc[-1]
        sell_price = float(last["close"])
        sell_amount = shares * sell_price
        sell_fee = sell_amount * COMMISSION_RATE
        cash += sell_amount - sell_fee
        profit = sell_amount - sell_fee - position_cost
        holding_days = int((last["date"] - buy_date).days) if buy_date is not None else 0
        trades.append(
            Trade(
                trade_id=len(trades) + 1,
                buy_date=buy_date.strftime("%Y-%m-%d") if buy_date is not None else "",
                buy_price=round(buy_price, 4),
                buy_shares=shares,
                buy_amount=round(buy_amount, 2),
                buy_fee=round(buy_fee, 2),
                sell_date=last["date"].strftime("%Y-%m-%d"),
                sell_price=round(sell_price, 4),
                sell_amount=round(sell_amount, 2),
                sell_fee=round(sell_fee, 2),
                profit=round(profit, 2),
                return_pct=round(profit / position_cost * 100, 4) if position_cost else 0.0,
                holding_days=holding_days,
                sell_reason="回测结束强制平仓",
            )
        )
        equity_rows[-1]["cash"] = cash
        equity_rows[-1]["shares"] = 0
        equity_rows[-1]["market_value"] = 0.0
        equity_rows[-1]["equity"] = cash

    equity_curve = pd.DataFrame(equity_rows)
    summary = build_summary(symbol=symbol, data=data, equity_curve=equity_curve, trades=trades, initial_cash=initial_cash, output_dir=output_dir)
    return summary, trades, equity_curve


def build_summary(symbol: str, data, equity_curve, trades: list[Trade], initial_cash: float, output_dir: Path) -> BacktestSummary:
    final_equity = float(equity_curve["equity"].iloc[-1])
    total_return = final_equity / initial_cash - 1
    days = max((data["date"].iloc[-1] - data["date"].iloc[0]).days, 1)
    annual_return = (final_equity / initial_cash) ** (365 / days) - 1
    running_max = equity_curve["equity"].cummax()
    drawdown = equity_curve["equity"] / running_max - 1
    max_drawdown = float(drawdown.min())
    daily_returns = equity_curve["equity"].pct_change().dropna()
    sharpe_ratio = calc_sharpe_ratio(daily_returns)
    closed_trades = len(trades)
    winning_trades = sum(1 for trade in trades if trade.profit > 0)
    win_rate = winning_trades / closed_trades if closed_trades else 0.0

    chart_path = output_dir / f"{symbol}_equity_curve.png"
    trades_path = output_dir / f"{symbol}_trades.csv"

    return BacktestSummary(
        symbol=symbol,
        start_date=data["date"].iloc[0].strftime("%Y-%m-%d"),
        end_date=data["date"].iloc[-1].strftime("%Y-%m-%d"),
        initial_cash=round(initial_cash, 2),
        final_equity=round(final_equity, 2),
        total_return_pct=round(total_return * 100, 4),
        annual_return_pct=round(annual_return * 100, 4),
        max_drawdown_pct=round(max_drawdown * 100, 4),
        sharpe_ratio=round(sharpe_ratio, 4),
        trade_count=closed_trades,
        win_rate_pct=round(win_rate * 100, 4),
        equity_curve_path=str(chart_path),
        trades_path=str(trades_path),
    )


def calc_sharpe_ratio(daily_returns) -> float:
    if len(daily_returns) < 2:
        return 0.0
    std = float(daily_returns.std(ddof=1))
    if std == 0 or np.isnan(std):
        return 0.0
    return float(daily_returns.mean() / std * np.sqrt(TRADING_DAYS_PER_YEAR))


def save_outputs(summary: BacktestSummary, trades: list[Trade], equity_curve, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    trades_df = pd.DataFrame([asdict(trade) for trade in trades])
    trades_path = Path(summary.trades_path)
    equity_path = output_dir / f"{summary.symbol}_equity_curve.csv"
    summary_path = output_dir / f"{summary.symbol}_summary.csv"
    trades_df.to_csv(trades_path, index=False, encoding="utf-8-sig")
    equity_curve.to_csv(equity_path, index=False, encoding="utf-8-sig")
    pd.DataFrame([asdict(summary)]).to_csv(summary_path, index=False, encoding="utf-8-sig")
    create_equity_curve_chart(equity_curve=equity_curve, chart_path=Path(summary.equity_curve_path), symbol=summary.symbol)


def create_equity_curve_chart(equity_curve, chart_path: Path, symbol: str) -> None:
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(equity_curve["date"], equity_curve["equity"], label="资金曲线", color="#1f77b4", linewidth=1.5)
    ax.fill_between(equity_curve["date"], equity_curve["equity"], alpha=0.12, color="#1f77b4")
    ax.set_title(f"{symbol} MA20/MA60 策略资金曲线")
    ax.set_xlabel("日期")
    ax.set_ylabel("总资产")
    ax.grid(alpha=0.25)
    ax.legend(loc="upper left")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(chart_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def print_summary(summary: BacktestSummary, trades: list[Trade]) -> None:
    print("\n回测结果")
    print(f"股票代码：{summary.symbol}")
    print(f"回测区间：{summary.start_date} 至 {summary.end_date}")
    print(f"初始资金：{summary.initial_cash:.2f}")
    print(f"最终资产：{summary.final_equity:.2f}")
    print(f"总收益率：{summary.total_return_pct:.2f}%")
    print(f"年化收益率：{summary.annual_return_pct:.2f}%")
    print(f"最大回撤：{summary.max_drawdown_pct:.2f}%")
    print(f"夏普比率：{summary.sharpe_ratio:.4f}")
    print(f"交易次数：{summary.trade_count}")
    print(f"胜率：{summary.win_rate_pct:.2f}%")
    if trades:
        print("\n每笔交易明细：")
        print(pd.DataFrame([asdict(trade) for trade in trades]).to_string(index=False))
    else:
        print("\n每笔交易明细：无交易")
    print(f"\n交易明细 CSV：{Path(summary.trades_path).resolve()}")
    print(f"资金曲线图：{Path(summary.equity_curve_path).resolve()}")


def main() -> None:
    args = parse_args()
    ensure_dependencies()
    default_start, default_end = get_default_date_range()
    start_date = args.start_date or default_start
    end_date = args.end_date or default_end
    output_dir = Path(args.output_dir)
    data = fetch_daily_data(symbol=args.symbol, start_date=start_date, end_date=end_date)
    summary, trades, equity_curve = run_backtest(data=data, symbol=args.symbol, initial_cash=args.initial_cash, output_dir=output_dir)
    save_outputs(summary=summary, trades=trades, equity_curve=equity_curve, output_dir=output_dir)
    print_summary(summary=summary, trades=trades)


if __name__ == "__main__":
    main()
