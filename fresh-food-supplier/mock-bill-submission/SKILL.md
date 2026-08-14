---
name: mock-bill-submission
description: 在用户演示"接单→采购→配送"闭环时，触发。比如“商户a 买 100 斤土豆“
version: 1.0.0
---

---

本系统是给内部对齐会看的"能跑通全链路"演示，不是生产系统。架构：录入前端（接单员经对话智能体口语下单 → 大语言模型解析成结构化参数）→ 后端引擎（`scripts/` 下确定性 Python 按层处理、逐层写 `data/`）→ 编排层（本 skill，只调度、不计算）。业务规则封装在 `scripts/` 的 Python 与 `data/` 的配置表（映射/价格/客户）里。

## 核心约定（全系统必须遵守）

1. **弹窗确认商户是编排层（对话智能体）的职责，不属任何脚本。** 智能体解析口语后、调 L1 `add` 落库前，必须弹窗展示「客户/商品/数量(单位)/备注」并让用户**明确指定商户**，未确认不得写 `data/orders_raw.csv`。
2. **自然语言与文件输入→结构化由对话智能体（大语言模型）完成。** 脚本只接"已确认的结构化参数"做确定性规则。用户给 `csv / image / word / excel` 等文件时，智能体须自行读取并解析成订单参数，再走步骤 0 的完整性校验；脚本不读文件。
3. **共享 CSV 逐层流转。** 所有层读写同一份 `data/`：`orders_raw.csv`(L1) → `orders_matched.csv`(L2) → `orders_risk.csv`(L3) → `orders_forecast.csv` + `dispatch.csv`(L4)。改一处即影响下游全部。
4. **数据编码与目录定位。** 数据文件为 UTF-8（带 BOM）；脚本按 `__file__` 向上两级定位 `data/`，也可用环境变量 `ORDER_DATA` 覆盖数据目录。

## 分层与模块映射

| 层 | 脚本 | 输入 | 输出 | 实际规则 / 讲点 |
|---|---|---|---|---|
| L1 录入 | `scripts/l1_intake.py` | 结构化参数（已确认商户） | `data/orders_raw.csv` | 强校验：客户须存在于 customers.csv、商品非空、数量>0、单位∈{斤,kg,千克,公斤,克,吨,份,个,箱,袋}；失败打印🔴无法接单并 `exit(2)`，不落库 |
| L2 编码定价 | `scripts/l2_match_price.py` | `orders_raw.csv` | `orders_matched.csv` | 别名(土豆/马铃薯/洋芋)→一品一码；单位按换算表归一到斤；大B用协议价、普通用市价；未知商品 sku_id 留空交 L3 |
| L3 异常确认 | `scripts/l3_risk_confirm.py` | `orders_matched.csv` | `orders_risk.csv` + 三态返回 | 异常三类：未知SKU / 超量(>50斤) / 超截单(接单时间晚于客户 cutoff)；常规单 auto_confirmed，异常单 needs_human 并附 AI 建议 |
| L4 汇总 | `scripts/l4_forecast_dispatch.py` | `orders_risk.csv` | `orders_forecast.csv` + `dispatch.csv` | 采购建议单：按SKU汇总需求×安全系数1.10、取整到10；分车/分仓单：按客户聚合，大B→中央仓A、普通→前置仓B |

## 运行环境约定

- 脚本为纯标准库 Python（`csv / argparse / os / sys / datetime / math`），无需第三方包。
- 脚本仅依赖 Python 标准库，**任意 Python 3（建议 3.9+）均可运行**，无需指定特定解释器。下文命令以 `python3` 代指你机器上的 Python 3 解释器（Windows 可用 `python` 或 `py`），并假设 cwd 为 skill 根目录（即含 `scripts/`、`data/` 的目录）。
- 数据目录默认 `<skill>/data`，可用环境变量 `ORDER_DATA` 覆盖（脚本已读取）。

## 演示工作流

复制此清单，每完成一项请勾选：

```
Task Progress:
- [ ] 步骤 0：核验输入与提取数据（录入信息完整性校验；csv/image/word/excel 由 LLM 自行读取解析）
- [ ] 步骤 1：准备并录入订单（运行 l1_intake.py）
- [ ] 步骤 2：编码与定价（运行 l2_match_price.py）
- [ ] 步骤 3：风险核查与确认（运行 l3_risk_confirm.py）
- [ ] 步骤 4：预测与分单（运行 l4_forecast_dispatch.py）
- [ ] 步骤 5：核验输出（查看三态与产物）
```

**步骤 0：核验输入与提取数据（编排层前置闸门）**

这是进入任何脚本之前的必做项，由**对话智能体（大语言模型）**完成，不调用脚本。

1. **完整性校验**：从用户消息里提取录入所需字段——`客户`、`商品`、`数量`、`单位`（备注可选）。四必填项缺任何一项 → **先追问用户补齐**，不得进步骤 1。
2. **文件类输入由 LLM 读取**：若用户提供的是 `csv / image / word / excel` 等文件，智能体须**自行读取并解析**出上述结构化订单参数（逐行/逐单抽取），再走完整性校验；脚本只吃"已确认的结构化参数"，不懂文件。
3. **校验通过 + 提取完成** → 进入**步骤 1** 调 L1 `add` 落库（落库前仍需弹窗确认商户，见核心约定 1）。

> 这一步是"入口前的入口"：把口语、图片、表格等非结构化输入，统一收口成 L1 能接的结构化参数。

**步骤 1：准备并录入订单（L1）**

运行：`python3 scripts/l1_intake.py show`
运行：`python3 scripts/l1_intake.py add --customer C003 --item 土豆 --qty 10 --unit 斤 --remark "加急"`

先运行 `show` 查看当前 `orders_raw.csv`。要新增订单，调用 `add` 并传入 `--customer --item --qty --unit`（必填）与 `--remark`（可选）。
**调用 `add` 之前，编排层（对话智能体）必须弹窗展示「客户/商品/数量(单位)/备注」并让用户明确指定商户。** 未知商户 → 先在 `data/customers.csv` 中开户。校验失败（未知客户 / 数量非正 / 单位非法）会打印 `🔴 无法接单` 并以退出码 2 结束，不写库。

**步骤 2：编码与定价（L2）**

运行：`python3 scripts/l2_match_price.py`

读取 `orders_raw.csv`，把别名映射为标准 SKU、单位换算到标准单位，并按客户类型定价（大B 协议价 / 普通 市价），写入 `orders_matched.csv`。演示时在此暂停，展示"洋芋→SHU001、单位已换算、价格已按客户带出"。

**步骤 3：风险核查与确认（L3）**

运行：`python3 scripts/l3_risk_confirm.py`

读取 `orders_matched.csv`，标记超量 / 超截单 / 未知商品，常规单自动确认，异常单转人工并附 AI 处理建议。打印**三态返回**并写入 `orders_risk.csv`：
- ✅ 全部接单 — 全部有效单均自动确认
- 🟡 部分接单 — 部分自动确认、部分转人工
- 🔴 无法接单 — 录入被拒，或全部转人工（0 单确认）

**步骤 4：预测与分单（L4）**

运行：`python3 scripts/l4_forecast_dispatch.py`

读取 `orders_risk.csv`，按 SKU 聚合次日需求并生成**采购建议单**（`orders_forecast.csv`），按客户聚合并路由仓库生成**分车/分仓单**（`dispatch.csv`）。这让"接单→采购→配送"的闭环在演示中可见。

**步骤 5：核验输出**

运行：`python3 scripts/l3_risk_confirm.py`（复核三态）
查看 `data/orders_risk.csv`、`data/orders_forecast.csv`、`data/dispatch.csv`。

确认结果符合预期：SKU/价格正确、异常已转人工、L4 两份产物齐备。若状态为 **🔴 无法接单**，则修正输入并返回**步骤 1**。

随时重置演示态：`python3 scripts/reset.py`（清空 L2/L3/L4 产物，保留 `orders_raw` 与配置）。

---

## 标准演示脚本（逐层点开的讲法）

0. **入口前先核验输入**：口语/文件都由 LLM 收口成结构化订单参数（文件类自行读取），四必填（客户/商品/数量/单位）缺失先追问补齐。
1. `l1_intake.py show` 看 L1 原始单（已含弹窗确认的商户）。
2. 跑 `l2_match_price.py` → 暂停，展示"洋芋→SHU001、单位已归一到斤、价格按客户类型带出（大B协议价 / 普通市价）"。
3. 跑 `l3_risk_confirm.py` → 展示三态返回：自动确认单 / 转人工单（超量、未知商品、超截单可各举一个），并宣读 AI 建议。
4. 跑 `l4_forecast_dispatch.py` → 展示两份产物：**采购建议单**（含安全系数与取整）与**分车/分仓单**（大B→中央仓A、普通→前置仓B）。
5. **叙事落点**：L1–L3 把录入、编码、风控做成确定性自动化，异常才转人工；L4 让"接单→采购→配送"的闭环在演示中可见。

## 边界

- **演示样板，非生产系统**：规则/阈值（超量50斤、安全系数1.10、截单时间、路由仓）均为硬编码简化；生产需接 OMS/TMS/实时市价并加置信度复核。
- **单位换算**：白名单含 斤/kg/千克/公斤/克/吨/份/个/箱/袋，统一折算到斤（1吨=2000斤、1公斤=2斤）；需新单位在 `UNIT_TO_JIN` 增项即可。
- **L4 简化假设**：转人工单也计入采购建议与分单（视为仍将履约），真实场景应先按最终确认结果再汇总。
- **改数据看不同走向**：改 `orders_raw.csv` 造异常组合；改 `sku_mapping.csv`/`prices.csv`/`customers.csv` 演示规则扩展；`reset.py` 清空 L2/L3/L4 产物后重跑。
