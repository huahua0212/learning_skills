---
name: token-review
description: >-
  AI推理平台商业化辅助 skill - 商业化评审（复盘→诊断→调优→人工确认闭环）。输入定价测算模型的预估值（来自 token-cost-pricing）+ 线上真实运行数据（来自 token-billing 计量计费 / token-reconciliation 对账账单），
  输出真实毛利复盘 + 预估VS真实偏差诊断 + 竞争力三层诊断 + 风险扫描 + 调价建议与运营策略 + 定价前后对比（待人工确认）。
  **不重新测算毛利**（毛利预估值在 token-cost-pricing 已完成），本 skill 基于真实数据计算实际毛利并与预估值做偏差对比。
  核心闭环：定价预估毛利 → 真实账单毛利 → 偏差诊断 → 风险扫描 → 调价建议 → 人工确认 → 反向修正定价参数（回炉 token-cost-pricing）。
  当平台上线运营后需要评估"实际赚不赚钱、定价要不要调"时使用。
---

# Token 工厂商业化评审（Token 工厂商业化 Skill D）

定位：把「定价测算模型的**预估值**」和「线上**真实运行数据**」做偏差对比，自动诊断毛利、竞争力、风险，输出调价策略与运营优化方案，形成商业化闭环。

**本 skill 不算预估毛利（那是 Skill A 的事）——本 skill 用 B/C 的真实数据算实际毛利，再和 A 的预估比。**

```
普通平台：计费 → 出账单
你的平台：计费 → 复盘 → 诊断 → 自动调优 → 人工确认 → 反向修正定价参数（行业高级闭环）
```

## 工作目录

以下所有路径均为相对路径，相对于 skill 所在项目的根目录（即 Claude Code 启动时的当前工作目录）。禁止写死本机绝对路径。

## 数据契约（前置预估 vs 后置真实）

**输入分两侧，全部引用上游 skill 输出，禁止凭空编数：**

| 数据侧 | 来源 | 字段 |
|--------|------|------|
| **预估侧（前置：定价测算）** | token-cost-pricing | `token_cost`（预估单 token 成本）、`pricing_suggestion`（预估毛利/建议价）、`throughput_estimate`（理论吞吐）、`cost_model.utilization`（预估利用率） |
| **真实侧（后置：线上运行）** | token-billing | 实际用量结构（输入/输出/缓存比例）、实际缓存命中率、实际计费收入（fee_details 汇总） |
| | token-reconciliation | `bill_main.final_amount`（真实账单应收）、差异率、调账金额、OOM 统计 |
| 目标毛利 | 与 token-cost-pricing 输入一致 | 缺省 60% |
| 运营数据（可选） | 直接提供 | 实际 GPU 利用率、实际吞吐压测值 |

> 数据契约：若上游未跑，需标注"该侧数据未经上游校验"；预估侧与真实侧**口径必须一致**（同一模型、同一计费模式），否则偏差对比无意义。

---

## 计算真实性要求（禁止凭记忆/推断输出）

**所有偏差、毛利、调价建议的计算必须运行配套脚本 `review_calculator.py`，禁止心算。** 公式与口径定义如下（2026-08-14 固化，与 cost_calculator.py 同源）：

| # | 计算点 | 公式 / 口径 | 备注 |
|---|---|---|---|
| 1 | 真实总月成本 | `单卡月成本 ÷ 真实利用率 × (1 + overhead_ratio)` | 与 A 的 cost_calculator.py 完全一致 |
| 2 | 真实单 token 成本 | `真实总月成本 ÷ (真实吞吐 × 月有效秒数) × 1e6`；月有效秒数 = 30×24×3600×load_factor | 输入/输出分开算 |
| 3 | **真实缓存成本** | `真实输入成本 × 10%`（Anthropic 缓存读取 = 0.1x 输入价，成本侧同理取输入成本 10%） | **2026-08-14 全链路验证修正**：缓存命中时 prefill 成本≈0，仅剩 KV 读取带宽成本；错用输入成本会导致缓存毛利被严重低估、结构误判亏损型 |
| 4 | 五大偏差 | 成本偏差率=(真实−预估)÷预估；毛利率偏差=真实−目标(**pt**)；吞吐偏差率=(真实−理论)÷理论；利用率偏差=真实−预估(**pt**)；缓存命中率偏差=真实−预估(**pt**) | **注意 pt 与比率的区别** |
| 5 | 真实毛利率 | `(真实收入 − 真实成本) ÷ 真实收入`；收入 = C 的 bill_main.final_amount | 月口径 |
| 6 | 请求级毛利 | `Σ(用量×售价) − Σ(用量×真实成本)`；缓存用真实缓存成本（见 #3），输入/输出用各自真实成本 | 结构竞争力判定（亏损单识别） |
| 7 | 调价建议 | `新售价 = 真实成本 ÷ (1 − 目标毛利)`；调整幅度 = (新售价−旧售价)÷旧售价 | 成本加成反推，非拍脑袋 |

脚本位置：`LLM-token-provider/token-review/review_calculator.py`

```bash
python3 LLM-token-provider/token-review/review_calculator.py \
  --estimated test/LLM-token-provider/token_cost_pricing_output.json \   # Skill A 输出（预估侧）
  --actual test/LLM-token-provider/review_actual_input.json \            # 真实侧（B/C 产出：真实利用率/吞吐/命中率/收入/用量结构）
  --target-margin 0.6 --output test/LLM-token-provider/review_output.json
```

金额全程 Decimal 定点（与 billing_calculator.py 同一精度方案）；**人工确认铁律：脚本只输出定价调整方案（前后对比），不自动改价**。

## 输出格式（双输出：严格 JSON + HTML 商业化评审报告）

**底层数据 = 严格 JSON**（机器可读，由 `review_calculator.py` 产出，禁止手写数字）；
**演示层 = HTML 一页式报告**（给老板/客户/演示用，由 `review_report.py` 渲染脚本从 JSON 生成，**HTML 内每个数字都来自 JSON，禁止手填**）。

```bash
# 1) 计算评审（产出 JSON）
python3 LLM-token-provider/token-review/review_calculator.py \
  --estimated test/LLM-token-provider/token_cost_pricing_output.json --actual test/LLM-token-provider/review_actual_input.json \
  --target-margin 0.6 --output test/LLM-token-provider/review_output.json

# 2) 渲染 HTML 报告（读 JSON 生成，数字同源）
python3 LLM-token-provider/token-review/review_report.py \
  --input test/LLM-token-provider/review_output.json \
  --output test/LLM-token-provider/review_report_2026-08.html \
  --title "Token 平台 · 商业化评审报告" --period "2026-08"
```

**HTML 报告结构（6 区块，浅色主题，纯本地无外部依赖，可打印 PDF）**：
1. **结论横幅**：毛利达标/不达标判定 + 真实 vs 目标毛利 + 根因 + 调价建议摘要（一屏看懂）
2. **KPI 大卡片**：真实毛利率 / 目标毛利率 / 毛利偏差 / 真实总月成本 / 真实输出成本
3. **五大偏差表**：成本（输入/输出）/ 毛利 / 吞吐（输入/输出）/ 利用率 / 缓存命中率，预估→真实 + 偏差标色
4. **真实成本表** + **结构竞争力表**（请求级毛利判定，亏损型/盈利型结构 callout）
5. **调价建议表**：调整前 vs 调整后 + 变化幅度（状态"待人工确认"）
6. **风险扫描表**（等级标色）+ **人工确认回炉闭环** callout

数字统一 2 位小数（金额）、全中文面向决策者；风格与 token-cost-pricing / token-reconciliation 的 HTML 报告一致（同一 CSS 体系）。

---

## 工作流程（复盘 → 诊断 → 调优 → 人工确认）

### Step 1：毛利偏差复盘（最核心，预估 VS 真实）

**五大偏差（公式全部与 cost_calculator.py 口径一致，由脚本计算，禁止心算）：**

| # | 偏差指标 | 公式 | 单位 | 诊断读法 |
|---|---------|------|------|---------|
| 1 | 单 Token 成本偏差率 | (真实成本 − 预估成本) ÷ 预估成本 | 比率 | 真实硬件成本 vs 投产前 Roofline 预估成本 |
| 2 | 毛利率偏差 | 真实毛利率 − 目标毛利率（如 60%） | **百分点 pt** | 真实毛利 vs 目标毛利（注意：是 pt 差，不是比率） |
| 3 | 吞吐偏差率 | (真实 prefill/decode 吞吐 − 理论吞吐) ÷ 理论吞吐 | 比率 | 真实吞吐 vs Roofline 理论吞吐 |
| 4 | 利用率偏差 | 真实 GPU 利用率 − 预估利用率 | 百分点 pt | 资源实际利用 vs 预估（摊薄成本的关键） |
| 5 | 缓存命中率偏差 | 真实缓存命中率 − 预估缓存命中率 | 百分点 pt | 命中率过低 → prefill 成本高（agent 客户核心指标） |

**真实成本计算（必须复用 cost_calculator.py 的公式结构，用真实利用率/真实吞吐替代预估，保证两侧可比）：**

```
# 口径与 token-cost-pricing/cost_calculator.py 完全一致：
#   effective_cost = monthly_cost ÷ utilization
#   total_monthly_cost = effective_cost × (1 + overhead_ratio)
#   cost_per_million = total_monthly_cost ÷ (throughput × seconds_per_month × load_factor) × 1e6
真实总月成本 = 单卡月成本 ÷ 真实利用率 × (1 + overhead_ratio)
真实输入成本/百万 = 真实总月成本 ÷ (真实输入吞吐 × 月有效秒数) × 1e6
真实输出成本/百万 = 真实总月成本 ÷ (真实输出吞吐 × 月有效秒数) × 1e6
月有效秒数 = 30 × 24 × 3600 × load_factor
```

**真实毛利计算**（用 B/C 真实数据，不重算 A 的预估）：

```
真实毛利率 = (真实收入 − 真实成本) ÷ 真实收入
  真实收入 = token-reconciliation 的 bill_main.final_amount（真实账单应收）
  真实成本 = 按真实用量结构加权的混合成本（B 的实际输入/输出/缓存比例 × 各自真实成本）
```

**偏差结论示例（系统自动输出）**：
- 当前模型真实毛利 **32%**，远低于定价预估 **60%**
- 根因：Decode 真实吞吐比理论低 40%，国产卡带宽瓶颈严重
- GPU 实际利用率只有 35%，远低于预估 70%

### Step 2：商业化竞争力诊断（三层诊断）

**① 成本竞争力**：同机型行业单 Token 成本对比（内置行业区间）
- 你的成本偏高/偏低原因：利用率、卡型、吞吐衰减

**② 售价竞争力**：和公开市场 API 价格对比
- 判断：高价无优势 / 价格合理 / 性价比极高

**③ 结构竞争力（高级）**：分析**用户请求结构**（用 B 的真实用量数据，按请求级毛利判定）：

```
# 请求级毛利判定（识别优质/亏损单）
单请求毛利 = (输入token × 输入售价 + 输出token × 输出售价 + 缓存token × 缓存售价)
           − (输入token × 真实输入成本 + 输出token × 真实输出成本 + 缓存token × 真实缓存成本)
# 结构判定
优质单：单请求毛利 > 0 且 输出/输入 token 比 低（输入多、输出少，利润率高）
亏损单：单请求毛利 < 0（输入少、输出爆炸，输出成本高）
租户/模型结构 = Σ 各租户/模型请求毛利 → 盈利型结构 or 亏损型结构
```

- **系统自动识别：当前租户群体是盈利型结构还是亏损型结构**（输出负毛利租户/模型清单）
- 结构健康度指标：`负毛利请求数 ÷ 总请求数`（或 负毛利金额 ÷ 总收入）

### Step 3：经营风险扫描清单（自动扫风险）

系统自动生成**风险清单 TOP10**：

| 风险 | 触发条件 | 输出 |
|------|---------|------|
| 毛利不达标 | 真实毛利率 < 目标毛利阈值 | 等级/影响金额/根因/建议 |
| 吞吐衰减 | 真实 Decode 吞吐远低于理论（国产卡高发） | 同上 |
| GPU 利用率过低 | 利用率过低导致摊薄成本暴涨 | 同上 |
| 负毛利用户 | 识别哪些租户/模型/请求亏钱 | 同上 |
| 定价倒挂 | 低价套餐用户大量跑高成本 Decode 任务 | 同上 |
| 缓存命中率过低 | Prefill 成本居高不下 | 同上 |

> 每一条风险都包含：**风险等级、影响金额、根因、建议方案**。若对账差异率/调账金额异常升高，一并纳入（来自 token-reconciliation）。

### Step 4：运营策略与调价建议（两类输出）

**调价建议（系统自动算出，需人工确认）**：

1. **按偏差自动修正模型单价（严谨公式）**：为恢复目标毛利，新售价按成本加成公式反推——
   ```
   新售价 = 真实成本 ÷ (1 − 目标毛利)
   调整幅度 = (新售价 − 旧售价) ÷ 旧售价
   ```
   （成本加成模型下"成本涨 X% → 售价涨 X%"恰好成立，因为售价 = 成本 ÷ (1−毛利) 是线性关系；但必须以真实成本反推，不能凭感觉拍涨幅）
2. **区分输入/输出分别调价**：prefill 成本稳定 → 输入价不动；decode 成本暴涨 → **单独上调输出单价**（输入/输出各用各自的真实成本反推，互不牵连）
3. **差异化租户调价**：对亏损严重的租户（Step 2 结构判定识别）→ 建议阶梯涨价、限制超长生成
4. **套餐档位重配**：发现包月用户超量大量跑高成本输出 → 建议优化套餐梯度

**运营优化建议（AI 式智能输出）**：
- GPU 调度优化：提升利用率
- 开启 KV 缓存降低 prefill 成本
- 限制超长输出 Token 防止 decode 打爆成本
- 劣质用户限流/涨价；优质用户降价挽留

### Step 5：智能调价 & 人工确认流程（关键关卡）

**⚠️ 反向修正定价参数不能自动直接修改——中间必须插入人工确认流程。**

```
调价建议（Step 4 输出）
   → 生成【定价调整方案】含：调整前 vs 调整后对比（新旧价格、预期毛利变化、影响租户范围）
   → 人工确认（业务/财务审核：风险可接受、客户影响可控、合规通过）
   → 确认通过 → 反向修正定价参数（回炉 token-cost-pricing 重算 + 同步 token-billing 计费规则）
   → 确认驳回 → 返回 Step 4 调整方案（或维持现价，记录驳回原因）
```

**定价调整方案必须输出前后对比**：

| 定价项 | 调整前 | 调整后 | 变化 | 预期毛利影响 |
|--------|--------|--------|------|-------------|
| 输入单价 | X | X | 0% | — |
| 输出单价 | Y | Y×1.3 | +30% | 真实毛利 32% → 45% |
| 缓存单价 | Z | Z | 0% | — |
| 套餐档位 | 99元/500万 | 99元/300万 | 梯度重配 | 降低劣质用量 |

**回炉路径（人工确认后触发）**：
- 调价幅度 ≥10% 或成本结构变化 → **重新运行 token-cost-pricing**（成本可能已变，市场价需重新核验，>7 天强制重验）
- 价格变化 → **同步更新 token-billing** 计费规则（含套餐单位经济校验、缓存/批量折扣联动）
- 对账发现的计量缺陷 → **回流 token-reconciliation** 修正对账口径，根因修 token-billing 计量

**闭环判定**：每次评审后输出"是否触发回炉 + 回炉到哪个 skill + 人工确认状态（待确认/已确认/已驳回）"，让商业化体系形成 PDCA 循环。

---

## 输出格式（严格 JSON，不要包含任何其他文字，不要使用 markdown 代码块围栏）

```json
{
  "deviation_analysis": {
    "estimated_vs_actual": {
      "estimated_source": "token-cost-pricing",
      "actual_source": "token-billing + token-reconciliation"
    },
    "four_deviations": [
      {"metric": "单Token成本偏差率", "estimated": 4.2, "actual": 6.8, "deviation_pct": 0.62, "verdict": "成本严重偏高"},
      {"metric": "毛利率偏差率", "estimated": 0.6, "actual": 0.31, "deviation_pt": -0.29, "verdict": "毛利不达标"},
      {"metric": "Decode吞吐偏差率", "estimated": 3000, "actual": 1600, "deviation_pct": -0.46, "verdict": "带宽瓶颈严重"},
      {"metric": "GPU利用率偏差", "estimated": 0.7, "actual": 0.38, "deviation_pt": -0.32, "verdict": "资源闲置严重"}
    ],
    "root_cause_summary": "当前定价模型偏乐观，未考虑国产卡Decode吞吐衰减与低利用率问题，整体商业化盈利能力弱，需要上调输出Token单价、优化调度提升利用率、加强缓存策略"
  },
  "competitiveness_diagnosis": {
    "cost": "同机型行业单Token成本对比结论 + 偏高/偏低原因",
    "price": "相对公开市场API价格：高价无优势 / 价格合理 / 性价比极高",
    "structure": "用户请求结构：盈利型结构 or 亏损型结构（输入多输出少=优质，输入少输出爆炸=劣质）"
  },
  "risk_scan": [
    {"risk": "毛利不达标", "severity": "高", "impact_amount": "XX元/月", "root_cause": "真实毛利率32%<目标60%", "suggestion": "上调输出单价"},
    {"risk": "吞吐衰减", "severity": "高", "impact_amount": "XX元/月", "root_cause": "Decode吞吐比理论低40%", "suggestion": "优化调度/换卡"},
    {"risk": "GPU利用率过低", "severity": "中", "impact_amount": "XX元/月", "root_cause": "实际利用率35%", "suggestion": "批量任务填空闲"}
  ],
  "pricing_adjustment_plan": {
    "status": "待人工确认",
    "before_after": [
      {"item": "输入单价", "before": 7.93, "after": 7.93, "change_pct": 0, "expected_margin_impact": "—"},
      {"item": "输出单价", "before": 18.87, "after": 24.53, "change_pct": 0.3, "expected_margin_impact": "真实毛利32%→45%"},
      {"item": "缓存单价", "before": 0.79, "after": 0.79, "change_pct": 0, "expected_margin_impact": "—"},
      {"item": "套餐档位", "before": "99元/500万", "after": "99元/300万", "change_pct": null, "expected_margin_impact": "降低劣质用量"}
    ]
  },
  "operation_strategies": [
    {"action": "GPU调度优化提升利用率", "expected": "摊薄成本下降"},
    {"action": "开启KV缓存降低prefill成本", "expected": "缓存命中率提升"},
    {"action": "限制超长输出防止decode打爆成本", "expected": "劣质单减少"},
    {"action": "劣质用户限流/涨价，优质用户降价挽留", "expected": "收入结构优化"}
  ],
  "feedback_loop": {
    "manual_confirmation_required": true,
    "confirmation_status": "待确认",
    "if_confirmed": "回炉 token-cost-pricing 重算（市场价>7天重验）→ 同步 token-billing 计费规则 → 更新套餐单位经济校验",
    "if_rejected": "返回 Step 4 调整方案或维持现价，记录驳回原因",
    "expected_effect": "价格体系与真实成本/市场重新对齐，毛利恢复目标水平"
  },
  "summary": "真实毛利32% vs 预估60%，偏差-29pt，根因是Decode吞吐衰减（-46%）与利用率过低（-32%）；建议单独上调输出单价30%（待人工确认）、优化调度提升利用率、开启缓存降低prefill成本；确认后回炉A重算定价"
}
```

---

## 注意事项

1. **本 skill 不重复测算预估毛利**：预估毛利/预估成本/理论吞吐来自 token-cost-pricing（Skill A），本 skill 只用 B/C 真实数据算实际毛利并与预估对比——重复测算会掩盖偏差，让闭环失效
2. **偏差复盘是核心**：五大偏差（成本/毛利/吞吐/利用率/缓存命中率）必须给出公式与中间值，偏差结论要落到根因，不能只报数字
3. **缓存成本口径必须用 0.1x（血泪教训）**：缓存命中时 prefill 成本≈0，真实缓存成本 = 输入成本 × 10%（Anthropic 0.1x 口径）。**2026-08-14 全链路验证曾因缓存成本错用输入成本，导致缓存毛利 -5.06 被误判为亏损、结构误判"亏损型"**——缓存请求毛利在结构判定、混合毛利两处都必须用真实缓存成本
4. **人工确认是铁律（本 skill 最大原则）**：反向修正定价参数**禁止自动直接修改**——必须先输出定价调整方案（前后对比），经人工确认后才回炉 A 重算。这是业务/财务合规底线
5. **区分输入/输出分别调价**：prefill 成本稳定则输入价不动，decode 成本暴涨单独上调输出价——只做整体调价会误伤优质客户
6. **结构竞争力是高级亮点**：分析用户请求结构（输入多输出少=优质、输入少输出爆炸=劣质），识别盈利型/亏损型租户结构——这是普通平台不会做的
7. **数据契约严格**：预估侧引用 token-cost-pricing、真实侧引用 token-billing/token-reconciliation，两侧口径必须一致；上游未跑须标注"未经上游校验"
8. **风险清单要量化**：每条风险包含风险等级、影响金额、根因、建议方案，不能只列现象
9. **结合 2026 行业背景**：OpenAI 降价、国产开源崛起、订阅转按量——这些会同时影响竞争力和风险判断
10. **若信息不足**：给出合理的行业默认假设并在 notes 中说明，不要臆造
11. **评审不是终点**：输出调价建议后必须走 Step 5 人工确认闭环（确认 → 回炉 A → 同步 B），避免"评审完了价格没动"
