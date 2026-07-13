from __future__ import annotations

from pathlib import Path

import pandas as pd


def load_financial_data(path: str | Path) -> pd.DataFrame:
    source = Path(path)
    if source.suffix.lower() in {".xlsx", ".xls"}:
        data = pd.read_excel(source)
    else:
        data = pd.read_csv(source, encoding="utf-8-sig")
    if "年份" not in data.columns:
        raise ValueError("财务数据需要包含“年份”列。")
    return data.sort_values("年份").reset_index(drop=True)


def enrich_financial_metrics(data: pd.DataFrame) -> pd.DataFrame:
    df = data.copy()
    if "营业收入" in df.columns:
        df["营收同比"] = df["营业收入"].pct_change() * 100
    if "净利润" in df.columns:
        df["净利润同比"] = df["净利润"].pct_change() * 100
    if {"总资产", "总负债"}.issubset(df.columns):
        df["资产负债率"] = df["总负债"] / df["总资产"] * 100
    if {"经营现金流净额", "净利润"}.issubset(df.columns):
        df["经营现金流/净利润"] = df["经营现金流净额"] / df["净利润"]
    return df


def build_fundamental_report(data: pd.DataFrame, company_name: str = "目标公司") -> str:
    df = enrich_financial_metrics(data)
    table = df.to_markdown(index=False)
    risks = [
        "收入或利润增长放缓可能影响市场对公司成长性的预期。",
        "毛利率、净利率或 ROE 若持续下行，可能意味着竞争加剧、成本压力或资产效率下降。",
        "资产负债率上升会提高财务费用和偿债压力。",
        "经营现金流长期弱于净利润时，需要关注利润质量和应收、存货变化。",
        "估值指标需要与行业、历史区间和盈利周期结合，单独使用容易误判。",
    ]
    questions = [
        "收入增长主要来自销量、价格、并表还是一次性因素？",
        "净利润变化是否受非经常性损益、减值或投资收益影响？",
        "现金流弱化是否来自账期延长、库存增加或资本开支提升？",
        "ROE 变化主要由净利率、周转率还是杠杆驱动？",
        "当前 PE/PB/PS 相比行业和公司历史分位处于什么水平？",
    ]
    return f"""# {company_name} 基本面分析报告

本报告基于用户提供的近 5 年财务数据生成，仅用于研究分析，不构成确定性买卖建议。

## 核心指标

{table}

## 分析框架

- 营收增长：观察收入同比变化及连续性，判断业务规模扩张是否稳定。
- 净利润增长：结合收入变化和利润率，区分增长来自主业改善还是费用、投资收益等因素。
- 毛利率和净利率：用于观察盈利能力趋势，连续下行需要谨慎解释。
- ROE：反映股东权益回报，需结合净利率、资产周转和财务杠杆拆解。
- 资产负债率：衡量财务杠杆和偿债压力。
- 经营现金流质量：经营现金流与净利润匹配度越高，通常利润质量越扎实。
- 估值指标：PE、PB、PS 应结合行业可比公司、历史分位和盈利周期观察。

## 主要风险

{chr(10).join(f"- {item}" for item in risks)}

## 需要进一步验证的问题

{chr(10).join(f"- {item}" for item in questions)}

## 谨慎结论

当前结论应以趋势观察和风险识别为主。若核心盈利指标、现金流质量和资产负债结构同时改善，可作为进一步研究线索；若增长质量依赖一次性因素或现金流持续偏弱，则需要提高风险权重。
"""
