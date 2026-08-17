---
name: token-cost-pricing
description: >-
  AI推理平台商业化辅助 skill - 算力成本核算与token定价。输入GPU类型/单卡成本/利用率/折旧期/目标毛利/模型，
  输出单token成本核算表 + 建议定价体系 + 市场对标 + 风险清单。
  支持 roofline 自动估算吞吐（prefill/decode 双模型）与显存可行性检查。
  市场对标必须实时核验官方定价页，禁止凭训练数据报价。
  当你需要为Token工厂/MaaS推理平台测算成本、制定定价方案时使用。
---

# 算力成本 → token 定价（Token 工厂商业化 Skill A）

你提供算力资源参数和目标毛利，我按以下流程输出完整的成本核算与定价方案。

## 工作目录

以下所有路径均为相对路径，相对于 skill 所在项目的根目录（即 Claude Code 启动时的当前工作目录）。禁止写死本机绝对路径。

## 输入要求

用户需提供以下信息（缺省时用合理行业默认值并标注）：

| 输入项 | 说明 | 示例 |
|--------|------|------|
| GPU 类型 | 使用什么算力 | 英伟达 H100 / A100 / 海光 DCU / 昇腾 910B |
| 单卡成本 | 单卡采购或租赁成本 | 25000 元/月 或一次性采购价 |
| 利用率 | 平均算力利用率（0-1） | 0.7 |
| 折旧期 | 硬件折旧年限（采购价时） | 3 年 |
| 目标毛利 | 期望毛利率 | 60% |
| 模型 | 部署的模型 | Llama-3-8B / Qwen / DeepSeek V4 Pro |
| 吞吐模式 | 手动输入或自动估算，二选一 | manual / auto |
| 输入吞吐 | 手动模式：输入 token/秒（估算值，需压测校准） | 8000 |
| 输出吞吐 | 手动模式：输出 token/秒（估算值，需压测校准） | 4000 |
| 参数量 | 自动模式：模型总参数量（十亿） | 8 |
| 激活参数量 | 自动模式：MoE 激活参数量（缺省=总参数量） | 8 |
| 精度 | 权重字节数：FP16=2 / INT8=1 / INT4=0.5 | 2 |
| 内存带宽 | 自动模式：GPU 内存带宽 GB/s | 3350 |
| 峰值算力 | 自动模式：GPU FP16/BF16 TFLOPS | 989 |
| 显存 | 自动模式：单卡显存 GB（做可行性检查） | 80 |
| 模型结构 | 自动模式：层数 / KV heads / head_dim（显存检查用） | 32 / 8 / 128 |
| 平均 token 数 | 自动模式：平均输入 / 输出 token | 2048 / 512 |
| 负载折算 | 实际负载相对峰值吞吐的折扣，缺省 0.6 | 0.6 |
| 运营成本系数 | 电费/带宽/存储/运维等非硬件成本，缺省 0.2 | 0.2 |
| 效率参数 | 可选：prefill MFU 0.65、decode 效率 0.8、batch 效率 0.9、框架开销 2GB | 使用缺省 |

---

**计算口径**：单 token 成本 = 总月成本 ÷（吞吐 × 每月秒数 × 负载折算）。自动模式下，输入吞吐按 prefill（算力受限）估算，输出吞吐按 decode（内存带宽受限）估算，并做显存可行性检查（权重 + KV cache + 框架开销 ≤ 显存）。

---

## 配套脚本

- **`cost_calculator.py`**：算力成本核算脚本（本 skill 目录下）。Step 1 的数值计算必须用它，不要心算。

## 工作流程

### Step 1：成本核算（用 Python 脚本计算，不心算）

**1. 吞吐获取（二选一）**
   - 手动模式：用户提供输入/输出 token/秒（估算值，需压测校准）
   - 自动模式：脚本用 roofline 模型估算，不需要手填吞吐：
     - prefill（输入处理，算力受限）：`input_tps = GPU_TFLOPS × 1e12 × prefill_mfu ÷ (2 × active_params)`
     - decode（输出生成，内存带宽受限，单序列）：`decode_tps_per_seq = 内存带宽 × 1e9 ÷ (active_params × bytes_per_param) × decode_efficiency`
     - 聚合输出吞吐 = `decode_tps_per_seq × 有效并发/批大小 × batch_efficiency`，有效并发由显存余量 ÷ 每序列 KV cache 决定

**2. 用脚本计算**，不手动心算。运行本 skill 目录下的 `cost_calculator.py`：

手动吞吐：
```bash
python3 LLM-token-provider/token-cost-pricing/cost_calculator.py \
  --gpu-type "英伟达H100" \
  --model "DeepSeekV4Pro" \
  --cost-mode monthly --cost 25000 --utilization 0.7 \
  --input-tps 8000 --output-tps 4000 --load-factor 0.6
```

自动估算吞吐（推荐）：
```bash
python3 LLM-token-provider/token-cost-pricing/cost_calculator.py \
  --gpu-type "英伟达H100" \
  --model "Llama-3-8B" \
  --cost-mode monthly --cost 25000 --utilization 0.7 \
  --params-billion 8 --bytes-per-param 2 \
  --num-layers 32 --num-kv-heads 8 --head-dim 128 \
  --avg-prompt-tokens 2048 --avg-output-tokens 512 \
  --memory-bandwidth-gbps 3350 --peak-tflops 989 \
  --vram-gb 80
```

一次性采购价模式：
```bash
python3 LLM-token-provider/token-cost-pricing/cost_calculator.py \
  --cost-mode one-time --cost 200000 --depreciation 3 \
  --utilization 0.7 --input-tps 8000 --output-tps 4000
```

**脚本输出说明：**
- `effective_card_cost_per_month` = 月成本 ÷ 利用率（有效成本）
- `total_monthly_cost` = 有效成本 × (1 + 非硬件成本系数)，含电费/带宽/存储/集群/运维等
- `throughput_estimate` = 吞吐来源（manual/estimated）、输入/输出 token/秒、估算假设
- `vram_check` = 显存可行性检查：权重 + KV cache + 框架开销是否 ≤ 显存
- `input_cost_per_million` / `output_cost_per_million` = 单 token 成本（元/百万 token）
- 输入成本按 prefill 吞吐算，输出成本按 decode 聚合吞吐算，二者差异由硬件/模型自动产生，不需要人为设定输出计算权重
- 脚本默认：负载折算 0.6、prefill MFU 0.65、decode 效率 0.8、batch 效率 0.9、非硬件成本系数 0.2，均可按实际调整

**3. 把脚本结果展示给用户**，并保留吞吐估算的假设说明。脚本输出的成本为估算值，需实际压测校准——在结果中明确标注。若 `vram_check.fits=false`，需先调整精度/并发/硬件，不要直接报价。

### Step 2：定价策略

基于成本核算结果，给出定价建议：

1. **选择定价策略**并说明理由：
   - 成本加成（成本 ÷（1 - 目标毛利））
   - 市场对标（参考 DeepSeek/Claude/GPT 官方价）
   - 低价引流（低于成本价抢市场，配合后续提价）
   - 混合策略

2. **给出建议定价**（输入/输出，元/百万 token）

3. **计算毛利率** =（建议价 - 成本价）÷ 建议价，确保达到目标毛利

4. **设计价格体系并测算影响**（可选但建议）——每个机制必须算出经济效果，不能只贴折扣标签：
   - **缓存折扣（通常 0.1x，即缓存读取价）**：给出命中率情景表（0%/30%/50%/70%），逐档算混合成本、混合售价、毛利，证明"价格下降但毛利不掉"。公式（输入:输出 = a:1，命中率 h）：
     - `混合成本(h) = [a×(1-h)×输入成本 + a×h×命中输入成本(≈0.3) + 输出成本] ÷ (a+1)`
     - `混合售价(h) = [a×(1-h)×输入价 + a×h×输入价×0.1 + 输出价] ÷ (a+1)`
     - 结论读法：h 越高，综合报价越低、毛利基本不变 → 缓存是竞争力来源，不是让利
   - **批量折扣（Batch 通常 50% 折扣）**：算"填充空闲产能"的增量经济。公式：
     - `增量毛利 = [批量价 × 增量产出 − 边际成本] ÷ (批量价 × 增量产出)`
     - 边际成本 = 电费等随流量成本（通常占总成本 10-15%）；批量价 = 建议价 × 折扣
     - 结论读法：利用率越低，批量折扣越划算（空转产能 0 收入 → 高毛利增量）；利用率 >90% 时批量折扣=纯让利，应取消
   - **套餐/阶梯价**：校验套餐单位经济：`套餐价 × (1 − 目标毛利) ≥ 套餐内含 token 量 × 单位成本`；其价值在现金流预收与客户锁定（集中度风险），不体现在单 token 成本数学里，需在 notes 说明

### Step 3：市场对标与风险

**1. 市场价实时核验（最重要）**：
   - **禁止凭记忆或训练数据输出任何市场价格**，价格一律以官方定价页/官方 API 为准
   - 每次运行本 skill 时联网核验，并在输出中记录：`source_url`（官方页面链接）、`verified_at`（核验日期）、模型版本
   - 官方来源示例（以实际打开到的页面为准）：
     - DeepSeek：`https://api-docs.deepseek.com/zh-cn/quick_start/pricing`
     - OpenAI：`https://platform.openai.com/docs/pricing`（或官方定价页）
     - Anthropic：`https://www.anthropic.com/pricing`
     - 阿里云百炼/Qwen：`https://help.aliyun.com/zh/model-studio/`（以官方模型广场为准）
   - 无法联网或页面打不开时：`market_comparison` 中 `verified=false`、价格填 `null`，并在风险清单中明确标注“市场价未核验，报价需核验后确认”，绝不能编造价格顶替
   - 距离上次核验超过 7 天，必须重新核验

**2. 关键判断：**
   - 若核算成本接近或超过竞品官方售价，说明以同价竞争无法盈利，必须差异化（服务/稳定性/增值），而非打价格战
   - 缓存命中价通常远低于未命中价——定价设计要充分体现缓存折扣的价值

**3. 竞争力分析**：基于已核验的官方价，说明相对竞品贵/便宜多少、对目标客户的吸引力

**4. 风险识别**：
   - 成本估算偏差（吞吐估算 vs 实际压测）
   - 利用率波动（对有效成本的影响）
   - 国产卡性能差异（海光/昇腾 vs 英伟达）
   - 价格战与竞品调价风险（OpenAI/DeepSeek 等官方调价需持续监控）
   - 上游成本上涨（模型厂商涨价/限接口）
   - 市场价未核验风险（若本轮无法核验，报价决策需暂停或加保护条款）

---

## 输出格式（双输出）

### 输出 1：严格 JSON（机器可读底层数据，必须包含，不要包含任何其他文字，不要使用 markdown 代码块围栏）

```json
{
  "cost_model": {
    "gpu_type": "英伟达 H100",
    "single_card_monthly_cost": 25000,
    "utilization": 0.7,
    "effective_card_cost_per_month": 35714.29,
    "overhead_ratio": 0.2,
    "total_monthly_cost": 42857.14,
    "notes": "有效成本=成本÷利用率；总成本=有效成本×(1+overhead)，含电费/带宽/存储/运维"
  },
  "throughput_estimate": {
    "mode": "estimated",
    "input_tokens_per_sec": 40178.12,
    "output_tokens_per_sec": 27738.0,
    "effective_batch": 184,
    "params_billion": 8.0,
    "active_params_billion": 8.0,
    "bytes_per_param": 2.0,
    "prefill_mfu": 0.65,
    "decode_efficiency": 0.8,
    "decode_tokens_per_sec_per_seq": 167.5,
    "notes": "prefill=算力受限；decode=内存带宽受限；吞吐为估算值，需压测校准"
  },
  "token_cost": {
    "input_cost_per_million": 0.6859,
    "output_cost_per_million": 0.9935,
    "notes": "输入成本按 prefill 吞吐、输出成本按 decode 聚合吞吐计算；估算值，需压测校准"
  },
  "vram_check": {
    "weights_gb": 16.0,
    "kv_cache_per_seq_gb": 0.336,
    "effective_batch": 184,
    "kv_cache_total_gb": 61.74,
    "framework_overhead_gb": 2.0,
    "total_vram_required_gb": 79.74,
    "vram_gb": 80.0,
    "fits": true
  },
  "pricing_suggestion": {
    "strategy": "市场对标 + 成本加成混合",
    "input_price_per_million": 1.71,
    "output_price_per_million": 2.48,
    "gross_margin": 0.6,
    "notes": "建议价=成本÷(1-目标毛利)；示例取整到分",
    "price_tiers_impact": {
      "cache_discount": {
        "discount": 0.1,
        "load_ratio": "3:1 输入输出",
        "scenarios": [
          {"hit_rate": 0.0, "blended_cost": 4.27, "blended_price": 10.67, "margin": 0.6},
          {"hit_rate": 0.5, "blended_cost": 3.26, "blended_price": 7.99, "margin": 0.59},
          {"hit_rate": 0.7, "blended_cost": 2.86, "blended_price": 6.92, "margin": 0.59}
        ],
        "conclusion": "命中率70%时综合报价降35%，毛利基本不掉——缓存是竞争力来源"
      },
      "batch_discount": {
        "discount": 0.5,
        "batch_price_per_million": 1.24,
        "load_from": 0.6,
        "load_to": 0.85,
        "output_gain": 0.42,
        "marginal_cost_ratio": 0.1,
        "incremental_gross_margin": 0.92,
        "conclusion": "填空闲产能的增量毛利率92%；利用率>90%时应取消批量折扣"
      },
      "package_check": {
        "package_price": 99,
        "included_tokens_million": 5,
        "unit_cost_per_million": 0.69,
        "margin_at_package_price": 0.965,
        "pass": true,
        "conclusion": "套餐单位经济成立，作用在现金流预收与客户锁定"
      }
    }
  },
  "market_comparison": {
    "verified": false,
    "verified_at": null,
    "source_urls": [],
    "deepseek_official": {"input_hit": null, "input_miss": null, "output": null},
    "claude_sonnet": {"input": null, "output": null},
    "gpt": {"input": null, "output": null},
    "notes": "示例未核验；运行时必须联网核验官方最新价并填来源，无法核验时保持null"
  },
  "risks": [
    "自动估算吞吐可能偏离实际，需压测校准",
    "显存检查依赖模型结构参数，需按实际部署校准",
    "利用率是最大变量，需按实际监控调整",
    "国产卡推理性能差异需实测",
    "市场价未核验，报价前需联网确认"
  ],
  "summary": "自动估算吞吐+显存检查通过；建议定价输入1.71元/百万token、输出2.48元/百万token，毛利率60%，市场价需核验后填写"
}
```

### 输出 2：HTML 一页式商业简报（面向老板/演示受众，与 JSON 同源同数据）

JSON 是给机器和你的，HTML 是给老板/演示看的。**HTML 由配套渲染脚本 `pricing_report.py` 从 JSON 生成（数字同源、禁止手填）**，与 token-reconciliation / token-review 的渲染脚本同一模式：

> **渲染器输入兼容两种结构（2026-08-17）**：① 直接喂 `cost_calculator.py` 的平铺输出——成本数字完整渲染，定价/市场对标/风险区块显示"待 AI 编排层补充"占位；② 喂 AI 编排层补齐 `cost_model`/`pricing_suggestion`/`market_comparison`/`risks`/`summary` 的完整结构——全区块渲染。两种都支持，不再丢数据。

```bash
# 1) 计算（产出 JSON，平铺结构）
python3 LLM-token-provider/token-cost-pricing/cost_calculator.py ... > test/LLM-token-provider/token_cost_pricing_output.json
# 2) 渲染 HTML（读 JSON 生成，数字同源；平铺或完整结构均可）
python3 LLM-token-provider/token-cost-pricing/pricing_report.py \
  --input test/LLM-token-provider/token_cost_pricing_output.json \
  --output test/LLM-token-provider/token_pricing_report_<YYYY-MM-DD>.html \
  --title "H100 × DeepSeek V4 Pro · Token 定价方案"
```

结构固定为 8 个区块（与 JSON 同目录，命名 `test/LLM-token-provider/token_pricing_report_<YYYY-MM-DD>.html`）：

1. **结论横幅**：一句话核心结论（赚不赚钱、能否竞争、最该做什么），用高亮 callout
2. **KPI 卡片**（顶部 3-4 个大数字）：输入/输出成本、建议售价、综合毛利
3. **成本结构**：有效成本 → 总成本（含 overhead）拆解，配小型示意
4. **定价体系**：建议输入/输出价 + 价格阶梯（缓存 0.1x、批量 50%、套餐校验 pass/fail）
5. **市场对标条形图**：我们的价 vs 竞品官方价（内联 SVG/CSS 条形，纯本地无外部依赖），必须标注核验日期与来源
6. **敏感度**：利用率 50%→90% 对输出成本/售价的影响（数据用脚本跑，不心算）
7. **风险清单**：按严重度红/黄/绿标注
8. **行动建议**：P0/P1 优先级

设计规范：
- 浅色主题、白底深字（与 IDE 主题一致），KPI 用大字号粗体
- 全中文，术语面向非技术决策者（写"每百万 token"，不写公式）
- 结论高亮、风险分色、数字统一 2 位小数
- 纯 HTML+CSS+内联 SVG，浏览器直接打开可演示，可打印为 PDF

---

## 数据真实性校验（每次运行必须执行）

1. **市场价格**：只允许来自官方定价页/官方 API 的实时数据，记录 `source_url` 和 `verified_at`；禁止凭训练记忆或推断输出
2. **模型与硬件规格**：凡不是用户提供的数据（吞吐、prefill/decode 效率、模型参数、显存估算）都必须标注为估算值，并附假设来源或压测计划
3. **行业事件**：降价/涨价/新模型发布等事实必须引用可访问的官方公告或权威来源并附链接；无法引用则写明“未核验”，不要写成事实
4. **有效期**：任何市场数据超过 7 天即视为过期，重新运行前必须重新核验
5. **无网兜底**：无法核验时 `verified=false`、价格字段为 `null`，并在 `risks` 中标注，不得伪造

---

## 注意事项

1. **区分事实与估算**：单卡成本/利用率/折旧/overhead 是输入参数；吞吐、prefill/decode 效率、token 成本是基于假设的**估算值**，必须明确标注，不能当作精确结果
2. **成本公式要清晰**：有效成本 = 成本 ÷ 利用率；总月成本 = 有效成本 × (1 + overhead)；单 token 成本 = 总月成本 ÷（吞吐 × 月秒数 × 负载折算）；建议价 = 成本 ÷（1 - 目标毛利），输出时给出中间值方便核对
3. **市场对标必须实时核验**：价格以官方页面为准，附来源和时间；不要使用训练数据中的旧价格或猜测价格
4. **行业动态以最新官方信息为准**：OpenAI/DeepSeek 调价、国产开源模型变化等会影响定价策略，但只有核验过的信息才能写进结论
5. **若信息不足**：给出合理的行业默认假设并在 notes 中说明，不要臆造
6. **套餐单位换算必查（血泪教训）**：中文"500 万 token"= 5 百万（million），不是 50 百万。写 `included_tokens_million` 前先换算：500万 → 5；并做 sanity check——套餐单价（元/百万）= 套餐价 ÷ 含 token 百万数，若该单价低于单 token 成本，套餐才可能亏钱。2026-08-14 曾因把 500 万写成 50 百万，误判 99 元套餐毛利 -115%（实际 +78.5%），务必避免

---

## 体系下一步（编排指引）

本 skill 是 Token 工厂商业化五件套的起点，后续按顺序调用：

```
A token-cost-pricing（本 skill）  怎么定价：成本 → 价格（产出预估毛利/预估成本/理论吞吐）
→ B token-billing                怎么算钱：计量 + 计费规则
→ C token-reconciliation         怎么对平：账单 + 对账 + 结算（产出真实账单应收）
→ D token-review                 真实赚不赚钱：真实毛利复盘 + 偏差诊断 + 调价建议
→ （人工确认）                    D 输出定价前后对比，经人工确认后才允许回炉
→ （回炉 A）                      调价/成本变化 → 重新运行本 skill（市场价>7天须重验）
```

- B 的价格字段直接引用本 skill 输出的 `pricing_suggestion`（数据契约，不重复输入）
- D 评审触发回炉时（竞品调价/利用率<60%/毛利偏离>10pt），**必须先经人工确认**（D 输出定价调整方案含前后对比），确认后才回到本 skill 重算——禁止自动直接改价
- 对账（C）发现计量缺陷时回流修正 B 的计量机制
