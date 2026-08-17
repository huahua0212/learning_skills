# LLM-token-provider · Token 工厂商业化「四 Skill 体系」

> 范例二：**多 Skill 串联成体系**。演示一个 AI 推理平台（Token 工厂 / MaaS）的完整商业化闭环——从「算力成本 → 怎么定价」到「怎么算钱 → 怎么对平 → 实际赚不赚钱」，拆成 A/B/C/D 四个 skill，每个 skill 独立可演示，又通过数据契约串成一条链。每个 skill 都是**双输出**：严格 JSON（机器可读）+ HTML 报告（给老板/演示用）。

## 这个体系解决什么业务问题

做一个 Token 推理平台，商业化要回答四个递进的问题：

1. **怎么定价**？算力成本是多少，怎么定输入/输出/缓存价格才能达标毛利？
2. **怎么算钱**？推理引擎每笔用量怎么计量、按什么规则算成费用？
3. **怎么对平**？计费结果和真实消耗对不上怎么办，怎么出账、怎么追溯？
4. **实际赚不赚钱**？真实毛利和当初预估差多少，该怎么调价？

四个 skill 恰好一一对应。

## Skill 编排：A → B → C → D → 回炉 A

```
A token-cost-pricing   怎么定价：算力成本 → 建议价格（产出预估毛利/成本/吞吐）
  │  价格字段（input/output/cache 单价）作为 B 的输入
  ▼
B token-billing        怎么算钱：计量 + 计费规则（产出费用明细 + 汇总）
  │  费用明细作为 C 的对账输入、D 的真实收入输入
  ▼
C token-reconciliation 怎么对平：三源对账 + 差异调账 + 账单（产出真实账单应收）
  │  差异率/调账金额/真实收入作为 D 的输入
  ▼
D token-review         真实赚不赚钱：真实毛利复盘 + 偏差诊断 + 调价建议
  │  输出定价调整方案（前后对比）→ 必须人工确认
  ▼
（人工确认通过）→ 回炉 A 重算定价（市场价 >7 天须重验）
```

**每个 skill 的职责边界（这是体系设计的关键）**：

| Skill | 职责 | 不做什么 |
|---|---|---|
| A token-cost-pricing | 成本核算 + 定价建议 + 市场对标 | 不算费用、不出账 |
| B token-billing | 用量采集 → 费用明细 | 不管账户、不对账 |
| C token-reconciliation | 对账 + 调账 + 账单 | 不产生价格、不算费用 |
| D token-review | 用真实数据复盘 + 调价建议 | 不自动改价（必须人工确认） |

**数据契约**：下游直接引用上游输出的字段（如 B 引用 A 的 `pricing_suggestion`），不各造一套示例值，保证全链路数字同源。

## 双输出模式（每个 skill 通用）

每个 skill 配两个脚本：**计算器**（产出严格 JSON）+ **报告渲染器**（从 JSON 生成 HTML，数字同源、禁止手填）。

```
计算器 (xxx_calculator.py) → JSON   ← 机器可读，禁止心算，所有计算必须跑它
报告渲染器 (xxx_report.py)  → HTML   ← 给老板/客户看，每个数字都来自 JSON
```

> **渲染器输入兼容性（A 已实现，2026-08-17）**：渲染器自动识别两种输入——① 计算器直接输出的**平铺结构**（此时定价/市场对标/风险区块显示"待 AI 编排层补充"占位）；② AI 编排层补齐 `cost_model`/`pricing_suggestion`/`market_comparison`/`risks`/`summary` 后的**完整结构**（全区块渲染）。直接跑"计算器 → 渲染器"链路不再丢数据。

脚本位置（相对项目根目录，输出统一进 `test/LLM-token-provider/`）：

| Skill | 计算器 | 报告渲染器 | 输出（JSON + HTML） |
|---|---|---|---|
| A | [cost_calculator.py](token-cost-pricing/cost_calculator.py) | [pricing_report.py](token-cost-pricing/pricing_report.py) | `test/LLM-token-provider/token_cost_pricing_output.json` 等 |
| B | [billing_calculator.py](token-billing/billing_calculator.py) | [billing_report.py](token-billing/billing_report.py) | `test/LLM-token-provider/billing_output.json` 等 |
| C | [reconciliation_calculator.py](token-reconciliation/reconciliation_calculator.py) | [reconciliation_report.py](token-reconciliation/reconciliation_report.py) | `test/LLM-token-provider/reconciliation_output.json` 等 |
| D | [review_calculator.py](token-review/review_calculator.py) | [review_report.py](token-review/review_report.py) | `test/LLM-token-provider/review_output.json` 等 |

## 各 Skill 的流程与作用

### A · token-cost-pricing（怎么定价）

输入 GPU/模型/利用率/目标毛利 → 计算单 token 成本（元/百万 token）→ 给出定价建议 + 价格阶梯（缓存折扣 0.1x、批量 50%、套餐校验）+ 市场对标（**必须联网核验官方价，禁止凭记忆**）→ 风险清单。

- 输出 `pricing_suggestion`：输入/输出/缓存单价，是全体系的**价格来源**。
- 铁律：成本计算必须跑 [cost_calculator.py](token-cost-pricing/cost_calculator.py)，禁止心算。

### B · token-billing（怎么算钱）

把推理引擎的 token 用量拉进来 → 按规则算成费用明细。五模块：用量采集 → 批次计量 → 规则引擎 → 费用明细 → 下游交接。

- 关键规则：`finish_reason` 分支计费（429/401 服务未执行**不计费**、content_filter **要收费**，Azure OpenAI 官方口径）、缓存识别（最小可缓存长度按模型划分，不是统一 1024）、缓存价格因子因厂商而异（**必须从价格体系传入，禁止写死**）、批量 50% 折扣、超长上下文 272K 加价。
- **价格快照**：每笔费用写入计费当刻价格，调价不重算历史——这是反查可信度的核心设计。
- 铁律：费用必须跑 [billing_calculator.py](token-billing/billing_calculator.py)。

### C · token-reconciliation（怎么对平）

把计费明细和推理原始日志（真相源）做**三源对账**（A≈B≈C）→ 差异分类（漏记/孤儿/重复/金额不符）→ 调账处理（**历史账单禁止原地修改**，只能新增调账冲销）→ 出账（先对完账再出账）。

- 核心 KPI：**差异率** = 差异笔数 ÷ 真相源 A 总请求数。
- 关键场景：MQ 消息丢失补记、OOM 请求**按真实 GPU 卡时计费不能 0 元**、计费中途变更按请求发生时刻计价（呼应 B 的价格快照）。
- 铁律：金额/差异率必须跑 [reconciliation_calculator.py](token-reconciliation/reconciliation_calculator.py)。

### D · token-review（实际赚不赚钱）

拿 A 的**预估**和 B/C 的**真实数据**做偏差对比（成本/毛利/吞吐/利用率/缓存命中率五大偏差）→ 竞争力三层诊断（成本/售价/结构）→ 风险扫描 → 调价建议（成本加成反推）→ **人工确认后回炉 A**。

- 不重复测算预估毛利（那是 A 的事），只用真实数据算实际毛利并对比。
- 铁律：反向修正定价参数**禁止自动直接修改**，必须先输出「定价调整方案（前后对比）」经人工确认——这是业务/财务合规底线。

## 快速演示（一个循环跑通 A→B→C→D）

假设已有 `test/LLM-token-provider/` 作为产物目录，从项目根目录：

```bash
# A：成本核算 + 定价（产出 JSON）
python3 LLM-token-provider/token-cost-pricing/cost_calculator.py \
  --gpu-type "英伟达H100" --model "DeepSeekV4Pro" \
  --cost-mode monthly --cost 25000 --utilization 0.7 \
  --input-tps 8000 --output-tps 4000 --load-factor 0.6 \
  > test/LLM-token-provider/token_cost_pricing_output.json

# B：计量计费（用 A 的价格参数，产出费用明细 JSON）
python3 LLM-token-provider/token-billing/billing_calculator.py \
  --prices '{"input":7.93,"output":18.87,"cache":0.79}' \
  --usage-file test/LLM-token-provider/usages.jsonl \
  > test/LLM-token-provider/billing_output.json

# C：对账与账单（用 B 的明细 + 推理原始日志，产出账单 JSON）
python3 LLM-token-provider/token-reconciliation/reconciliation_calculator.py \
  --raw-log test/LLM-token-provider/source_a_logs.jsonl \
  --metering test/LLM-token-provider/source_b_metering.jsonl \
  --adjust test/LLM-token-provider/adjustments.jsonl \
  --threshold 0.00005 --gpu-second-price 0.05 \
  > test/LLM-token-provider/reconciliation_output.json

# D：商业化评审（A 的预估 + B/C 的真实数据，产出评审 JSON）
python3 LLM-token-provider/token-review/review_calculator.py \
  --estimated test/LLM-token-provider/token_cost_pricing_output.json \
  --actual test/LLM-token-provider/review_actual_input.json \
  --target-margin 0.6 \
  --output test/LLM-token-provider/review_output.json

# 每步之后可运行对应 report 渲染器，把 JSON 渲染成一页式 HTML 报告
python3 LLM-token-provider/token-billing/billing_report.py \
  --input test/LLM-token-provider/billing_output.json \
  --output test/LLM-token-provider/billing_report_2026-08.html \
  --title "Token 平台 · 计量计费报告" --period "2026-08"
```

完整命令与每个 skill 的输入说明见各自的 [SKILL.md](token-billing/SKILL.md)。

## 边界

- **规则均有官方依据核验**（2026-08-14）：finish_reason 分支、缓存门槛、批量折扣、超长上下文加价等，来源见各 SKILL.md，禁止凭训练记忆推断。
- **市场对标必须实时核验**：价格以官方定价页为准，无法联网时 `verified=false` 并标注，禁止编造。
- **账户/充值/风控不在此体系**：B 只算钱不管钱，扣款归账户体系。

## 这个范例教会你

1. 一个复杂业务如何拆成**多个 skill 串联**（A→B→C→D），各自职责边界清晰、通过数据契约衔接。
2. **双输出模式**：机器可读 JSON + 演示层 HTML 报告，一套数据两种受众——这是"给老板演示"的标准做法。
3. **计算真实性**：所有金额/差异/毛利必须跑脚本、禁止 AI 心算；规则要有官方依据并记录来源。
4. **人工确认关卡**：涉及改价等对外决策的环节，skill 只输出方案不自动执行——合规底线要写进 skill。
5. **产物统一落 `test/LLM-token-provider/`**：全链路中间文件集中、可串接、可追溯。
