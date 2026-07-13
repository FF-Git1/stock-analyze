from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from .utils import resolve_path


def date_range_by_years(years: int) -> tuple[str, str]:
    end = datetime.now()
    start = end - timedelta(days=365 * years + 10)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def fetch_a_share_daily(symbol: str, start_date: str, end_date: str, adjust: str = "qfq") -> pd.DataFrame:
    import akshare as ak

    raw = ak.stock_zh_a_hist(
        symbol=symbol,
        period="daily",
        start_date=start_date,
        end_date=end_date,
        adjust=adjust,
    )
    if raw.empty:
        raise ValueError(f"未获取到 {symbol} 的行情数据。")
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
    for column in ["open", "close", "high", "low", "volume", "amount", "pct_change", "change", "turnover"]:
        if column in data.columns:
            data[column] = pd.to_numeric(data[column], errors="coerce")
    return data.dropna(subset=["open", "close", "high", "low", "volume"]).sort_values("date").reset_index(drop=True)


def save_raw_data(data: pd.DataFrame, symbol: str, raw_data_dir: str | Path) -> Path:
    output = resolve_path(raw_data_dir) / f"{symbol}_daily.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    data.to_csv(output, index=False, encoding="utf-8-sig")
    return output


def load_stock_names() -> dict[str, str]:
    try:
        import akshare as ak

        spot = ak.stock_zh_a_spot_em()
        return dict(zip(spot["代码"].astype(str), spot["名称"].astype(str)))
    except Exception:
        return {}
