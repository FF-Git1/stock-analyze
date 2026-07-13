from __future__ import annotations

import numpy as np
import pandas as pd


def add_moving_averages(data: pd.DataFrame, windows: tuple[int, ...] = (5, 20, 60)) -> pd.DataFrame:
    df = data.copy()
    for window in windows:
        df[f"ma{window}"] = df["close"].rolling(window).mean()
    return df


def add_rsi(data: pd.DataFrame, window: int = 14) -> pd.DataFrame:
    df = data.copy()
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df[f"rsi{window}"] = 100 - (100 / (1 + rs))
    return df


def add_macd(data: pd.DataFrame) -> pd.DataFrame:
    df = data.copy()
    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    df["macd_dif"] = ema12 - ema26
    df["macd_dea"] = df["macd_dif"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = (df["macd_dif"] - df["macd_dea"]) * 2
    return df


def add_bollinger(data: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    df = data.copy()
    middle = df["close"].rolling(window).mean()
    std = df["close"].rolling(window).std()
    df["boll_mid"] = middle
    df["boll_upper"] = middle + 2 * std
    df["boll_lower"] = middle - 2 * std
    return df


def add_volume_metrics(data: pd.DataFrame) -> pd.DataFrame:
    df = data.copy()
    df["volume_ma5"] = df["volume"].rolling(5).mean()
    df["volume_ma20"] = df["volume"].rolling(20).mean()
    df["volume_ma60"] = df["volume"].rolling(60).mean()
    df["volume_expansion_ratio"] = df["volume_ma20"] / df["volume_ma60"]
    return df


def add_drawdown(data: pd.DataFrame) -> pd.DataFrame:
    df = data.copy()
    df["drawdown"] = df["close"] / df["close"].cummax() - 1
    return df


def add_common_indicators(data: pd.DataFrame) -> pd.DataFrame:
    df = add_moving_averages(data)
    df = add_macd(df)
    df = add_rsi(df)
    df = add_bollinger(df)
    df = add_volume_metrics(df)
    return add_drawdown(df)


def period_return(data: pd.DataFrame, days: int) -> float:
    recent = data.tail(days + 1)
    if len(recent) < days + 1:
        return float("nan")
    return float(recent["close"].iloc[-1] / recent["close"].iloc[0] - 1)
