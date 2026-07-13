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


LOOKBACK_YEARS = 2
RETURN_20D_WINDOW = 20
RETURN_60D_WINDOW = 60
RSI_WINDOW = 14
MA20_WINDOW = 20
MA60_WINDOW = 60


@dataclass(frozen=True)
class ScreenResult:
    symbol: str
    name: str
    trade_date: str
    close: float
    return_20d_pct: float
    return_60d_pct: float
    ma20: float
    ma60: float
    ma20_ma60_trend: str
    rsi14: float
    volume_ma20: float
    volume_ma60: float
    volume_expansion_ratio: float
    max_drawdown_pct: float
    passed_filter: bool
    reason: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="A股批量选股脚本，数据源为 akshare。")
    parser.add_argument("--symbols", nargs="*", help="股票代码列表，例如 --symbols 000001 600519 300750。也支持逗号分隔。")
    parser.add_argument("--symbols-file", help="股票代码文件，每行一个代码，也可用逗号分隔。")
    parser.add_argument("--output", default="batch_stock_screen.csv", help="完整排序表 CSV 输出路径。")
    parser.add_argument("--filtered-output", default="batch_stock_screen_filtered.csv", help="符合筛选条件的 CSV 输出路径。")
    parser.add_argument("--adjust", default="qfq", choices=["", "qfq", "hfq"], help="复权方式：空字符串、不复权；qfq 前复权；hfq 后复权。")
    return parser.parse_args()


def ensure_dependencies() -> None:
    if DEPENDENCY_IMPORT_ERROR is not None:
        raise RuntimeError("请先安装依赖：pip install akshare pandas numpy") from DEPENDENCY_IMPORT_ERROR


def get_date_range(years: int = LOOKBACK_YEARS) -> tuple[str, str]:
    end = datetime.now()
    start = end - timedelta(days=365 * years + 10)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def parse_symbols(symbols: list[str] | None, symbols_file: str | None) -> list[str]:
    values: list[str] = []
    if symbols:
        for item in symbols:
            values.extend(split_symbol_text(item))
    if symbols_file:
        text = Path(symbols_file).read_text(encoding="utf-8-sig")
        values.extend(split_symbol_text(text))

    cleaned = []
    seen = set()
    for value in values:
        symbol = value.strip()
        if not symbol:
            continue
        if symbol.isdigit():
            symbol = symbol.zfill(6)
        if symbol not in seen:
            cleaned.append(symbol)
            seen.add(symbol)
    if not cleaned:
        raise ValueError("请通过 --symbols 或 --symbols-file 输入至少一个股票代码。")
    return cleaned


def split_symbol_text(text: str) -> list[str]:
    normalized = text.replace("，", ",").replace("\n", ",").replace("\r", ",").replace("\t", ",")
    parts: list[str] = []
    for chunk in normalized.split(","):
        parts.extend(chunk.split())
    return parts


def load_stock_names() -> dict[str, str]:
    try:
        import akshare as ak

        spot = ak.stock_zh_a_spot_em()
        return dict(zip(spot["代码"].astype(str), spot["名称"].astype(str)))
    except Exception:
        return {}


def fetch_daily_data(symbol: str, adjust: str):
    import akshare as ak

    start_date, end_date = get_date_range()
    raw = ak.stock_zh_a_hist(
        symbol=symbol,
        period="daily",
        start_date=start_date,
        end_date=end_date,
        adjust=adjust,
    )
    if raw.empty:
        raise ValueError("未获取到行情数据")

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
            "涨跌额": "change",
            "换手率": "turnover",
        }
    )
    data["date"] = pd.to_datetime(data["date"])
    for column in ["open", "close", "high", "low", "volume", "amount", "pct_change", "change", "turnover"]:
        if column in data.columns:
            data[column] = pd.to_numeric(data[column], errors="coerce")
    return data.dropna(subset=["close", "volume"]).sort_values("date").reset_index(drop=True)


def add_indicators(data):
    df = data.copy()
    df["ma20"] = df["close"].rolling(MA20_WINDOW).mean()
    df["ma60"] = df["close"].rolling(MA60_WINDOW).mean()
    df["volume_ma20"] = df["volume"].rolling(MA20_WINDOW).mean()
    df["volume_ma60"] = df["volume"].rolling(MA60_WINDOW).mean()
    df["drawdown"] = df["close"] / df["close"].cummax() - 1

    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / RSI_WINDOW, min_periods=RSI_WINDOW, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / RSI_WINDOW, min_periods=RSI_WINDOW, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi14"] = 100 - (100 / (1 + rs))
    return df


def period_return(data, days: int) -> float:
    recent = data.tail(days + 1)
    if len(recent) < days + 1:
        return float("nan")
    return float(recent["close"].iloc[-1] / recent["close"].iloc[0] - 1)


def make_screen_result(symbol: str, name: str, data) -> ScreenResult:
    if len(data) < RETURN_60D_WINDOW + 1:
        raise ValueError(f"有效交易日不足 {RETURN_60D_WINDOW + 1} 天")

    df = add_indicators(data)
    latest = df.iloc[-1]
    return_20d = period_return(df, RETURN_20D_WINDOW)
    return_60d = period_return(df, RETURN_60D_WINDOW)
    volume_ratio = latest["volume_ma20"] / latest["volume_ma60"] if latest["volume_ma60"] else float("nan")
    max_drawdown = float(df["drawdown"].min())

    checks = {
        "收盘价高于MA60": latest["close"] > latest["ma60"],
        "MA20高于MA60": latest["ma20"] > latest["ma60"],
        "RSI在40到70之间": 40 <= latest["rsi14"] <= 70,
        "近20日均量高于近60日均量": latest["volume_ma20"] > latest["volume_ma60"],
    }
    passed_filter = all(checks.values())
    failed_reasons = [label for label, passed in checks.items() if not passed]
    trend = "MA20>MA60" if latest["ma20"] > latest["ma60"] else "MA20<=MA60"

    return ScreenResult(
        symbol=symbol,
        name=name,
        trade_date=latest["date"].strftime("%Y-%m-%d"),
        close=round(float(latest["close"]), 4),
        return_20d_pct=round(return_20d * 100, 4),
        return_60d_pct=round(return_60d * 100, 4),
        ma20=round(float(latest["ma20"]), 4),
        ma60=round(float(latest["ma60"]), 4),
        ma20_ma60_trend=trend,
        rsi14=round(float(latest["rsi14"]), 4),
        volume_ma20=round(float(latest["volume_ma20"]), 2),
        volume_ma60=round(float(latest["volume_ma60"]), 2),
        volume_expansion_ratio=round(float(volume_ratio), 4),
        max_drawdown_pct=round(max_drawdown * 100, 4),
        passed_filter=passed_filter,
        reason="通过" if passed_filter else "；".join(failed_reasons),
    )


def screen_stocks(symbols: list[str], adjust: str):
    names = load_stock_names()
    rows = []
    errors = []
    total = len(symbols)
    for index, symbol in enumerate(symbols, start=1):
        print(f"[{index}/{total}] 处理 {symbol} ...")
        try:
            data = fetch_daily_data(symbol=symbol, adjust=adjust)
            result = make_screen_result(symbol=symbol, name=names.get(symbol, symbol), data=data)
            rows.append(asdict(result))
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)})
            print(f"  跳过 {symbol}: {exc}")
    if not rows:
        raise RuntimeError("没有生成任何有效结果，请检查股票代码、网络或 akshare 数据源。")
    return pd.DataFrame(rows), pd.DataFrame(errors)


def sort_results(results):
    return results.sort_values(
        by=["passed_filter", "return_20d_pct", "return_60d_pct", "volume_expansion_ratio"],
        ascending=[False, False, False, False],
    ).reset_index(drop=True)


def save_outputs(results, errors, output: str, filtered_output: str) -> None:
    sorted_results = sort_results(results)
    output_path = Path(output)
    filtered_output_path = Path(filtered_output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    filtered_output_path.parent.mkdir(parents=True, exist_ok=True)

    sorted_results.to_csv(output_path, index=False, encoding="utf-8-sig")
    sorted_results.loc[sorted_results["passed_filter"]].to_csv(filtered_output_path, index=False, encoding="utf-8-sig")

    if not errors.empty:
        error_path = output_path.with_name(f"{output_path.stem}_errors.csv")
        errors.to_csv(error_path, index=False, encoding="utf-8-sig")
        print(f"异常记录：{error_path.resolve()}")

    print("\n排序表预览：")
    print(sorted_results.to_string(index=False))
    print(f"\n完整排序表：{output_path.resolve()}")
    print(f"筛选结果表：{filtered_output_path.resolve()}")
    print(f"符合条件数量：{int(sorted_results['passed_filter'].sum())} / {len(sorted_results)}")


def main() -> None:
    args = parse_args()
    ensure_dependencies()
    symbols = parse_symbols(args.symbols, args.symbols_file)
    results, errors = screen_stocks(symbols=symbols, adjust=args.adjust)
    save_outputs(results=results, errors=errors, output=args.output, filtered_output=args.filtered_output)


if __name__ == "__main__":
    main()
