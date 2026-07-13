from __future__ import annotations

import argparse
from dataclasses import dataclass
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


DEFAULT_YEARS = 3
TRADING_DAYS_FOR_RECENT_RETURN = 20


@dataclass(frozen=True)
class AnalysisResult:
    symbol: str
    name: str
    start_date: str
    end_date: str
    latest_close: float
    recent_20d_return: float
    max_drawdown: float
    chart_path: Path
    report_path: Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="A股技术分析脚本，数据源为 akshare。")
    parser.add_argument("--symbol", required=True, help="A股代码，例如 000001、600519、300750。")
    parser.add_argument("--output-dir", default="technical_analysis_output", help="图表和报告输出目录。")
    parser.add_argument("--adjust", default="qfq", choices=["", "qfq", "hfq"], help="复权方式：空字符串、不复权；qfq 前复权；hfq 后复权。")
    return parser.parse_args()


def get_date_range(years: int = DEFAULT_YEARS) -> tuple[str, str]:
    end = datetime.now()
    start = end - timedelta(days=365 * years + 10)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def fetch_a_share_daily(symbol: str, adjust: str) -> tuple[pd.DataFrame, str]:
    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError("请先安装依赖：pip install akshare pandas numpy matplotlib") from exc

    start_date, end_date = get_date_range()
    raw = ak.stock_zh_a_hist(
        symbol=symbol,
        period="daily",
        start_date=start_date,
        end_date=end_date,
        adjust=adjust,
    )
    if raw.empty:
        raise ValueError(f"未获取到 {symbol} 的行情数据，请检查股票代码或数据源状态。")

    data = raw.rename(
        columns={
            "日期": "date",
            "股票代码": "symbol",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
            "成交额": "amount",
            "振幅": "amplitude",
            "涨跌幅": "pct_change",
            "涨跌额": "change",
            "换手率": "turnover",
        }
    )
    data["date"] = pd.to_datetime(data["date"])
    numeric_columns = ["open", "close", "high", "low", "volume", "amount", "pct_change", "change", "turnover"]
    for column in numeric_columns:
        if column in data.columns:
            data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.dropna(subset=["open", "close", "high", "low", "volume"]).sort_values("date").reset_index(drop=True)

    name = get_stock_name(symbol)
    return data, name


def get_stock_name(symbol: str) -> str:
    try:
        import akshare as ak

        spot = ak.stock_zh_a_spot_em()
        match = spot.loc[spot["代码"].astype(str) == symbol]
        if not match.empty:
            return str(match.iloc[0]["名称"])
    except Exception:
        pass
    return symbol


def add_indicators(data: pd.DataFrame) -> pd.DataFrame:
    df = data.copy()
    for window in (5, 20, 60):
        df[f"ma{window}"] = df["close"].rolling(window=window).mean()

    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    df["macd_dif"] = ema12 - ema26
    df["macd_dea"] = df["macd_dif"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = (df["macd_dif"] - df["macd_dea"]) * 2

    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi14"] = 100 - (100 / (1 + rs))

    middle = df["close"].rolling(window=20).mean()
    std = df["close"].rolling(window=20).std()
    df["boll_mid"] = middle
    df["boll_upper"] = middle + 2 * std
    df["boll_lower"] = middle - 2 * std

    df["volume_ma5"] = df["volume"].rolling(window=5).mean()
    df["volume_ma20"] = df["volume"].rolling(window=20).mean()
    df["volume_change_pct"] = df["volume"].pct_change() * 100
    df["drawdown"] = df["close"] / df["close"].cummax() - 1
    return df


def calc_recent_return(data: pd.DataFrame, days: int = TRADING_DAYS_FOR_RECENT_RETURN) -> float:
    recent = data.tail(days + 1)
    if len(recent) < 2:
        return float("nan")
    return recent["close"].iloc[-1] / recent["close"].iloc[0] - 1


def calc_max_drawdown(data: pd.DataFrame) -> float:
    return float(data["drawdown"].min())


def format_pct(value: float) -> str:
    if value is None or np.isnan(value):
        return "N/A"
    return f"{value * 100:.2f}%"


def ensure_dependencies() -> None:
    if DEPENDENCY_IMPORT_ERROR is not None:
        raise RuntimeError("请先安装依赖：pip install akshare pandas numpy matplotlib") from DEPENDENCY_IMPORT_ERROR


def configure_matplotlib() -> None:
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False


def plot_candles(ax, data: pd.DataFrame) -> None:
    from matplotlib.patches import Rectangle

    x = np.arange(len(data))
    width = 0.62
    for idx, row in data.iterrows():
        color = "#d62728" if row["close"] >= row["open"] else "#2ca02c"
        ax.vlines(x[idx], row["low"], row["high"], color=color, linewidth=0.8)
        body_low = min(row["open"], row["close"])
        body_height = abs(row["close"] - row["open"])
        if body_height == 0:
            body_height = max(row["close"] * 0.001, 0.01)
        rect = Rectangle((x[idx] - width / 2, body_low), width, body_height, facecolor=color, edgecolor=color, linewidth=0.6)
        ax.add_patch(rect)
    ax.set_xlim(-1, len(data))


def set_date_ticks(ax, data: pd.DataFrame) -> None:
    x = np.arange(len(data))
    tick_count = min(8, len(data))
    if tick_count <= 1:
        return
    ticks = np.linspace(0, len(data) - 1, tick_count, dtype=int)
    labels = data["date"].iloc[ticks].dt.strftime("%Y-%m-%d")
    ax.set_xticks(x[ticks])
    ax.set_xticklabels(labels, rotation=30, ha="right")


def create_chart(data: pd.DataFrame, symbol: str, name: str, output_dir: Path) -> Path:
    import matplotlib.pyplot as plt

    configure_matplotlib()
    chart_data = data.tail(180).reset_index(drop=True)
    x = np.arange(len(chart_data))

    fig, axes = plt.subplots(
        4,
        1,
        figsize=(15, 11),
        sharex=True,
        gridspec_kw={"height_ratios": [4.5, 1.5, 1.8, 1.5]},
    )
    price_ax, volume_ax, macd_ax, rsi_ax = axes

    plot_candles(price_ax, chart_data)
    price_ax.plot(x, chart_data["ma5"], label="MA5", color="#f0ad00", linewidth=1.1)
    price_ax.plot(x, chart_data["ma20"], label="MA20", color="#1f77b4", linewidth=1.1)
    price_ax.plot(x, chart_data["ma60"], label="MA60", color="#9467bd", linewidth=1.1)
    price_ax.plot(x, chart_data["boll_upper"], label="BOLL上轨", color="#7f7f7f", linewidth=0.9, linestyle="--")
    price_ax.plot(x, chart_data["boll_mid"], label="BOLL中轨", color="#8c564b", linewidth=0.9, linestyle="--")
    price_ax.plot(x, chart_data["boll_lower"], label="BOLL下轨", color="#7f7f7f", linewidth=0.9, linestyle="--")
    price_ax.set_title(f"{name}({symbol}) 技术分析图 - 最近180个交易日")
    price_ax.set_ylabel("价格")
    price_ax.legend(loc="upper left", ncol=6, fontsize=9)
    price_ax.grid(alpha=0.2)

    volume_colors = np.where(chart_data["close"] >= chart_data["open"], "#d62728", "#2ca02c")
    volume_ax.bar(x, chart_data["volume"], color=volume_colors, width=0.7, alpha=0.75)
    volume_ax.plot(x, chart_data["volume_ma5"], color="#f0ad00", label="成交量MA5", linewidth=1.0)
    volume_ax.plot(x, chart_data["volume_ma20"], color="#1f77b4", label="成交量MA20", linewidth=1.0)
    volume_ax.set_ylabel("成交量")
    volume_ax.legend(loc="upper left", fontsize=9)
    volume_ax.grid(alpha=0.2)

    macd_colors = np.where(chart_data["macd_hist"] >= 0, "#d62728", "#2ca02c")
    macd_ax.bar(x, chart_data["macd_hist"], color=macd_colors, width=0.7, alpha=0.75, label="MACD柱")
    macd_ax.plot(x, chart_data["macd_dif"], color="#f0ad00", label="DIF", linewidth=1.0)
    macd_ax.plot(x, chart_data["macd_dea"], color="#1f77b4", label="DEA", linewidth=1.0)
    macd_ax.axhline(0, color="#444444", linewidth=0.8)
    macd_ax.set_ylabel("MACD")
    macd_ax.legend(loc="upper left", fontsize=9)
    macd_ax.grid(alpha=0.2)

    rsi_ax.plot(x, chart_data["rsi14"], color="#17becf", label="RSI14", linewidth=1.1)
    rsi_ax.axhline(70, color="#d62728", linewidth=0.8, linestyle="--")
    rsi_ax.axhline(30, color="#2ca02c", linewidth=0.8, linestyle="--")
    rsi_ax.set_ylim(0, 100)
    rsi_ax.set_ylabel("RSI")
    rsi_ax.legend(loc="upper left", fontsize=9)
    rsi_ax.grid(alpha=0.2)
    set_date_ticks(rsi_ax, chart_data)

    fig.tight_layout()
    chart_path = output_dir / f"{symbol}_technical_chart.png"
    fig.savefig(chart_path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return chart_path


def build_recent_20d_table(data: pd.DataFrame) -> str:
    rows = []
    recent = data.tail(TRADING_DAYS_FOR_RECENT_RETURN).copy()
    for _, row in recent.iterrows():
        rows.append(
            f"| {row['date'].strftime('%Y-%m-%d')} | {row['close']:.2f} | {row['pct_change']:.2f}% | "
            f"{row['volume']:.0f} | {row['volume_change_pct']:.2f}% |"
        )
    header = "| 日期 | 收盘价 | 当日涨跌幅 | 成交量 | 成交量较前日变化 |\n|---|---:|---:|---:|---:|"
    return "\n".join([header, *rows])


def build_report(data: pd.DataFrame, symbol: str, name: str, chart_path: Path, output_dir: Path) -> AnalysisResult:
    latest = data.iloc[-1]
    previous = data.iloc[-2] if len(data) >= 2 else latest
    recent_20d_return = calc_recent_return(data)
    max_drawdown = calc_max_drawdown(data)
    report_path = output_dir / f"{symbol}_technical_report.md"

    ma_relation = "多头排列" if latest["ma5"] > latest["ma20"] > latest["ma60"] else "非典型多头排列"
    macd_state = "MACD柱为正" if latest["macd_hist"] >= 0 else "MACD柱为负"
    rsi_state = "偏高" if latest["rsi14"] >= 70 else "偏低" if latest["rsi14"] <= 30 else "中性区间"
    volume_state = "放大" if latest["volume"] > latest["volume_ma20"] else "低于20日均量"

    report = f"""# {name}（{symbol}）A股技术分析报告

生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

数据源：akshare  
分析区间：{data['date'].iloc[0].strftime('%Y-%m-%d')} 至 {data['date'].iloc[-1].strftime('%Y-%m-%d')}  
说明：本报告基于历史行情和常见技术指标自动生成，不构成确定性买卖建议。

![技术分析图]({chart_path.name})

## 核心数据概览

| 项目 | 数值 |
|---|---:|
| 最新收盘价 | {latest['close']:.2f} |
| 最新日涨跌幅 | {latest['pct_change']:.2f}% |
| 最近20个交易日累计涨跌幅 | {format_pct(recent_20d_return)} |
| 区间最大回撤 | {format_pct(max_drawdown)} |
| 最新成交量 | {latest['volume']:.0f} |
| 成交量较前一日变化 | {latest['volume_change_pct']:.2f}% |
| MA5 / MA20 / MA60 | {latest['ma5']:.2f} / {latest['ma20']:.2f} / {latest['ma60']:.2f} |
| MACD DIF / DEA / 柱 | {latest['macd_dif']:.4f} / {latest['macd_dea']:.4f} / {latest['macd_hist']:.4f} |
| RSI14 | {latest['rsi14']:.2f} |

## 指标解读

- **K线图**：展示每个交易日的开盘、收盘、最高和最低价格，有助于观察价格波动结构和阶段性趋势。
- **MA5、MA20、MA60**：分别代表短期、中期和较长期均线。当前均线状态为“{ma_relation}”，可用于观察趋势强弱与价格相对均线的位置。
- **MACD**：由 DIF、DEA 和柱状图组成，常用于观察趋势动能变化。当前状态为“{macd_state}”，说明短中期动能正在发生相应变化，但需要结合价格和成交量确认。
- **RSI14**：衡量近14个交易日上涨和下跌力量的相对强弱。当前 RSI 位于“{rsi_state}”，通常 70 以上视为偏热，30 以下视为偏弱。
- **布林带**：中轨通常为20日均线，上下轨反映波动范围。价格靠近上轨时说明短期较强或波动扩张，靠近下轨时说明短期承压或波动下移。
- **成交量变化**：当前成交量“{volume_state}”。成交量放大通常表示交易活跃度提升，但方向需要结合价格涨跌判断。
- **最大回撤**：区间最大回撤为 {format_pct(max_drawdown)}，表示从阶段高点到后续低点的最大跌幅，是衡量历史下行风险的重要指标。

## 最近20个交易日涨跌幅

最近20个交易日累计涨跌幅：**{format_pct(recent_20d_return)}**

{build_recent_20d_table(data)}

## 风险提示

- 技术指标基于历史数据计算，不能预测未来价格，也不能替代基本面、估值、行业景气度和宏观环境分析。
- A股个股可能受政策、公告、业绩、流动性、市场情绪和突发事件影响，历史规律可能失效。
- 单一指标容易产生误判，应结合多周期、多指标和风险承受能力综合评估。
- 本报告仅用于量化研究和技术分析学习，不构成投资建议或收益承诺。
"""
    report_path.write_text(report, encoding="utf-8")
    return AnalysisResult(
        symbol=symbol,
        name=name,
        start_date=data["date"].iloc[0].strftime("%Y-%m-%d"),
        end_date=data["date"].iloc[-1].strftime("%Y-%m-%d"),
        latest_close=float(latest["close"]),
        recent_20d_return=float(recent_20d_return),
        max_drawdown=max_drawdown,
        chart_path=chart_path,
        report_path=report_path,
    )


def run_analysis(symbol: str, output_dir: Path, adjust: str) -> AnalysisResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    data, name = fetch_a_share_daily(symbol=symbol, adjust=adjust)
    data = add_indicators(data)
    chart_path = create_chart(data=data, symbol=symbol, name=name, output_dir=output_dir)
    return build_report(data=data, symbol=symbol, name=name, chart_path=chart_path, output_dir=output_dir)


def main() -> None:
    args = parse_args()
    ensure_dependencies()
    result = run_analysis(symbol=args.symbol, output_dir=Path(args.output_dir), adjust=args.adjust)
    print(f"完成：{result.name}({result.symbol})")
    print(f"分析区间：{result.start_date} 至 {result.end_date}")
    print(f"最新收盘价：{result.latest_close:.2f}")
    print(f"最近20个交易日累计涨跌幅：{format_pct(result.recent_20d_return)}")
    print(f"区间最大回撤：{format_pct(result.max_drawdown)}")
    print(f"图表：{result.chart_path.resolve()}")
    print(f"报告：{result.report_path.resolve()}")


if __name__ == "__main__":
    main()
