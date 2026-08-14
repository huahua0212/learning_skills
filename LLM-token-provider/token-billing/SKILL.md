---
name: token-billing
description: >-
  AI 推理平台商业化辅助 skill - 计量与计费规则设计。输入定价方案（推荐来自 token-cost-pricing 的 JSON 输出）+ 计费模式诉求 + 客户类型，
  输出从「推理引擎用量采集」到「费用明细下游交接」的五模块工程化方案。借鉴云产品分层（采集→规则→出账→对账），简化为 token 业务 5 步。
  边界：只负责「用量→费用」，账户充值/风控不涉及，账单对账归 token-reconciliation。
  与 token-cost-pricing（上游定价）、token-reconciliation（下游对账）、token-review（下游评审）搭配使用，
  当设计 Token 工厂/MaaS 平台如何采集 token 用量、如何按规则计算费用时使用。
---

# Token 计量与计费规则设计（Token 工厂商业化 Skill B）

定位：把价格体系落成「**推理引擎 usage → 费用明细**」的工程化方案。
**只算钱，不管钱（账户）、不对账（账单）。**五模块覆盖完整链路。

## 工作目录

以下所有路径均为相对路径，相对于 skill 所在项目的根目录（即 Claude Code 启动时的当前工作目录）。禁止写死本机绝对路径。

## 上下游与数据契约（重要）

本 skill 不独立产生价格，价格字段必须与上游对齐：

- **上游 = token-cost-pricing（Skill A）**：A 输出严格 JSON，B 直接引用其字段，不得另造示例值：
  - `input_price_per_million` ← `A.pricing_suggestion.input_price_per_million`
  - `output_price_per_million` ← `A.pricing_suggestion.output_price_per_million`
  - `cache_hit_price_per_million` ← `A.pricing_suggestion.price_tiers_impact.cache_discount.cache_hit_price_per_million`（缺省 = 输入价 × 0.1）
  - 批量/套餐设计须先通过 A 的 `price_tiers_impact` 校验（批量增量毛利、套餐单位经济）
- **下游 = token-reconciliation（Skill C，账务对账）**：B 输出的「费用明细表 + 租户周期汇总」是对账的输入
- **下游 = token-review（Skill D，商业化评审）**：B 输出的运营数据（用量/收入/finish_reason 分布/套餐抵扣占比）是评审的输入
- **回炉 A**：B 产出的「实际缓存命中率 + 实际用量结构」是 A 重算成本的依据
- 若未先运行 A：可在此直接提供价格参数，但必须在 notes 中标注「价格未经成本核算校验，建议先跑 token-cost-pricing」

## 借鉴来源与简化点

借鉴国内云厂商「计量计费产品」分文档档的工程化做法（采集→规则引擎→价格中心→出账→对账），针对 token 业务做了三档简化：

| 维度 | 云产品做法 | token 业务简化 |
|---|---|---|
| 分层架构 | 采集→规则→价格中心→出账→对账 | 同样借鉴，5 模块化 |
| 计费场景 | 十几种（按量/包年包月/抢占式/预留实例…）| 3 档（按量 / 包周期 / 批量任务） |
| 产品目录 | 几十类（计算/存储/网络/CDN…）| 1 维（模型名） |
| 出账周期 | 小时/日/月三级 | 实时 + 日冻结 + 月结算 三档 |
| 账户扣款/欠费/合同 | 全套 | **舍弃**——本体系账户模块独立，账户扣款由后续账户体系处理 |

## ⚠️ 计算真实性要求（禁止凭记忆/推断输出）

**所有费用计算必须运行配套脚本 `billing_calculator.py`，禁止心算、禁止凭训练记忆输出数值。** 规则来源已全部核验官方文档（2026-08-14）：

| 规则 | 核验来源 | 核验结论 |
|---|---|---|
| finish_reason 计费分支 | Azure OpenAI 官方 FAQ（learn.microsoft.com） | 429/401 服务未执行处理**不计费**；200+content_filter / 400 / 408 服务执行了处理**要收费** |
| 缓存读取价 | Anthropic 官方 prompt caching 文档 | **0.1x 输入价**（仅 Anthropic）；DeepSeek 官方：缓存命中 0.025 vs 未命中 3.0 = **0.0083x**——**因厂商而异，必须从价格体系传入，不能写死** |
| 缓存最小可缓存长度 | Anthropic 官方 | **按模型划分**：512 / 1024 / 2048 / 4096 不等，**不是统一 1024** |
| 缓存写入成本 | Anthropic 官方 | 缓存写入要收费：5 分钟 TTL = 1.25x 输入价、1 小时 TTL = 2x 输入价（**成本侧必须考虑，否则缓存毛利被高估**） |
| 批量折扣 | OpenAI 官方 Batch API 文档 | **50% 折扣**，24h 内完成，超时已完成部分仍收费 |
| 超长上下文加价 | OpenAI GPT-5.5 官方 | 输入 >272K tokens 时**全会话**输入 ×2、输出 ×1.5（1M 上下文模型建议启用） |

脚本位置：`LLM-token-provider/token-billing/billing_calculator.py`（与 token-cost-pricing 的 cost_calculator.py 同一套路）

```bash
# 单笔：价格 + 用量 → 费用明细（含 price_snapshot / applied_rules）
python3 LLM-token-provider/token-billing/billing_calculator.py \
  --prices '{"input":7.93,"output":18.87,"cache":0.79}' \
  --usage '{"request_id":"req_001","tenant_id":"t_a","model":"deepseek-v4-pro",
            "prompt_tokens":2048,"completion_tokens":512,"cached_tokens":0,
            "finish_reason":"stop"}'
# 批量：usage-file 传 JSONL，每行一个用量对象；--batch 标记批量折扣
python3 LLM-token-provider/token-billing/billing_calculator.py \
  --prices '{"input":7.93,"output":18.87,"cache":0.79}' --usage-file test/LLM-token-provider/usages.jsonl
```

## 输入要求

用户需提供以下信息（缺省时用合理默认值并标注）：

| 输入项 | 说明 | 示例 |
|---|---|---|
| 价格体系 | 输入/输出/缓存单价（来自 A 或直接提供） | 输入7.93 输出18.87 缓存0.79（元/百万token） |
| 推理引擎 | 当前用什么框架暴露 OpenAI 兼容端点 | vLLM / TRT-LLM / SGLang / One API |
| 计费模式诉求 | 用哪几种计费方式组合 | 按量 + 包周期套餐（高频企业） + 批量任务（离线） |
| 客户类型 | 主要卖给谁 | 开发者（按量） / 企业租户（包周期） / 混合 |
| 结算周期 | 出账频率 | 实时扣费 / 日冻结 / 月结算 |

---

## 工作流程（5 模块）

### Step 1：用量采集（Usage Collection）

**目标**：把推理引擎的 token 用量拉进来，构建原始用量流。

**1.1 采集字段标准 schema**（全体系沿用，推理引擎必须返回）：

| 字段 | 类型 | 说明 | 数据源 |
|---|---|---|---|
| `request_id` | string | **主键、幂等键**——后续四模块都靠它追踪 | 推理框架调用生成的唯一 ID |
| `tenant_id` | string | 调用方租户 ID（多租户必备） | 网关层注入 |
| `model` | string | 模型名 | 请求参数 |
| `prompt_tokens` | int | 实际送入的输入 token | 推理框架 usage.prompt_tokens |
| `completion_tokens` | int | 实际生成的输出 token | 推理框架 usage.completion_tokens |
| `cached_tokens` | int | 缓存命中的输入 token | 推理框架 usage.cached_tokens / `cache_read_input_tokens` |
| `finish_reason` | enum | `stop` / `length` / `tool_calls` / `content_filter` / `rate_limit` | 推理框架返回 |
| `timestamp` | ISO 8601 | 完成时间 | 网关层获取 |

**1.2 采集机制**：

- **采集点**：推理网关（vLLM / TRT-LLM / One API 反向代理）的响应处理钩子里
- **传输**：异步队列（Kafka / RabbitMQ）削峰，避免网关阻塞
- **重试**：回调失败 3 次重试 → 进死信队列 + 告警（**漏 1 笔 = 漏 1 笔的钱**）
- **采集位置原则**：**网关机采，不前端采集**——前端价格变更要全量改后端机采
- **缓存命中识别**：依赖推理引擎 / 网关的 prefix cache 实现（如 vLLM 的 Automatic Prefix Caching）。如果引擎不报告 cached_tokens，则将 `cached_tokens` 字段记为 0，价格按 input 计

**1.3 输出**：原始用量流（按 `request_id` 幂等 append）

### Step 2：批次计量（Batch Metering）

**目标**：把请求级流按维度聚合，形成可计费的批次数据。

**2.1 聚合维度**：
- **必选**：`tenant_id + model + 时间窗`
- **可选**（推荐开启）：`api_key / 子项目 / 环境（生产 / 测试）`——便于精细化对账与异常告警

**2.2 时间窗**：

| 出账周期 | 切片规则 | 用途 |
|---|---|---|
| 实时计量 | 分钟级 | 控制台实时用量展示 / 套餐额度扣减 |
| 日冻结 | 每日 0:00 | 日对账、日账单 |
| 月结算 | 每月 1 日 0:00 | 月账单结算 |

**2.3 幂等与迟到达数据**：
- **幂等**：主键 = `request_id`，重复上报直接 `upsert` 不处理
- **迟到数据**：定义调整窗口（如日结后 24h 内允许补入），超期进「差异报告」推给 Skill C 对账处理

**2.4 输出**：按 `tenant_id × model × 时间窗` 聚合的批次用量表

### Step 3：规则引擎（Billing Rules）

**目标**：把聚合后的用量按规则算成费用。这是 SKILL 的核心。

**3.1 基础费用公式**（与 Skill A 的价格阶梯对齐）：

```
费用 = prompt_tokens/1e6 × input_price_per_million
     + completion_tokens/1e6 × output_price_per_million
     + cached_tokens/1e6 × cache_hit_price_per_million
```

**3.2 finish_reason 分支规则**（已核验 Azure OpenAI 官方 FAQ，2026-08-14；总原则 = **服务是否执行了处理**）：

| `finish_reason` | 业务含义 | 计费规则 | 官方依据 |
|---|---|---|---|
| `stop` / `tool_calls` | 正常完成 | 按实际 token **全收** | Azure FAQ：200 正常 |
| `length` | 达 `max_tokens` 截断 | 按实际 token **全收**（用户已用满配额） | Azure FAQ：200 正常 |
| `content_filter` | 部分内容被安全过滤 | **仍收费**（收输入 + 实际输出） | Azure FAQ：200+content_filter 会收费；因内容过滤的 400 也收费 |
| `rate_limit` | 未生成就被限流 | **不计费** | Azure FAQ：429 服务未执行处理不收费 |
| `auth_error` | 认证失败 | **不计费** | Azure FAQ：401 服务未执行处理不收费 |
| 客户端网络中断 | 推到一半断流 | 收输入 token 部分（prefill 已发生，建议 100%） | 平台自定义策略，非官方口径，须标注 |
| 未知 finish_reason | — | 保守全收 + 标注需人工确认 | 兜底策略 |

**3.3 计费模式匹配**（按 `customer_plan_id`）：

| 模式 | 计算逻辑 | 扣减方式 |
|---|---|---|
| **按量（Pay-as-you-go）** | 实时按规则 3.1+3.2 计算 | 累计计入账户余额扣减项（不实际扣款，扣款归账户体系） |
| **包周期套餐（Subscription）** | 超量按按量；套餐内扣额度 | 包月内的额度从套餐额度中扣减；扣完 → 切到按量或暂停 |
| **批量任务（Batch）** | cost × 0.5（OpenAI 官方 Batch API 50% 折扣，24h 内完成，超时已完成部分仍收费） | 仅当请求标记 `batch=true` 生效，异步处理 |

**模式优先级**：包周期 → 按量 → 批量（批量可叠加在任一模式上作为折扣层）

**3.4 缓存识别规则**（已核验 Anthropic 官方，2026-08-14）：

- **最小可缓存长度按模型划分，不是统一 1024**：Claude Opus5/Fable5/Mythos5 = **512**；Claude Sonnet5 / Opus4.x = **1024**；Claude Opus4.7 / Mythos Preview / Haiku3.5 = **2048**；Claude Opus4.6/4.5 / Haiku4.5 = **4096**。配置表已固化在 `billing_calculator.py` 的 `CACHE_MIN_TOKENS`
- `cached_tokens ≥ 该模型最小可缓存长度` 才单独按缓存价计；否则合并到 `prompt_tokens` 按输入价收（避免小请求因缓存价导致亏损）
- **缓存写入成本（成本侧必须考虑）**：缓存写入本身要收费——5 分钟 TTL = 1.25x 输入价、1 小时 TTL = 2x 输入价（Anthropic 官方）。自建平台时缓存写入消耗 prefill 算力，定价时必须把写入成本计入，否则缓存毛利被高估。缓存命中免费刷新 TTL
- **缓存价格因子因厂商而异**：Anthropic 缓存读取 = 0.1x 输入价；DeepSeek V4 Pro = 0.025/3.0 = **0.0083x**。**必须从价格体系传入，禁止写死 0.1x**

**3.5 超长上下文加价（可选规则，已核验 OpenAI GPT-5.5 官方）**：

- 输入 > **272K tokens** 时，**全会话**输入 ×2、输出 ×1.5（OpenAI GPT-5.5 官方规则）
- 1M 上下文模型（如 DeepSeek V4）建议启用；普通模型可关闭
- 脚本参数：`--long-context-threshold 272000`

**3.6 规则引擎输出**：每笔 `request_id` 对应一条**费用记录**（见 Step 4）

### Step 4：费用明细（Fee Detail Generation）

**目标**：产出**可追溯、可重算**的费用明细，作为对账依据。

**4.1 费用明细 schema**（append-only 表）：

```json
{
  "billing_record_id": "uuid",
  "request_id": "req_xxx",           // 外键 → 原始用量
  "tenant_id": "tenant_a",
  "model": "deepseek-v4-pro",
  "billing_mode": "按量",              // 按量 / 包周期 / 批量
  "customer_plan_id": "plan_xxx",
  "tokens": {
    "input": 1200,
    "output": 320,
    "cache_hit": 8000
  },
  "cost": {
    "input": 0.0095,
    "output": 0.0060,
    "cache_hit": 0.0063,
    "total": 0.0218,
    "currency": "CNY"
  },
  "price_snapshot": {                 // 价格快照 = 计费当刻的锁价
    "input_price_per_million": 7.93,
    "output_price_per_million": 18.87,
    "cache_hit_price_per_million": 0.79,
    "snapshot_at": "2026-08-14T13:30:05+08:00"
  },
  "applied_rules": [                  // 记录本次计费应用了哪些规则分支
    "basic_formula",
    "finish_reason_stop_full",
    "cache_below_threshold_merged_to_input"
  ],
  "billing_period": "2026-08-14:hour-13",
  "created_at": "2026-08-14T13:30:05+08:00"
}
```

**4.2 幂等设计**：
- 主键 = `request_id + billing_mode`（同一请求在不同模式只算一次）
- 重采/补采：不处理已有数据；冲突进入「差异报告」

**4.3 价格快照原则**（**核心反查可信度设计**）：
- 计费当刻的价格**冻结**写入 `price_snapshot`
- 后续即使 Skill A 调价，**历史明细不被重算**
- 反查争议时：明示「按 8 月 14 日 13:30 的价格快照计算」

**4.4 输出**：append-only 费用明细表（按 `billing_period` 已分区）。

**4.5 ⚠️ 数值必须来自脚本**：费用明细中的 `cost.*` / `price_snapshot` / `applied_rules` / `charged` 字段**一律由 `billing_calculator.py` 计算产出**，禁止手工填写或凭记忆估算。脚本输出即明细结构（`fee_details` 数组元素即为 4.1 的 schema 实例）。

### Step 5：下游交接（Hand-off）

**目标**：把费用明细按下游需求组装成不同视图。

**5.1 给 Skill C（对账）**：
- **明细视图**：整张费用明细（可直接对客户 usage）
- **汇总视图**：按 `tenant_id × billing_period` 汇总（用于账单对账）
- **差额视图**：按 `finish_reason` 区分计费与否（便于对账核对漏计/多计）

**5.2 给 Skill D（评审）**：
- **收入分布**：按 `model × billing_mode` 的收入占比
- **健康指标**：
  - `finish_reason` 分布（rate_limit 占比高 → 容量不足）
  - `billing_mode` 分布（按量 vs 包周期）
  - 套餐抵扣占比 / 批量任务占比
  - 缓存命中率（agent 类客户的实际命中率，回炉 A 的依据）

**5.3 回 Skill A（成本归集）**：
- **实际成本归集**：按 `model × tenant × period` 的实际 token 消费量
- **结构反推**：输入/输出/缓存实际比例（用于 A 的 `load_factor` 校准）
- **触发回炉条件**：
  - 缓存命中率偏离 A 假设 > 20pt
  - 输入/输出比例偏离 A 假设 > 30pt
  - 实际成本 / 预期成本比 > 1.2 或 < 0.8
  - 触发时启动「重新测算 A」流程

---

## 输出格式（双输出：严格 JSON + HTML 计量计费报告）

**底层数据 = 严格 JSON**（机器可读，由 `billing_calculator.py` 产出，禁止手写数字）；
**演示层 = HTML 一页式报告**（给老板/客户/演示用，由 `billing_report.py` 渲染脚本从 JSON 生成，**HTML 内每个数字都来自 JSON，禁止手填**）。

```bash
# 1) 计算费用（产出 JSON）
python3 LLM-token-provider/token-billing/billing_calculator.py \
  --prices '{"input":7.93,"output":18.87,"cache":0.79}' --usage-file test/LLM-token-provider/usages.jsonl \
  > test/LLM-token-provider/billing_output.json

# 2) 渲染 HTML 报告（读 JSON 生成，数字同源）
python3 LLM-token-provider/token-billing/billing_report.py \
  --input test/LLM-token-provider/billing_output.json \
  --output test/LLM-token-provider/billing_report_2026-08.html \
  --title "Token 平台 · 计量计费报告" --period "2026-08"
```

**HTML 报告结构（4 区块，浅色主题，纯本地无外部依赖，可打印 PDF）**：
1. **结论横幅**：计费/不计费笔数 + 总费用 + 不计费原因（一屏看懂）
2. **KPI 大卡片**：总费用 / 计费请求数 / 输入 token / 输出 token / 缓存 token（含占输入侧比例）
3. **费用明细表**：request_id / 租户 / 计费模式 / 三段 token / 费用 / 应用规则（不计费行黄色标注）
4. **计费模式分布 + 规则应用统计 + 价格快照**：模式收入占比、每条规则触发笔数、计费当刻锁价（调价不重算历史）

数字统一 2 位小数（金额）、全中文面向决策者；风格与 token-cost-pricing / token-reconciliation / token-review 的 HTML 报告一致（同一 CSS 体系）。

> 原 JSON schema（下方示例）保持为机器可读的底层结构；运行时以 `billing_calculator.py` 实际输出为准。

```json
{
  "usage_collection": {
    "schema": ["request_id","tenant_id","model","prompt_tokens","completion_tokens","cached_tokens","finish_reason","timestamp"],
    "primary_key": "request_id",
    "collection_point": "inference_gateway_response_hook",
    "transport": "async_queue (Kafka/RabbitMQ)",
    "retry_policy": "3 retries, then dead_letter_queue + alert",
    "notes": "网关机采，不前端采集；缓存字段依赖推理引擎能力（如 vLLM Automatic Prefix Caching）"
  },
  "batch_metering": {
    "aggregation_keys": ["tenant_id","model","time_window"],
    "optional_keys": ["api_key","sub_project","env"],
    "time_windows": [
      {"name":"real_time","granularity":"minute","use":"control_panel_and_package_deduction"},
      {"name":"daily_freeze","granularity":"day","cutoff":"00:00","use":"daily_billing"},
      {"name":"monthly_close","granularity":"month","cutoff":"day-1 00:00","use":"monthly_invoice"}
    ],
    "idempotency": "主键=request_id，重复upsert；迟到数据24h内补入，超期走C对账差异报告"
  },
  "billing_rules": {
    "basic_formula": "cost = prompt_tokens/1e6 × input_price + completion_tokens/1e6 × output_price + cached_tokens/1e6 × cache_hit_price",
    "finish_reason_branches": {
      "stop_or_tool_calls": "按实际token全收",
      "length_truncated": "按实际token全收（已用满max_tokens）",
      "content_filter": "收输入 + 实际输出",
      "rate_limit": "不计费（OpenAI/Anthropic口径）",
      "client_disconnect": "收输入token（prefill已发生）"
    },
    "billing_modes": {
      "pay_as_you_go": "实时按规则3.1+3.2",
      "subscription": "先扣额度，超量切按量或暂停",
      "batch_discount": "费用×0.5，仅batch=true生效"
    },
    "cache_recognition": "cached_tokens≥1024单独按缓存价，否则合并到input按输入价",
    "notes": "规则来源：Azure OpenAI FAQ + Anthropic 官方文档核验（2026-08-14），非业内惯例推断"
  },
  "fee_detail": {
    "schema_example": {
      "billing_record_id": "uuid",
      "request_id": "req_xxx",
      "tenant_id": "tenant_a",
      "model": "deepseek-v4-pro",
      "billing_mode": "按量",
      "customer_plan_id": "plan_xxx",
      "tokens": {"input":1200,"output":320,"cache_hit":8000},
      "cost": {"input":0.0095,"output":0.0060,"cache_hit":0.0063,"total":0.0218,"currency":"CNY"},
      "price_snapshot": {"input_price_per_million":7.93,"output_price_per_million":18.87,"cache_hit_price_per_million":0.79,"snapshot_at":"2026-08-14T13:30:05+08:00"},
      "applied_rules": ["basic_formula","finish_reason_stop_full"],
      "billing_period": "2026-08-14:hour-13",
      "created_at": "2026-08-14T13:30:05+08:00"
    },
    "primary_key": "request_id+billing_mode",
    "price_snapshot_principle": "计费当刻锁价；后续Skill A调价不重算历史；反查争议按snapshot_at为准"
  },
  "hand_off": {
    "to_reconciliation_skill_c": ["明细视图(full table)","汇总视图(tenant×period)","差额视图(finish_reason分类)"],
    "to_review_skill_d": ["收入分布(model×mode)","finish_reason分布","billing_mode分布","缓存命中率"],
    "back_to_pricing_skill_a": ["实际成本归集","输入/输出/缓存实际比例","缓存命中率实际值"],
    "trigger_reevaluation": [
      "缓存命中率偏离假设>20pt",
      "输入/输出比例偏离>30pt",
      "实际成本/预期成本∉[0.8,1.2]"
    ]
  },
  "pricing_source": {
    "from_skill_a": true,
    "input_price_per_million": 7.93,
    "output_price_per_million": 18.87,
    "cache_hit_price_per_million": 0.79,
    "notes": "价格直接引用 token-cost-pricing 输出；若未跑 A 则此处标注未经成本核算校验"
  },
  "implementation_recommendations": {
    "queue_priority": "异步队列优先级 = 输入(低) < 输出(中) < 缓存监控(高)",
    "data_retention": "原始用量保留 18 个月(对账追溯用)；费用明细保留 60 个月(财务凭证)",
    "monitoring_kpis": [
      "采集失败率 (目标 < 0.1%)",
      "幂等冲突率 (异常波动反映重采/补采)",
      "rate_limit 占比 (容量预警)",
      "缓存命中率 (回炉A的核心指标)"
    ]
  },
  "risks": [
    "推理引擎不返回 cached_tokens → 缓存命中率永远=0 → 缓存折扣失效 → A 的 price_tiers_impact 与实际不匹配",
    "finish_reason 误判 → 多计或漏计（如将 rate_limit 错判为 length） → 客户投诉",
    "价格快照设计失效：若不改 price_snapshot，调价同时改规则3.1公式 → 历史反查失真",
    "凌晨跨日数据延迟：00:00 切日时未到的请求 → 日结数据缺失 → 对账差异",
    "包周期/按量切换边界不清 → 同一笔费用同时算两遍",
    "单位换算错误：500万 token=5 百万不是 50 百万",
    "缓存写入成本漏算：Anthropic 缓存写入=1.25x/2x 输入价；自建平台缓存写入消耗 prefill 算力，漏算会高估缓存毛利（2026-08-14 核验新增）",
    "缓存最小长度门槛按模型划分（512/1024/2048/4096），写死 1024 会错（2026-08-14 核验修正）",
    "缓存价格因子因厂商而异（Anthropic 0.1x vs DeepSeek 0.0083x），写死 0.1x 会错（2026-08-14 核验修正）",
    "禁止心算：费用必须跑 billing_calculator.py，手工估算必错"
  ],
  "summary": "借鉴云产品分层简化为 5 模块(采集→批次→规则→明细→交接)。核心：① 网关机采保证数据完整性 ② 价格快照保证反查可信度 ③ finish_reason 分支规则对齐 Azure OpenAI 官方口径（429/401 不计费是关键）④ 缓存命中率回炉 A 是闭环关键。所有费用计算必须运行 billing_calculator.py（2026-08-14 已核验官方文档：缓存门槛按模型划分、缓存价因厂商而异、缓存写入有成本、批量 50%、超长上下文 272K 加价）。所有价格字段直接引用 token-cost-pricing 输出，保证与定价一致；明细表交给 token-reconciliation 做账单与对账。"
}
```

---

## 注意事项

1. **计算真实性是生命线（硬性要求）**：**所有费用数值必须由 `billing_calculator.py` 计算，禁止心算、禁止凭训练记忆或推断输出**。脚本内含核验过的规则表（finish_reason 分支 / 缓存最小长度 / 批量折扣），输出含 `applied_rules` 记录每条规则应用，审计可查
2. **计量准确性是生命线**：Token 计费的第一原则是「每笔用量算得准」，网关机采 + 异步队列 + 幂等 + 重试 + 死信告警五件套缺一不可
3. **价格快照（price_snapshot）是反查的生命线**：调价不重算历史是商业可信度的根本，不要图省事省略
4. **finish_reason 分支规则来源**：Azure OpenAI 官方 FAQ 已核验（2026-08-14）——服务是否执行处理是收费分水岭（429/401 不计费、content_filter 收费），这是与客户对账争议时的依据
5. **缓存规则已核验 Anthropic 官方（2026-08-14）**：最小可缓存长度按模型划分（512/1024/2048/4096 不等）；缓存写入收费（1.25x/2x 输入价）成本侧必须考虑；缓存读取价 0.1x 仅适用 Anthropic，DeepSeek 为 0.0083x——**缓存价必须从价格体系传入，禁止写死**
6. **缓存依赖引擎能力**：若推理引擎（vLLM/TRT-LLM）不返回 `cached_tokens` 字段，缓存折扣即使设计上正确也无法生效。**实际部署前必须验证引擎的缓存命中率报告能力**
7. **边界清晰：本 skill 只算钱**——账户余额/充值/扣款/风控属于账户体系（本体系已舍弃该模块，若业务需要请另行设计），账单生成/对账/结算属于 token-reconciliation，不要混入
8. **对账反哺**：对账发现的差异问题（漏计/重复计）应回流修正本 skill 的计量机制——见 token-reconciliation 的 Step 4
9. **单位换算必查（血泪教训）**：中文「500 万 token」= 5 百万（million），不是 50 百万；套餐校验先算「套餐单价（元/百万）= 套餐价 ÷ 含 token 百万数」，若该单价低于单位成本套餐才可能亏
10. **若信息不足**：给出合理的行业默认假设并在 notes 中说明，不要臆造
11. **数据保留期**：原始用量建议保留 18 个月（对账追溯），费用明细保留 60 个月（财务凭证），与税法要求对齐
