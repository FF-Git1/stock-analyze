from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_config(path: str | Path = "config.yaml") -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在：{config_path}")
    with config_path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def ensure_project_dirs(config: dict[str, Any]) -> None:
    for key in ("raw_data_dir", "processed_data_dir", "reports_dir", "notebooks_dir"):
        value = config.get("paths", {}).get(key)
        if value:
            resolve_path(value).mkdir(parents=True, exist_ok=True)


def resolve_path(path: str | Path) -> Path:
    target = Path(path)
    if target.is_absolute():
        return target
    return PROJECT_ROOT / target


def split_symbols(text: str) -> list[str]:
    normalized = text.replace("，", ",").replace("\n", ",").replace("\r", ",").replace("\t", ",")
    values: list[str] = []
    for chunk in normalized.split(","):
        values.extend(chunk.split())
    return [normalize_symbol(value) for value in values if value.strip()]


def normalize_symbol(symbol: str) -> str:
    value = symbol.strip()
    return value.zfill(6) if value.isdigit() else value


def collect_symbols(args: argparse.Namespace) -> list[str]:
    symbols: list[str] = []
    if getattr(args, "symbols", None):
        for item in args.symbols:
            symbols.extend(split_symbols(item))
    if getattr(args, "symbols_file", None):
        symbols.extend(split_symbols(resolve_path(args.symbols_file).read_text(encoding="utf-8-sig")))

    cleaned: list[str] = []
    seen = set()
    for symbol in symbols:
        if symbol and symbol not in seen:
            cleaned.append(symbol)
            seen.add(symbol)
    if not cleaned:
        raise ValueError("请通过 --symbols 或 --symbols-file 输入至少一个股票代码。")
    return cleaned


def format_pct(value: float) -> str:
    return "N/A" if value != value else f"{value:.2f}%"
