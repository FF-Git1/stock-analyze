from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from src.data_loader import date_range_by_years, fetch_a_share_daily
from src.indicators import add_common_indicators
from src.report import configure_matplotlib
from src.utils import resolve_path


DEFAULT_OUTPUT_DIR = "reports/quant_trade"
DEFAULT_YEARS = 3
DEFAULT_INITIAL_CASH = 100_000.0
DEFAULT_POSITION_RATIO = 0.9
DEFAULT_COMMISSION_RATE = 0.001
TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True)
class QuantTradeConfig:
    initial_cash: float
    position_ratio: float
    commission_rate: float
    stop_loss_pct: float
    take_profit_pct: float


@dataclass(frozen=True)
class Trade:
    trade_id: int
    buy_date: str
    buy_price: float
    shares: int
    sell_date: str
    sell_price: float
    profit: float
    return_pct: float
    holding_days: int
    sell_reason: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A股量化交易分析脚本：技术指标、交易信号、回测和Markdown报告。")
    parser.add_argument("stock", help="股票代码或名称，例如 000021、600519、深科技。")
    parser.add_argument("--years", type=int, default=DEFAULT_YEARS, help="回看年数，默认 3 年。")
    parser.add_argument("--start-date", help="开始日期，格式 YYYYMMDD；默认按 --years 计算。")
    parser.add_argument("--end-date", help="结束日期，格式 YYYYMMDD；默认今天。")
    parser.add_argument("--adjust", default="qfq", choices=["", "qfq", "hfq"], help="复权方式，默认前复权 qfq。")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="输出目录。")
    parser.add_argument("--initial-cash", type=float, default=DEFAULT_INITIAL_CASH, help="初始资金。")
    parser.add_argument("--position-ratio", type=float, default=DEFAULT_POSITION_RATIO, help="单次建仓资金比例。")
    parser.add_argument("--commission-rate", type=float, default=DEFAULT_COMMISSION_RATE, help="单边交易费率。")
    parser.add_argument("--stop-loss-pct", type=float, default=8.0, help="止损比例，默认 8%%。")
    parser.add_argument("--take-profit-pct", type=float, default=25.0, help="止盈比例，默认 25%%。")
    parser.add_argument("--cache-csv", help="行情接口失败时使用的本地CSV，列名需兼容项目行情字段。")
    return parser


def normalize_code(value: str) -> str | None:
    text = value.strip()
    return text.zfill(6) if text.isdigit() and len(text) <= 6 else None


def resolve_stock(stock: str) -> tuple[str, str]:
    code = normalize_code(stock)
    if code:
        return code, stock

    try:
        import akshare as ak

        for loader_name in ("stock_info_a_code_name", "stock_zh_a_spot_em"):
            try:
                spot = getattr(ak, loader_name)()
            except Exception:
                continue
            code_column = "代码" if "代码" in spot.columns else "code"
            name_column = "名称" if "名称" in spot.columns else "name"
            matches = spot[spot[name_column].astype(str).str.contains(stock, regex=False)]
            if len(matches) == 1:
                row = matches.iloc[0]
                return str(row[code_column]).zfill(6), str(row[name_column])
            if len(matches) > 1:
                compact = matches[[code_column, name_column]].head(10).to_dict("records")
                raise ValueError(f"股票名称存在多个匹配，请改用代码：{compact}")
    except ImportError as exc:
        raise RuntimeError("名称解析需要 akshare，请先安装 requirements.txt。") from exc

    raise ValueError(f"未能解析股票名称：{stock}。请改用 6 位股票代码。")


def load_market_data(symbol: str, args: argparse.Namespace) -> pd.DataFrame:
    start_date, end_date = (args.start_date, args.end_date) if args.start_date and args.end_date else date_range_by_years(args.years)
    try:
        return fetch_a_share_daily(symbol, start_date, end_date, args.adjust)
    except Exception:
        if not args.cache_csv:
            raise
        cached = pd.read_csv(resolve_path(args.cache_csv), encoding="utf-8-sig")
        if "date" in cached.columns:
            cached["date"] = pd.to_datetime(cached["date"])
        elif "日期" in cached.columns:
            cached = cached.rename(columns={"日期": "date"})
            cached["date"] = pd.to_datetime(cached["date"])
        return cached.sort_values("date").reset_index(drop=True)


def add_strategy_columns(data: pd.DataFrame) -> pd.DataFrame:
    df = add_common_indicators(data)
    df["trend_ok"] = (df["close"] > df["ma20"]) & (df["ma20"] > df["ma60"])
    df["momentum_ok"] = (df["macd_hist"] > 0) & (df["rsi14"].between(45, 70))
    df["volume_ok"] = df["volume_expansion_ratio"] >= 1
    df["buy_signal"] = df["trend_ok"] & df["momentum_ok"] & df["volume_ok"] & ~df["trend_ok"].shift(1).fillna(False)
    df["sell_signal"] = (df["close"] < df["ma20"]) | (df["ma20"] < df["ma60"]) | (df["rsi14"] > 80)
    return df


def run_signal_backtest(data: pd.DataFrame, config: QuantTradeConfig) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    df = add_strategy_columns(data)
    cash = config.initial_cash
    shares = 0
    entry_price = 0.0
    entry_date = None
    pending_buy = False
    pending_sell_reason: str | None = None
    trades: list[Trade] = []
    equity_rows = []

    for _, row in df.iterrows():
        date = row["date"]
        open_price = float(row["open"])
        close_price = float(row["close"])

        if shares > 0:
            stop_hit = close_price <= entry_price * (1 - config.stop_loss_pct / 100)
            take_hit = close_price >= entry_price * (1 + config.take_profit_pct / 100)
            if stop_hit:
                pending_sell_reason = f"触发{config.stop_loss_pct:.1f}%止损"
            elif take_hit:
                pending_sell_reason = f"触发{config.take_profit_pct:.1f}%止盈"

        if pending_sell_reason and shares > 0:
            cash, trade = close_position(
                trade_id=len(trades) + 1,
                cash=cash,
                shares=shares,
                entry_date=entry_date,
                entry_price=entry_price,
                exit_date=date,
                exit_price=open_price,
                commission_rate=config.commission_rate,
                reason=pending_sell_reason,
            )
            trades.append(trade)
            shares = 0
            entry_price = 0.0
            entry_date = None
            pending_sell_reason = None

        if pending_buy and shares == 0:
            budget = cash * config.position_ratio
            buyable = int((budget / (open_price * (1 + config.commission_rate))) // 100 * 100)
            if buyable > 0:
                buy_amount = buyable * open_price
                fee = buy_amount * config.commission_rate
                cash -= buy_amount + fee
                shares = buyable
                entry_price = open_price
                entry_date = date
            pending_buy = False

        equity_rows.append(
            {
                "date": date,
                "cash": cash,
                "shares": shares,
                "close": close_price,
                "market_value": shares * close_price,
                "equity": cash + shares * close_price,
            }
        )

        if shares == 0 and bool(row["buy_signal"]):
            pending_buy = True
        elif shares > 0 and bool(row["sell_signal"]):
            pending_sell_reason = "技术卖出信号"

    equity_curve = pd.DataFrame(equity_rows)
    if shares > 0:
        last = df.iloc[-1]
        cash, trade = close_position(
            trade_id=len(trades) + 1,
            cash=cash,
            shares=shares,
            entry_date=entry_date,
            entry_price=entry_price,
            exit_date=last["date"],
            exit_price=float(last["close"]),
            commission_rate=config.commission_rate,
            reason="回测结束强制平仓",
        )
        trades.append(trade)
        equity_curve.loc[equity_curve.index[-1], ["cash", "shares", "market_value", "equity"]] = [cash, 0, 0.0, cash]

    trades_df = pd.DataFrame([asdict(trade) for trade in trades])
    return summarize(df, equity_curve, trades_df, config.initial_cash), trades_df, equity_curve


def close_position(
    trade_id: int,
    cash: float,
    shares: int,
    entry_date,
    entry_price: float,
    exit_date,
    exit_price: float,
    commission_rate: float,
    reason: str,
) -> tuple[float, Trade]:
    buy_cost = shares * entry_price * (1 + commission_rate)
    sell_amount = shares * exit_price
    sell_fee = sell_amount * commission_rate
    new_cash = cash + sell_amount - sell_fee
    profit = sell_amount - sell_fee - buy_cost
    holding_days = int((exit_date - entry_date).days) if entry_date is not None else 0
    trade = Trade(
        trade_id=trade_id,
        buy_date=entry_date.strftime("%Y-%m-%d") if entry_date is not None else "",
        buy_price=round(entry_price, 4),
        shares=shares,
        sell_date=exit_date.strftime("%Y-%m-%d"),
        sell_price=round(exit_price, 4),
        profit=round(profit, 2),
        return_pct=round(profit / buy_cost * 100, 4) if buy_cost else 0.0,
        holding_days=holding_days,
        sell_reason=reason,
    )
    return new_cash, trade


def summarize(data: pd.DataFrame, equity_curve: pd.DataFrame, trades: pd.DataFrame, initial_cash: float) -> dict:
    final_equity = float(equity_curve["equity"].iloc[-1])
    total_return = final_equity / initial_cash - 1
    days = max((data["date"].iloc[-1] - data["date"].iloc[0]).days, 1)
    annual_return = (final_equity / initial_cash) ** (365 / days) - 1
    drawdown = equity_curve["equity"] / equity_curve["equity"].cummax() - 1
    daily_returns = equity_curve["equity"].pct_change().dropna()
    std = float(daily_returns.std(ddof=1)) if len(daily_returns) > 1 else 0.0
    sharpe = 0.0 if std == 0 or np.isnan(std) else float(daily_returns.mean() / std * np.sqrt(TRADING_DAYS_PER_YEAR))
    trade_count = len(trades)
    win_rate = 0.0 if trade_count == 0 else float((trades["profit"] > 0).mean())
    profit_factor = calculate_profit_factor(trades)
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
        "profit_factor": round(profit_factor, 4),
    }


def calculate_profit_factor(trades: pd.DataFrame) -> float:
    if trades.empty:
        return 0.0
    gross_profit = float(trades.loc[trades["profit"] > 0, "profit"].sum())
    gross_loss = abs(float(trades.loc[trades["profit"] < 0, "profit"].sum()))
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def build_signal_view(data: pd.DataFrame) -> dict:
    latest = data.iloc[-1]
    score = 0
    reasons = []
    checks = [
        (latest["close"] > latest["ma20"], "收盘价站上MA20"),
        (latest["ma20"] > latest["ma60"], "MA20高于MA60"),
        (45 <= latest["rsi14"] <= 70, "RSI处于趋势友好区间"),
        (latest["macd_hist"] > 0, "MACD柱为正"),
        (latest["volume_expansion_ratio"] >= 1, "成交量20日均量高于60日均量"),
    ]
    for passed, reason in checks:
        if bool(passed):
            score += 1
            reasons.append(reason)

    if score >= 4:
        rating = "偏积极"
    elif score >= 2:
        rating = "中性观察"
    else:
        rating = "偏谨慎"
    return {"rating": rating, "score": score, "reasons": reasons}


def plot_outputs(data: pd.DataFrame, equity_curve: pd.DataFrame, symbol: str, output_path: Path) -> Path:
    import matplotlib.pyplot as plt

    configure_matplotlib()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(3, 1, figsize=(13, 10), sharex=True)

    axes[0].plot(data["date"], data["close"], label="收盘价", linewidth=1.2)
    axes[0].plot(data["date"], data["ma20"], label="MA20", linewidth=1)
    axes[0].plot(data["date"], data["ma60"], label="MA60", linewidth=1)
    axes[0].set_title(f"{symbol} 量化交易分析")
    axes[0].grid(alpha=0.25)
    axes[0].legend(loc="upper left")

    axes[1].bar(data["date"], data["macd_hist"], label="MACD柱", color=np.where(data["macd_hist"] >= 0, "#d62728", "#2ca02c"))
    axes[1].plot(data["date"], data["rsi14"], label="RSI14", color="#9467bd", linewidth=1)
    axes[1].axhline(70, color="#999999", linestyle="--", linewidth=0.8)
    axes[1].axhline(30, color="#999999", linestyle="--", linewidth=0.8)
    axes[1].grid(alpha=0.25)
    axes[1].legend(loc="upper left")

    axes[2].plot(equity_curve["date"], equity_curve["equity"], label="策略权益", color="#1f77b4", linewidth=1.2)
    axes[2].grid(alpha=0.25)
    axes[2].legend(loc="upper left")

    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return output_path


def save_outputs(symbol: str, name: str, data: pd.DataFrame, summary: dict, trades: pd.DataFrame, equity_curve: pd.DataFrame, output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    enriched_path = output_dir / f"{symbol}_quant_data.csv"
    summary_path = output_dir / f"{symbol}_quant_summary.csv"
    trades_path = output_dir / f"{symbol}_quant_trades.csv"
    equity_path = output_dir / f"{symbol}_quant_equity.csv"
    chart_path = output_dir / f"{symbol}_quant_chart.png"
    report_path = output_dir / f"{symbol}_quant_report.md"

    data.to_csv(enriched_path, index=False, encoding="utf-8-sig")
    pd.DataFrame([{**{"symbol": symbol, "name": name}, **summary}]).to_csv(summary_path, index=False, encoding="utf-8-sig")
    trades.to_csv(trades_path, index=False, encoding="utf-8-sig")
    equity_curve.to_csv(equity_path, index=False, encoding="utf-8-sig")
    plot_outputs(data, equity_curve, symbol, chart_path)
    report_path.write_text(build_report(symbol, name, data, summary, trades, chart_path), encoding="utf-8")

    return {
        "data": enriched_path,
        "summary": summary_path,
        "trades": trades_path,
        "equity": equity_path,
        "chart": chart_path,
        "report": report_path,
    }


def build_report(symbol: str, name: str, data: pd.DataFrame, summary: dict, trades: pd.DataFrame, chart_path: Path) -> str:
    latest = data.iloc[-1]
    signal = build_signal_view(data)
    recent_trades = "暂无交易"
    if not trades.empty:
        recent_trades = trades.tail(10).to_markdown(index=False)

    return f"""# {name}（{symbol}）量化交易分析报告

生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

本报告基于历史行情、技术指标和规则化回测生成，仅用于量化研究，不构成投资建议。

![量化分析图]({chart_path.name})

## 当前信号

- 综合评级：**{signal["rating"]}**
- 信号得分：{signal["score"]}/5
- 触发因素：{", ".join(signal["reasons"]) if signal["reasons"] else "暂无明显正向触发因素"}
- 最新收盘价：{latest["close"]:.2f}
- MA20 / MA60：{latest["ma20"]:.2f} / {latest["ma60"]:.2f}
- RSI14：{latest["rsi14"]:.2f}
- MACD柱：{latest["macd_hist"]:.4f}
- 成交量扩张比：{latest["volume_expansion_ratio"]:.4f}

## 策略规则

- 买入：收盘价站上 MA20、MA20 高于 MA60、MACD 柱为正、RSI14 在 45 到 70 之间、20日均量高于60日均量。
- 卖出：跌破 MA20、MA20 低于 MA60、RSI14 高于 80、触发止损或止盈。
- 交易执行：信号出现后的下一个交易日开盘价成交。

## 回测摘要

| 指标 | 数值 |
|---|---:|
| 回测区间 | {summary["start_date"]} 至 {summary["end_date"]} |
| 初始资金 | {summary["initial_cash"]:.2f} |
| 最终权益 | {summary["final_equity"]:.2f} |
| 总收益率 | {summary["total_return_pct"]:.2f}% |
| 年化收益率 | {summary["annual_return_pct"]:.2f}% |
| 最大回撤 | {summary["max_drawdown_pct"]:.2f}% |
| 夏普比率 | {summary["sharpe_ratio"]:.4f} |
| 交易次数 | {summary["trade_count"]} |
| 胜率 | {summary["win_rate_pct"]:.2f}% |
| 盈亏比 | {summary["profit_factor"]:.4f} |

## 最近交易

{recent_trades}

## 风险提示

- 历史回测不能预测未来收益，参数可能过拟合。
- A股个股受公告、政策、行业景气、流动性和市场情绪影响较大。
- 若最大回撤较高或夏普较低，不应单独依赖该策略做交易决策。
"""


def main() -> None:
    args = build_parser().parse_args()
    symbol, name = resolve_stock(args.stock)
    config = QuantTradeConfig(
        initial_cash=args.initial_cash,
        position_ratio=args.position_ratio,
        commission_rate=args.commission_rate,
        stop_loss_pct=args.stop_loss_pct,
        take_profit_pct=args.take_profit_pct,
    )
    data = load_market_data(symbol, args)
    enriched = add_strategy_columns(data)
    summary, trades, equity_curve = run_signal_backtest(data, config)
    output_dir = resolve_path(args.output_dir) / symbol
    paths = save_outputs(symbol, name, enriched, summary, trades, equity_curve, output_dir)

    print(pd.DataFrame([{**{"symbol": symbol, "name": name}, **summary}]).to_string(index=False))
    print(f"报告：{paths['report']}")
    print(f"图表：{paths['chart']}")
    print(f"交易明细：{paths['trades']}")


if __name__ == "__main__":
    main()
