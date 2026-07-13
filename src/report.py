from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


def configure_matplotlib() -> None:
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False


def plot_equity_curve(equity_curve: pd.DataFrame, symbol: str, output_path: Path) -> Path:
    configure_matplotlib()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(equity_curve["date"], equity_curve["equity"], label="资金曲线", color="#1f77b4", linewidth=1.5)
    ax.fill_between(equity_curve["date"], equity_curve["equity"], alpha=0.12, color="#1f77b4")
    ax.set_title(f"{symbol} 策略资金曲线")
    ax.set_xlabel("日期")
    ax.set_ylabel("总资产")
    ax.grid(alpha=0.25)
    ax.legend(loc="upper left")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return output_path


def save_markdown(content: str, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    return output_path
