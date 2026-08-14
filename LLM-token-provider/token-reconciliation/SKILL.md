---
name: token-reconciliation
description: >-
  AI推理平台商业化辅助 skill - 对账与账单设计（Token 工厂商业化 Skill C）。输入计费明细（来自 token-billing）+ 推理原始日志 + 结算周期，
  输出对账机制（三源对账 + 实时/日终两层）+ 差异分类与调账处理 + 账单生成与状态流转。基于 8 年支付/对账经验，差异分类复用"智能对账差异分析助手"方法论。
  核心原则：**先对账（校正）后出账（锁定）**，历史账单禁止原地修改，全部通过调账流水处理。
  与 token-billing（上游计费）、token-review（下游评审）搭配使用，
  当设计 Token 工厂/MaaS 平台的账务对账、调账、账单生成方案时使用。
---

# Token 对账与账单设计（Token 工厂商业化 Skill C）

定位：把计费明细变成「能对平、能出账、能追溯」的账务闭环。
**先对账（校正数据）→ 后出账（锁定结果）**，本 skill 不产生价格、不算费用（归 Skill B）。

## 工作目录

以下所有路径均为相对路径，相对于 skill 所在项目的根目录（即 Claude Code 启动时的当前工作目录）。禁止写死本机绝对路径。

## ⚠️ 计算真实性要求（禁止凭记忆/推断输出）

**所有涉及金额与差异率的计算必须运行配套脚本 `reconciliation_calculator.py`，禁止心算。** 公式与口径定义如下（2026-08-14 固化）：

| # | 计算点 | 公式 / 口径 | 备注 |
|---|---|---|---|
| 1 | 差异率 | `差异笔数 ÷ 真相源A总请求数`（漏记 + 孤儿 + 用量不符 + 重复上报 计入差异笔数；**分母固定取源A**，避免差异率偏高时口径漂移） | 核心 KPI |
| 2 | 阈值判定 | 差异率 < 容忍阈值（默认 0.00005 = 万分之 0.5）→ 微量差异记录不修正；≥ 阈值 → 超阈值生成差异修正单 | 阈值可配置 |
| 3 | 调账净额 | `adjust_amount = Σ(正向补收为 +，反向冲减为 −)`，含 OOM 补记与外部调账 | 带符号汇总 |
| 4 | OOM 补记 | `补记金额 = gpu_physical_seconds × gpu_second_price`（默认 0.05 元/卡秒，可配置） | 不能 0 元 |
| 5 | 账单应收 | `final_amount = original_amount + adjust_amount`（原始计算金额 + 调账净额） | 行业标准 |

脚本位置：`LLM-token-provider/token-reconciliation/reconciliation_calculator.py`

```bash
python3 LLM-token-provider/token-reconciliation/reconciliation_calculator.py \
  --raw-log test/LLM-token-provider/source_a_logs.jsonl \      # 源A 推理原始日志（真相源）
  --metering test/LLM-token-provider/source_b_metering.jsonl \ # 源B 计量/计费明细（token-billing 输出）
  --adjust test/LLM-token-provider/adjustments.jsonl \         # 调账记录（可选，amount 带符号）
  --threshold 0.00005 \                # 差异容忍阈值（默认万分之 0.5）
  --gpu-second-price 0.05              # GPU 卡时单价（OOM 补记用）
```

金额全程 Decimal 定点（与 billing_calculator.py 同一精度方案，ROUND_HALF_UP、4 位小数）。

## 上下游与数据契约（重要）

- **上游 = token-billing（Skill B）**：B 输出的"每笔费用明细"（request_id / tenant_id / tokens / cost / billing_mode / price_snapshot / timestamp）是本 skill 的**计费侧基准**
- **上游补充 = 推理原始日志（真相源）**：vLLM 等推理引擎的原始请求日志（request_id 全局唯一、token 数、GPU 卡时），即使请求 OOM 失败也会落日志——这是对账的**真相源**
- **下游 = token-review（Skill D）**：对账发现的差异率、调账金额、退款率是评审的输入
- **反哺闭环**：对账暴露的计量/计费缺陷，回流修正 token-billing 的计量机制（先修计量，不对账打补丁）
- 若未先运行 B：需用户直接提供计费明细（CSV/JSON 均可），并在 notes 中标注"计费明细来源"

## 借鉴来源（2026-08-14 迭代，参考私有化 MaaS 平台对账&账单设计）

| 借鉴点 | 参考设计思路 | 本 skill 落地 |
|---|---|---|
| 数据流顺序 | 推理日志 → MQ → 计量 → 计费 → **对账（校正）→ 账单** | Step 1-3 对账在前，Step 4 账单在后 |
| 三源对账 | 源A 推理原始日志（真相源）/ 源B 计量库 / 源C 账单库，目标 A≈B≈C | Step 1 |
| 两层对账 | 实时轻量（request_id 去重/漏记补偿）+ 日终批量（条数/聚合/异常 case） | Step 2 |
| 差异修正单 | 超阈值差异生成修正单，不直接改历史数据 | Step 3 |
| 调账流水 | 修正单 → 调账记录（正向补收/反向冲减）→ 下一期账单体现 | Step 3 |
| 账单铁律 | 账单一旦出账不可修改，只能新增调账冲销 | Step 3 + Step 4 |
| 计费中途变更 | 按**请求发生时刻**的计价规则，非出账单时刻 | 与 B 的 price_snapshot 呼应 |

## 输入要求

用户需提供以下信息（缺省时用合理默认值并标注）：

| 输入项 | 说明 | 示例 |
|--------|------|------|
| 计费明细 | 每笔费用明细（来自 B 或直接提供） | B 输出的 fee_details 结构 |
| 推理原始日志 | 真相源（含 OOM/失败请求的日志） | vLLM 原始请求日志 JSONL |
| 客户对账文件（可选） | 客户侧账单/用量数据 | 客户月度账单 CSV |
| 结算周期 | 对账与出账频率 | 实时 + 日终 + 月结 |
| 差异容忍阈值 | 微量差异允许比例 | 万分之 0.5（0.005%） |

---

## 工作流程（先对账，后出账）

### Step 1：对账基准与数据源（对什么账）

**核心：三源对账，目标 A ≈ B ≈ C。**

| 源 | 数据 | 角色 |
|----|------|------|
| **源A：推理原始日志** | request_id（全局唯一）、prefill_time、decode_time、token 数、gpu_physical_seconds；**OOM/失败请求也会落日志** | **真相源**（计量/计费的最终裁判） |
| **源B：计量库数据** | MQ 消费后聚合的租户用量明细（来自 token-billing 的计量模块） | 计量侧 |
| **源C：账单库数据** | 已生成账单的计费明细 | 账务侧 |

**对账顺序**：
1. **先内部自对**（A vs B vs C）：保证"计量数 = 推理实际消耗 = 账单金额"三者一致——这是平台自身可信度的根基
2. **再对外客户对**：平台账单 vs 客户侧账单/用量（差异归因才有意义）

**核心痛点（对账要解决的事）**：推理日志丢消息、重复上报、KV 缓存折扣配置变更、异构卡负载折算参数变更、部分请求 OOM 失败但产生部分 GPU 消耗——导致「计量数 ≠ 推理实际消耗 ≠ 账单金额」。

**输出**：三源数据就绪清单 + 对账基准定义（哪些字段用于逐笔匹配，主键 = request_id）

### Step 2：两层对账机制（怎么对）

#### 1）实时轻量对账（近实时，检测异常）

每条请求处理完成后，以 `request_id` 为唯一主键：

- **重复上报检测**：同一 request_id 重复上报 → 去重，丢弃重复消息（解决 MQ 重复投递）
- **漏记检测**：日志存在、计量库无记录 → 标记漏记，进入**补偿队列**（解决 MQ 消息丢失）
- 计量入库成功 → 写已处理标记

> 目标：第一时间发现异常，不等日终。

#### 2）日终批量对账（最重要，财务结算依赖，凌晨跑任务）

按天维度，输入当天全部源A原始日志，对比源B计量库：

1. **请求总条数**：日志总请求数 VS 计量入库请求数
2. **聚合指标**：总输入 token、总输出 token、总物理 GPU 秒、总标准卡秒
3. **异常 case 单独拎出**：
   - **OOM / 中途中断请求**：已消耗 prefill 算力但没有完整输出 token → **按实际消耗卡时计量，不能直接丢弃不计费**（0 元 = 平台损失）
   - **KV 缓存命中请求**：核对缓存折扣是否按配置生效（缓存价配置变更的追溯）
   - **负载折算参数当天变更**：同一租户同一天出现两套折算系数 → 识别计算偏差

**对账输出 3 种结果**：

| 结果 | 判定 | 处理 |
|------|------|------|
| ✅ 一致 | 无差异 | 不做处理 |
| ⚠️ 微量差异 | 在容忍阈值内（如万分之 0.5） | 记录差异日志，不修正，人工巡检 |
| ❌ 超阈值差异 | 超出容忍阈值 | 生成**差异修正单**（见 Step 3） |

> 差异率 = 差异笔数 ÷ 总笔数，是计费系统健康度关键 KPI，输出给 token-review。

### Step 3：差异分类与调账处理（核心，复用"智能对账差异分析助手"方法论）

#### 3.1 差异分类库（四类基础差异）

| 差异类型 | 特征 | 处理方式 | 风险等级 |
|---------|------|---------|---------|
| 金额不符 | 双方金额对不上 | 拉取原始调用明细核对，按平台侧为准并给出依据 | 高 |
| 漏单 | 平台有、客户无（或反之） | 检查计量缺失/对账时间窗，补充或冲销 | 高 |
| 时间差 | 跨结算周期边界 | 归入正确的账期，避免重复计入下期 | 中 |
| 重复记账 | 同一笔出现两次 | 幂等校验，剔除重复 | 中 |

#### 3.2 典型异常场景库（对账要覆盖的具体 case）

| 异常场景 | 根因 | 对账处理 |
|---------|------|---------|
| MQ 消息丢失 | 原始日志存在、计量无记录 | 调账**补记**用量 |
| MQ 重复消费 | 同一 request_id 多条计量记录 | 去重，多余记录作废 |
| 请求失败 OOM | 已消耗 prefill 算力、无完整输出 | **按真实 GPU 卡时计费**，不能 0 元 |
| 计费策略中途变更 | 租户中途改套餐 | **按请求发生时刻**使用当时的计价规则（= B 的 price_snapshot），非出账单时刻 |
| KV 缓存折扣配置变更 | 折扣比例改过 | 核对配置生效时间，命中/未命中按各自时刻的配置计 |

#### 3.3 调账处理（财务铁律）

**⚠️ 历史账单禁止原地修改**（财务审计要求，不能篡改已出账数据）。

- 超阈值差异 → 生成**差异修正单**
- 修正单不是直接改历史计量/账单，而是生成一笔**调账记录（正向补收 / 反向冲减）**，在**下一期账单**体现
- 调账记录必须：调账单号、租户 id、±用量、±金额、原因、关联对账任务 id

**输出**：差异分类清单 + 调账流水设计 + 每类差异的识别规则/处理动作/责任方

### Step 4：账单生成（先对完账，再出账）

> 账单是对账校正后的**最终结算输出**，不可随意篡改。

#### 4.1 账单生成完整流程

1. 账期结束（如每月 1 号凌晨）
2. **先执行日终对账任务**（Step 2），产出调账流水
3. 聚合计量层用量 + 本期调账流水
4. 调用计费引擎，生成账单主表 + 明细
5. 账单状态：**待确认**，运营人员复核
6. 复核通过：状态改**已确认**；生成可导出 Excel/PDF 账单文件，推送给租户

#### 4.2 账单核心约束

1. 账单一旦确认出账，**不可修改，只能新增调账冲销**
2. **双视图**：租户可见视图 vs 内部财务视图（内部附带硬件成本、毛利，租户看不到）
3. 粒度：日账单 / 月账单，对外业务主要用**月账单**

#### 4.3 账单数据表设计

**bill_main 账单主表**：

```
bill_id | tenant_id | bill_cycle(如 2026-08) | bill_status(待确认/已确认/已调账/已关闭)
total_token_in | total_token_out | total_standard_gpu_second
original_amount(原始计算金额) | adjust_amount(调账总金额，可正可负) | final_amount(最终应收金额)
create_time | confirm_time
```

**bill_item 账单明细子表**：每条明细可以是 按 token 计费 / 卡时消耗 / 套餐固定费用 / **调账记录**（备注写清"对账补记""对账冲减"）

**bill_export_record 导出记录**：PDF/Excel 导出存档

#### 4.4 账单状态流转

```
待确认 → 已确认 → 已调账 → 已关闭
```

#### 4.5 账单输出内容

**租户侧对外展示**（客户看到）：
- 账期、租户名称、各模型输入/输出 token 用量
- 计费模式（按 Token / 包套餐 / 按卡时）
- 原始费用、调账说明、最终应付金额
- ❌ **不展示**：GPU 物理卡秒、硬件成本、毛利率（内部敏感）

**内部财务运营视图**（后台，租户字段基础上额外增加）：
- 总硬件成本（账期真实硬件消耗成本）、账期整体毛利率
- OOM 请求统计、KV 缓存命中率
- 调账明细完整原因

**账单配套能力**：账单查询（按租户/账期，明细可下钻到 request_id）、导出（Excel 对账 / PDF 结算）、账单通知（消息推送"账单就绪"）

### Step 5：反哺闭环（对完账、出完账之后）

- **差异根因分析**：差异集中在某类（如缓存 token 计量不准、OOM 漏计）→ 回流 token-billing 修正计量机制
- **KPI 输出给 token-review**：差异率、调账金额占比、退款率
- **结算与发票**：结算周期、结算单生成、发票开具（专票/普票）；出海需补充汇率与多币种规则

---

## 输出格式（双输出：严格 JSON + HTML 对账账单报告）

**底层数据 = 严格 JSON**（机器可读，由 `reconciliation_calculator.py` 产出，禁止手写数字）：
**演示层 = HTML 一页式报告**（给老板/客户/演示用，由 `reconciliation_report.py` 渲染脚本从 JSON 生成，**HTML 内每个数字都来自 JSON，禁止手填**）。

```bash
# 1) 计算对账与账单（产出 JSON）
python3 LLM-token-provider/token-reconciliation/reconciliation_calculator.py \
  --raw-log test/LLM-token-provider/source_a_logs.jsonl --metering test/LLM-token-provider/source_b_metering.jsonl \
  --adjust test/LLM-token-provider/adjustments.jsonl --threshold 0.00005 --gpu-second-price 0.05 \
  > test/LLM-token-provider/reconciliation_output.json

# 2) 渲染 HTML 报告（读 JSON 生成，数字同源）
python3 LLM-token-provider/token-reconciliation/reconciliation_report.py \
  --input test/LLM-token-provider/reconciliation_output.json \
  --output test/LLM-token-provider/reconciliation_report_2026-08-14.html \
  --title "Token 平台 · 对账与账单报告" --period "2026-08-14" --tenant "全租户"
```

**HTML 报告结构（8 区块，浅色主题，纯本地无外部依赖，可打印 PDF）**：
1. **结论横幅**：差异率 + 判定结果（一致/微量/超阈值）+ 最终应收金额（一屏看懂）
2. **KPI 大卡片**：原始金额 / 调账净额 / 最终应收 / 差异率 / 计费请求数
3. **三源对账对比表**：源A vs 源B 请求数、输入/输出 token、差值（标红差异）
4. **差异清单**：漏记 / 孤儿 / 重复上报 / 用量不符 四类（含处理动作）
5. **调账流水表**：OOM 补记 + 外部调账（±金额、原因）
6. **账单主表**：bill_main 字段（金额、用量、状态流转）
7. **账单明细类型说明**：按 token / 卡时 / 套餐 / 调账记录
8. **双视图 + 审计说明**：租户侧 vs 内部财务侧；历史账单不可篡改铁律

数字统一 2 位小数（金额）、全中文面向决策者；风格与 token-cost-pricing 的 HTML 简报一致（同一 CSS 体系）。

> 原 JSON schema（下方示例）保持为机器可读的底层结构；运行时以 `reconciliation_calculator.py` 实际输出为准。

```json
{
  "reconciliation_baseline": {
    "sources": {
      "A_inference_raw_log": "真相源：request_id/GPU卡时/token数，OOM也落日志",
      "B_metering_data": "计量库：来自 token-billing",
      "C_billing_data": "账单库：已生成账单的计费明细"
    },
    "objective": "A ≈ B ≈ C，先内部自对，再对外客户对",
    "match_primary_key": "request_id"
  },
  "two_layer_reconciliation": {
    "real_time": {
      "dup_detect": "同一request_id重复上报→丢弃（MQ重复投递）",
      "missing_detect": "日志有计量无→补偿队列（MQ消息丢失）"
    },
    "daily_batch": {
      "dimensions": ["请求总条数", "总输入token", "总输出token", "总物理GPU秒", "总标准卡秒"],
      "exception_cases": ["OOM/中断按实际卡时计费", "KV缓存折扣配置生效核对", "负载折算参数变更识别"]
    },
    "tolerance_threshold": 0.00005,
    "outputs": ["一致不处理", "微量差异记录不修正", "超阈值生成差异修正单"]
  },
  "difference_classification": [
    {"type": "金额不符", "action": "拉取原始调用明细核对，平台侧为准", "severity": "高"},
    {"type": "漏单", "action": "检查计量缺失/对账时间窗，补充或冲销", "severity": "高"},
    {"type": "时间差", "action": "归入正确账期，避免重复计入", "severity": "中"},
    {"type": "重复记账", "action": "幂等校验，剔除重复", "severity": "中"}
  ],
  "abnormal_scenarios": [
    {"case": "MQ消息丢失", "handling": "调账补记用量"},
    {"case": "MQ重复消费", "handling": "去重，多余记录作废"},
    {"case": "请求失败OOM", "handling": "按真实GPU卡时计费，不能0元"},
    {"case": "计费策略中途变更", "handling": "按请求发生时刻计价（price_snapshot），非出账时刻"},
    {"case": "KV缓存折扣配置变更", "handling": "按配置生效时间追溯"}
  ],
  "adjustment_rules": {
    "iron_rule": "历史账单禁止原地修改，只能新增调账冲销",
    "flow": "超阈值差异→差异修正单→调账记录（正向补收/反向冲减）→下一期账单体现",
    "adjust_record": "调账单号/租户id/±用量/±金额/原因/关联对账任务id",
    "oom_formula": "补记金额 = gpu_physical_seconds × gpu_second_price（默认 0.05 元/卡秒）"
  },
  "bill_generation": {
    "flow": "账期结束→日终对账产出调账流水→聚合计量用量+调账→生成账单主表+明细→待确认→复核→已确认→导出推送",
    "bill_main_fields": ["bill_id","tenant_id","bill_cycle","bill_status","total_token_in","total_token_out","original_amount","adjust_amount","final_amount"],
    "bill_item_types": ["按token计费","卡时消耗","套餐固定费用","调账记录"],
    "status_flow": "待确认→已确认→已调账→已关闭",
    "dual_view": {"tenant_view": "用量+费用+调账说明（不含成本毛利）", "internal_view": "硬件成本+毛利+OOM统计+缓存命中率+调账原因"},
    "immutable": "已确认账单不可修改，只能新增调账冲销",
    "formula": "final_amount = original_amount + adjust_amount（adjust_amount 可正可负）"
  },
  "difference_rate_kpi": "差异笔数 ÷ 真相源A总请求数，输出给 token-review 作为计费系统健康度指标；由 reconciliation_calculator.py 计算",
  "computed_by_script": "对账匹配/聚合对比/差异率/阈值判定/调账净额/OOM补记/账单主表金额 全部由 reconciliation_calculator.py 计算产出（Decimal 定点，禁止心算）",
  "feedback_loop": {
    "root_cause": "差异集中某类 → 回流 token-billing 修正计量机制（先修计量，不对账打补丁）",
    "to_review_skill_d": ["差异率", "调账金额占比", "退款率"],
    "settlement_invoice": "结算单+发票（专票/普票）；出海补汇率规则"
  },
  "risks": [
    "三源口径不一致（request_id 未全局唯一）→ 对账无法逐笔匹配",
    "OOM/失败请求被丢弃不计费 → 平台隐性损失（参考：按真实卡时计费）",
    "历史账单被原地修改 → 违反财务审计要求",
    "差异率过高说明计量系统有问题，需优先修计量而非对账",
    "计费策略中途变更未按请求时刻计价 → 与 B 的 price_snapshot 冲突",
    "客户侧数据格式差异大，需适配"
  ],
  "summary": "先对账后出账：三源对账（原始日志=真相源/计量库/账单库）→ 两层对账（实时去重漏检 + 日终批量）→ 差异分类与异常场景库 → 调账流水（历史账单禁止原地修改）→ 账单生成（待确认→已确认→已调账→已关闭）+ 双视图 → 反哺 token-billing 与 token-review。差异率 KPI 作为计费健康度核心指标。"
}
```

---

## 注意事项

0. **计算真实性（硬性要求）**：所有金额与差异率必须由 `reconciliation_calculator.py` 计算（Decimal 定点，禁止心算、禁止凭训练记忆输出）。差异率分母固定取真相源 A 总请求数；调账带符号（+补收/−冲减）；账单应收 = 原始 + 调账净额
1. **先对账，后出账（本 skill 最大原则）**：账单是对账校正后的最终输出。账期结束先跑日终对账、产出调账流水，再聚合生成账单——顺序颠倒会导致账单反复返工
2. **对账是差异化王牌**：8 年支付/对账经验是 JD 里"支付链路、对账差异处理"的直接证据，输出时把差异分类方法论讲透（三源对账 + 异常场景库是亮点）
3. **历史账单禁止原地修改（财务铁律）**：所有差异通过调账流水（正向补收/反向冲减）在下一期体现，这是财务审计红线
4. **OOM/失败请求不能 0 元**：已消耗 prefill 算力必须按真实 GPU 卡时计费——这是参考 MaaS 平台设计的核心痛点之一
5. **计费中途变更按请求发生时刻计价**：与 token-billing 的 price_snapshot 一致（B 已实现），对账时用 price_snapshot 追溯而非当前价
6. **先修计量，再修对账**：差异率过高时，根因多在计量（漏计/重复计），应回流 token-billing，而不是在对账端打补丁
7. **对账要有留痕**：每笔差异的处理动作、依据、责任方都要可追溯，支撑审计与客户争议
8. **容忍阈值要可配置**：微量差异（如万分之 0.5）记录不修正，避免过度修正引入新误差；阈值需按平台规模校准
9. **结合 2026 行业背景**：Token 平台出海涉及多币种/税务，若出海需补充汇率与发票规则
10. **若信息不足**：给出合理的行业默认假设并在 notes 中说明，不要臆造
