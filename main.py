from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A股量化分析项目入口。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    screen = subparsers.add_parser("screen", help="批量选股并导出 CSV。")
    screen.add_argument("--symbols", nargs="*", help="股票代码列表，例如 000001 600519。")
    screen.add_argument("--symbols-file", help="股票代码文件。")
    screen.add_argument("--output", default="reports/batch_stock_screen.csv")
    screen.add_argument("--filtered-output", default="reports/batch_stock_screen_filtered.csv")

    backtest = subparsers.add_parser("backtest", help="运行 MA20/MA60 策略回测。")
    backtest.add_argument("--symbol", required=True)
    backtest.add_argument("--start-date")
    backtest.add_argument("--end-date")
    backtest.add_argument("--output-dir", default="reports/backtest")

    fundamentals = subparsers.add_parser("fundamentals", help="根据财务数据生成基本面 Markdown 报告。")
    fundamentals.add_argument("--input", required=True, help="财务数据 CSV 或 Excel。")
    fundamentals.add_argument("--company", default="目标公司")
    fundamentals.add_argument("--output", default="reports/fundamental_report.md")
    return parser


def run_screen(args: argparse.Namespace, config: dict) -> None:
    import pandas as pd

    from src.data_loader import date_range_by_years, fetch_a_share_daily, load_stock_names, save_raw_data
    from src.indicators import add_common_indicators, period_return
    from src.utils import collect_symbols, resolve_path

    symbols = collect_symbols(args)
    names = load_stock_names()
    years = int(config["screener"]["years"])
    start_date, end_date = date_range_by_years(years)
    rows = []

    for index, symbol in enumerate(symbols, start=1):
        print(f"[{index}/{len(symbols)}] 处理 {symbol} ...")
        data = fetch_a_share_daily(symbol, start_date, end_date, config["data"]["adjust"])
        save_raw_data(data, symbol, config["paths"]["raw_data_dir"])
        df = add_common_indicators(data)
        latest = df.iloc[-1]
        passed = (
            latest["close"] > latest["ma60"]
            and latest["ma20"] > latest["ma60"]
            and config["screener"]["rsi_min"] <= latest["rsi14"] <= config["screener"]["rsi_max"]
            and latest["volume_ma20"] > latest["volume_ma60"]
        )
        rows.append(
            {
                "symbol": symbol,
                "name": names.get(symbol, symbol),
                "trade_date": latest["date"].strftime("%Y-%m-%d"),
                "close": latest["close"],
                "return_20d_pct": period_return(df, 20) * 100,
                "return_60d_pct": period_return(df, 60) * 100,
                "ma20": latest["ma20"],
                "ma60": latest["ma60"],
                "ma20_ma60_trend": "MA20>MA60" if latest["ma20"] > latest["ma60"] else "MA20<=MA60",
                "rsi14": latest["rsi14"],
                "volume_expansion_ratio": latest["volume_expansion_ratio"],
                "max_drawdown_pct": df["drawdown"].min() * 100,
                "passed_filter": passed,
            }
        )

    result = pd.DataFrame(rows).sort_values(["passed_filter", "return_20d_pct", "return_60d_pct"], ascending=[False, False, False])
    output = resolve_path(args.output)
    filtered_output = resolve_path(args.filtered_output)
    output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output, index=False, encoding="utf-8-sig")
    result[result["passed_filter"]].to_csv(filtered_output, index=False, encoding="utf-8-sig")
    print(result.to_string(index=False))
    print(f"完整排序表：{output}")
    print(f"筛选结果表：{filtered_output}")


def run_backtest_command(args: argparse.Namespace, config: dict) -> None:
    import pandas as pd

    from src.backtest import run_ma_cross_backtest, save_backtest_outputs
    from src.data_loader import date_range_by_years, fetch_a_share_daily
    from src.report import plot_equity_curve
    from src.utils import resolve_path

    years = int(config["data"]["default_years"])
    default_start, default_end = date_range_by_years(years)
    start_date = args.start_date or default_start
    end_date = args.end_date or default_end
    data = fetch_a_share_daily(args.symbol, start_date, end_date, config["data"]["adjust"])
    summary, trades, equity_curve = run_ma_cross_backtest(
        data=data,
        initial_cash=float(config["backtest"]["initial_cash"]),
        position_ratio=float(config["backtest"]["position_ratio"]),
        commission_rate=float(config["backtest"]["commission_rate"]),
    )
    output_dir = resolve_path(args.output_dir)
    paths = save_backtest_outputs(args.symbol, summary, trades, equity_curve, output_dir)
    chart_path = plot_equity_curve(equity_curve, args.symbol, output_dir / f"{args.symbol}_equity_curve.png")
    print(pd.DataFrame([{**{"symbol": args.symbol}, **summary}]).to_string(index=False))
    print(f"交易明细：{paths['trades']}")
    print(f"资金曲线图：{chart_path}")


def run_fundamentals(args: argparse.Namespace) -> None:
    from src.fundamentals import build_fundamental_report, load_financial_data
    from src.report import save_markdown
    from src.utils import resolve_path

    data = load_financial_data(resolve_path(args.input))
    content = build_fundamental_report(data, args.company)
    output = save_markdown(content, resolve_path(args.output))
    print(f"基本面报告：{output}")


def main() -> None:
    args = build_parser().parse_args()
    from src.utils import ensure_project_dirs, load_config

    config = load_config()
    ensure_project_dirs(config)
    if args.command == "screen":
        run_screen(args, config)
    elif args.command == "backtest":
        run_backtest_command(args, config)
    elif args.command == "fundamentals":
        run_fundamentals(args)


if __name__ == "__main__":
    main()
