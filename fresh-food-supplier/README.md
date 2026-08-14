# fresh-food-supplier · 生鲜供应「接单 → 采购 → 配送」演示 Skill

> 范例一：**单 Skill 形态**。演示一个业务闭环（客户下单 → 编码定价 → 风控确认 → 采购/分单）如何被拆成确定性分层脚本，让 AI 一步步执行、每层都产出可见文件，最终能跑通"接单 → 采购 → 配送"全链路。

本目录不是生产系统，是给内部对齐会看的**能跑通全链路的演示样板**。

## 这个 Skill 解决什么业务问题

生鲜供应商每天收到大量口语/表格形式的订单。人工处理要经历：收单 → 识别商品（"洋芋"就是"土豆"）→ 定价格（大客户协议价 vs 市场价）→ 查异常（超量？超截单？未知商品？）→ 汇总采购、安排分车分仓。

本 skill 把这条人工链路自动化，并**保留每一步的证据**，演示时逐层点开给观众看。

## Skill 架构：AI 编排 + 脚本确定

```
对话智能体（LLM）                        确定性脚本（scripts/）
┌─────────────────────┐  结构化参数  ┌──────────────────────────────┐
│ 口语/图片/表格下单    │ ──────────> │ L1 录入 → L2 编码定价 →        │
│ 解析 + 弹窗确认商户    │             │ L3 风控确认 → L4 采购/分单     │
└─────────────────────┘             │ 每层读写同一份 data/            │
                                    └──────────────────────────────┘
```

- **AI（编排层）只调度，不计算**：解析口语/文件成结构化订单参数、弹窗让用户确认商户、按步骤调脚本。
- **脚本只做确定性规则**：录入强校验、别名映射、单位换算、按客户类型定价、异常识别、采购/分单汇总。

## 分层流程与每层的作用

| 层 | 脚本 | 输入 → 输出 | 作用 / 讲点 |
|---|---|---|---|
| L1 录入 | `scripts/l1_intake.py` | 结构化参数 → `data/orders_raw.csv` | 强校验（客户存在、商品非空、数量>0、单位合法）；失败打印 🔴 无法接单，不落库 |
| L2 编码定价 | `scripts/l2_match_price.py` | `orders_raw.csv` → `orders_matched.csv` | 别名→标准 SKU（洋芋→土豆）、单位归一（各种斤制→斤）、大B协议价/普通市价 |
| L3 异常确认 | `scripts/l3_risk_confirm.py` | `orders_matched.csv` → `orders_risk.csv` + 三态返回 | 识别未知SKU/超量(>50斤)/超截单；常规单自动确认，异常单转人工 + AI 建议 |
| L4 汇总 | `scripts/l4_forecast_dispatch.py` | `orders_risk.csv` → `orders_forecast.csv` + `dispatch.csv` | 采购建议单（按SKU汇总×安全系数1.10、取整到10）；分车/分仓单（大B→中央仓A、普通→前置仓B） |

**演示的叙事落点**：L1–L3 把录入、编码、风控做成确定性自动化（异常才转人工）；L4 让「接单 → 采购 → 配送」的闭环在演示中**可见**。

## 数据流转：`data/` 下的证据链

所有层读写同一份 `data/`，改一处即影响下游全部：

```
orders_raw.csv (L1) → orders_matched.csv (L2) → orders_risk.csv (L3) → orders_forecast.csv + dispatch.csv (L4)
```

配套配置表：
- `customers.csv`：客户档案（类型/截单时间/结算周期）
- `sku_mapping.csv`：别名 → 标准 SKU（土豆/马铃薯/洋芋 都是 SHU001）
- `prices.csv`：市场价 vs 协议价

改这些配置即可演示不同业务走向（新客户、新商品、新价格、异常组合）。

## 运行环境

- 纯标准库 Python 3（`csv/argparse/os/sys/datetime/math`），无第三方依赖。
- 数据目录默认 `<skill>/data`，可用环境变量 `ORDER_DATA` 覆盖。

## 快速演示

完整演示脚本见 [SKILL.md](mock-bill-submission/SKILL.md) 的「标准演示脚本」小节，核心是这一条链：

```bash
# 口语下单由 AI 解析成结构化参数（并弹窗确认商户）
python3 scripts/l1_intake.py add --customer C003 --item 土豆 --qty 10 --unit 斤 --remark "加急"
python3 scripts/l2_match_price.py        # 编码定价 → orders_matched.csv
python3 scripts/l3_risk_confirm.py       # 风控确认 → 三态返回
python3 scripts/l4_forecast_dispatch.py  # 采购/分单 → orders_forecast.csv + dispatch.csv
# 随时重置演示态
python3 scripts/reset.py
```

## 边界

- **演示样板，非生产**：阈值（超量50斤、安全系数1.10）、路由仓均为硬编码简化；生产需接 OMS/TMS/实时市价。
- **单位换算**：白名单单位统一折算到斤（1吨=2000斤、1公斤=2斤），新单位在 `UNIT_TO_JIN` 增项即可。
- **L4 简化假设**：转人工单也计入采购/分单（视为仍将履约），真实场景应先按最终确认结果再汇总。

## 这个范例教会你

1. 一个业务闭环如何拆成**确定性分层脚本**（每层职责单一、输入输出明确）。
2. **AI 只做编排**（解析口语、确认、调度），**计算交给脚本**——这是"演示可信"的关键。
3. **每层都写 `data/` 产物**，让流程"看得见"，这是演示 demo 与"黑盒一句话"的本质区别。
