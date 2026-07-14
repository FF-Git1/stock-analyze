# stock-analyze

A 股量化分析与研究辅助项目。项目基于 `akshare` 拉取 A 股行情数据，提供批量选股、技术分析、MA20/MA60 策略回测、基本面报告和量化交易分析等功能，并将结果输出到 `reports/` 目录。

> 免责声明：本项目输出仅用于学习、研究和策略复盘，不构成任何投资建议。行情接口、财务数据和新闻数据可能存在延迟、缺失或口径差异，实盘决策请以交易所公告、公司公告和权威数据源为准。

## 功能概览

- **批量选股**：根据均线、RSI、成交量放大等条件筛选股票。
- **技术分析**：生成单只 A 股的技术指标报告和图表。
- **MA20/MA60 回测**：回测均线金叉/死叉策略，输出交易明细、资金曲线和汇总结果。
- **量化交易分析**：结合趋势、动量、波动、止盈止损和回测结果生成 Markdown 报告。
- **基本面报告**：基于用户提供的财务 CSV/Excel 生成基本面分析框架报告。
- **资讯图片简报**：可配合本地 Codex skill 生成跨圈科技/股票热点与股票池资讯图片。

## 目录结构

```text
.
├── main.py                         # 项目统一入口：screen / backtest / fundamentals
├── batch_stock_screener.py          # 独立批量选股脚本
├── a_share_technical_analysis.py    # 单股技术分析脚本
├── ma_cross_backtest.py             # MA20/MA60 策略回测脚本
├── quant_trade_analysis.py          # 量化交易分析脚本
├── config.yaml                      # 路径、数据、回测和筛选配置
├── requirements.txt                 # Python 依赖
├── src/                             # 核心数据、指标、回测、报告模块
├── data/                            # 本地数据目录
├── notebooks/                       # Notebook 实验目录
└── reports/                         # 报告、图表和回测结果输出目录
```

## 环境安装

建议使用 Python 3.10+。

```powershell
cd D:\stock-analyze
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

依赖包括：

- `akshare`
- `pandas`
- `numpy`
- `matplotlib`
- `PyYAML`
- `tabulate`

## 快速开始

### 1. 批量选股

通过统一入口运行：

```powershell
python main.py screen --symbols 000001 600519 300750
```

或使用独立脚本：

```powershell
python batch_stock_screener.py --symbols 000001 600519 300750
```

也可以从文件读取股票代码：

```powershell
python main.py screen --symbols-file data/symbols.txt
```

输出：

- `reports/batch_stock_screen.csv`
- `reports/batch_stock_screen_filtered.csv`

### 2. 单股技术分析

```powershell
python a_share_technical_analysis.py --symbol 000021 --adjust qfq
```

输出示例：

- `reports/000021_technical/000021_technical_report.md`
- `reports/000021_technical/000021_technical_chart.png`

### 3. MA20/MA60 策略回测

```powershell
python main.py backtest --symbol 000021 --start-date 20210101 --end-date 20260713
```

或使用独立脚本：

```powershell
python ma_cross_backtest.py --symbol 000021 --start-date 20210101 --end-date 20260713 --initial-cash 100000
```

输出通常包括：

- 交易明细 CSV
- 策略汇总 CSV
- 资金曲线 CSV
- 资金曲线图 PNG

### 4. 量化交易分析

支持股票代码或股票名称：

```powershell
python quant_trade_analysis.py 000021
python quant_trade_analysis.py 深科技
```

### 5. Windows 隐蔽性股票浮窗

本项目提供一个轻量本地浮窗，支持置顶、透明度调节、拖拽、开始/暂停实时获取、动态添加/删除股票，以及关闭浮窗。

```powershell
python desktop_stock_float.py
```

默认股票池：

- `000021` 深科技
- `000725` 京东方A
- `000938` 紫光股份
- `603099` 长白山
- `002745` 木林森
- `001309` 德明利
- `603986` 兆易创新
- `600667` 太极实业

浮窗说明：

- 点击 `▶` 开始每秒获取一次行情，再点一次暂停。
- 输入 6 位股票代码后按回车或点 `+` 添加。
- 点 `-` 删除最后一只股票，双击某只股票行可删除该股票。
- 股票较多时窗口大小不变，在股票列表区域滚动鼠标滚轮上下查看。
- 拖拽窗口任意位置可以移动，右键可打开菜单。
- 下方滑条调节透明度，`×` 或 `Esc` 可关闭浮窗。

行情来自公开网页接口，可能存在延迟、缺失或限流，不适合作为实盘下单依据。

常用参数：

```powershell
python quant_trade_analysis.py 000021 `
  --years 3 `
  --initial-cash 100000 `
  --position-ratio 0.9 `
  --stop-loss-pct 0.08 `
  --take-profit-pct 0.25
```

输出示例：

- `reports/quant_trade/000021/000021_quant_report.md`
- `reports/quant_trade/000021/000021_quant_chart.png`
- `reports/quant_trade/000021/000021_quant_trades.csv`
- `reports/quant_trade/000021/000021_quant_summary.csv`
- `reports/quant_trade/000021/000021_quant_equity.csv`
- `reports/quant_trade/000021/000021_quant_data.csv`

如果行情接口失败，可以使用本地缓存 CSV：

```powershell
python quant_trade_analysis.py 000021 --cache-csv reports/quant_trade/000021/000021_quant_data.csv
```

### 5. 基本面报告

准备一个 CSV 或 Excel 文件，至少包含 `年份` 列。可选字段包括：

- `营业收入`
- `净利润`
- `总资产`
- `总负债`
- `经营现金流净额`

运行：

```powershell
python main.py fundamentals --input data/fundamentals.csv --company 目标公司 --output reports/fundamental_report.md
```

## 配置说明

`config.yaml` 控制默认目录、数据复权方式、回测参数和筛选条件：

```yaml
paths:
  raw_data_dir: data/raw
  processed_data_dir: data/processed
  reports_dir: reports
  notebooks_dir: notebooks

data:
  adjust: qfq
  default_years: 5

backtest:
  initial_cash: 100000
  position_ratio: 0.9
  commission_rate: 0.001

screener:
  years: 2
  rsi_min: 40
  rsi_max: 70
```

复权参数说明：

- 空字符串：不复权
- `qfq`：前复权
- `hfq`：后复权

## 数据来源

行情与部分股票信息主要通过 `akshare` 获取。不同数据源可能存在字段变化、接口限流或临时不可用的情况。若遇到网络或接口错误，可以稍后重试，或使用脚本支持的本地 CSV 缓存参数。

## 输出文件

所有分析结果默认写入 `reports/`：

- 技术分析：`reports/<symbol>_technical/`
- 均线回测：`reports/<symbol>_backtest/` 或自定义目录
- 量化交易：`reports/quant_trade/<symbol>/`
- 批量筛选：`reports/batch_stock_screen*.csv`
- 资讯图片：`reports/news_briefing/`

## 常见问题

### akshare 拉取失败怎么办？

可能是网络、接口限流或数据源临时异常。建议：

1. 稍后重试。
2. 检查股票代码是否为 6 位 A 股代码。
3. 对 `quant_trade_analysis.py` 使用 `--cache-csv` 指定本地行情 CSV。

### 股票名称能否直接输入？

`quant_trade_analysis.py` 支持部分股票名称解析，例如：

```powershell
python quant_trade_analysis.py 深科技
```

其他脚本通常建议直接使用 6 位股票代码。

### 回测结果能否直接用于实盘？

不建议。当前回测主要用于研究策略行为，未完整考虑滑点、涨跌停无法成交、流动性冲击、停牌、分红配股、税费细节和真实撮合约束。

## 开发建议

- 新增策略逻辑优先放入 `src/`，脚本层只负责参数解析、调用和输出。
- 新增报告输出时尽量写入 `reports/<功能>/<股票代码>/`，避免覆盖已有结果。
- 修改筛选条件时同步更新 `config.yaml` 和 README 示例，保持使用口径一致。
