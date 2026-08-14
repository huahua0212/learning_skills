#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
review_calculator.py - Token 工厂商业化评审计算器（token-review Skill D 配套脚本）

固化以下规则（公式与 cost_calculator.py 口径完全一致，禁止心算）：
  1. 真实成本（复用 A 的公式，用真实利用率/真实吞吐替代预估）：
     - 真实总月成本 = 单卡月成本 ÷ 真实利用率 × (1 + overhead_ratio)
     - 真实输入/输出成本 = 真实总月成本 ÷ (真实吞吐 × 月有效秒数) × 1e6
     - 月有效秒数 = 30 × 24 × 3600 × load_factor
  2. 五大偏差：
     - 单 Token 成本偏差率 = (真实成本 − 预估成本) ÷ 预估成本（比率）
     - 毛利率偏差 = 真实毛利率 − 目标毛利率（百分点 pt，注意不是比率）
     - 吞吐偏差率 = (真实吞吐 − 理论吞吐) ÷ 理论吞吐（比率）
     - 利用率偏差 = 真实利用率 − 预估利用率（百分点 pt）
     - 缓存命中率偏差 = 真实命中率 − 预估命中率（百分点 pt）
  3. 真实毛利率 = (真实收入 − 真实成本) ÷ 真实收入（收入来自 token-reconciliation bill_main.final_amount）
  4. 请求级毛利判定（结构竞争力）：
     - 单请求毛利 = Σ(用量×售价) − Σ(用量×真实成本)
     - 亏损单 = 毛利 < 0；结构健康度 = 负毛利请求数 ÷ 总请求数
  5. 调价建议（成本加成反推，为恢复目标毛利）：
     - 新售价 = 真实成本 ÷ (1 − 目标毛利)
     - 调整幅度 = (新售价 − 旧售价) ÷ 旧售价
  6. 人工确认铁律：本脚本只输出定价调整方案（前后对比），不自动改价——确认状态由外部流程控制

金额全程 Decimal 定点（与 billing_calculator.py 同一精度方案），禁止 float。

用法：
  python3 review_calculator.py \\
    --estimated test/LLM-token-provider/token_cost_pricing_output.json \\   # Skill A 输出（预估侧）
    --actual test/LLM-token-provider/review_actual_input.json \\            # 真实侧（B/C 产出，结构见下）
    --target-margin 0.6 \\
    --output test/LLM-token-provider/review_output.json

真实侧输入结构（--actual）：
{
  "single_card_monthly_cost": 25000,      # 单卡月成本（与 A 输入一致）
  "overhead_ratio": 0.2,                   # 与 A 输入一致
  "load_factor": 0.6,                      # 与 A 输入一致
  "actual_utilization": 0.38,              # 真实 GPU 利用率
  "actual_input_tps": 6000.0,              # 真实 prefill 吞吐
  "actual_output_tps": 1600.0,             # 真实 decode 聚合吞吐
  "actual_cache_hit_rate": 0.12,           # 真实缓存命中率
  "revenue_per_month": 300000.0,           # 真实收入（= C 的 bill_main.final_amount，月口径）
  "usage_structure": {                     # 真实用量结构（来自 B）
    "input_tokens": 120000000,
    "output_tokens": 40000000,
    "cache_tokens": 50000000
  }
}
"""

import argparse
import json
import sys
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

# ============================================================
# 金额精度（与 billing_calculator.py 一致）
# ============================================================
MONEY_QUANTUM = Decimal("0.0001")


def D(x) -> Decimal:
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x))


def money(v) -> Decimal:
    return D(v).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def pct_ratio(v) -> float:
    """比率偏差显示为百分比"""
    return float(v) * 100


# ============================================================
# 核心计算
# ============================================================

def compute_review(estimated: dict, actual: dict, target_margin: float) -> dict:
    """完整商业化评审计算。estimated=A 输出，actual=真实侧输入。"""

    # ---- 读取预估侧（Skill A 输出）----
    est_cost = estimated["token_cost"]
    est_input_cost = D(est_cost["input_cost_per_million"])
    est_output_cost = D(est_cost["output_cost_per_million"])
    est_utilization = D(estimated["cost_model"]["utilization"])
    est_throughput = estimated["throughput_estimate"]
    est_input_tps = D(est_throughput["input_tokens_per_sec"])
    est_output_tps = D(est_throughput["output_tokens_per_sec"])
    est_pricing = estimated["pricing_suggestion"]
    est_input_price = D(est_pricing["input_price_per_million"])
    est_output_price = D(est_pricing["output_price_per_million"])
    est_cache_price = D(est_pricing["price_tiers_impact"]["cache_discount"]
                        .get("cache_hit_price_per_million", est_input_price * D("0.1")))
    est_cache_hit_rate = D(actual.get("estimated_cache_hit_rate", 0.4))  # A 未输出时用真实侧传入的预估

    # ---- 读取真实侧 ----
    card_cost = D(actual["single_card_monthly_cost"])
    overhead = D(actual.get("overhead_ratio", 0.2))
    load_factor = D(actual.get("load_factor", 0.6))
    act_utilization = D(actual["actual_utilization"])
    act_input_tps = D(actual["actual_input_tps"])
    act_output_tps = D(actual["actual_output_tps"])
    act_cache_hit_rate = D(actual.get("actual_cache_hit_rate", 0))
    revenue = D(actual["revenue_per_month"])
    usage = actual.get("usage_structure", {})
    in_tok = D(usage.get("input_tokens", 0))
    out_tok = D(usage.get("output_tokens", 0))
    cache_tok = D(usage.get("cache_tokens", 0))

    # ---- 1. 真实成本（复用 cost_calculator.py 公式）----
    seconds_per_month = D(30) * D(24) * D(3600)
    effective_seconds = seconds_per_month * load_factor

    act_total_monthly_cost = (card_cost / act_utilization) * (D(1) + overhead)
    act_input_cost = act_total_monthly_cost / (act_input_tps * effective_seconds) * D("1000000")
    act_output_cost = act_total_monthly_cost / (act_output_tps * effective_seconds) * D("1000000")
    act_input_cost = money(act_input_cost)
    act_output_cost = money(act_output_cost)

    # ---- 2. 五大偏差 ----
    input_cost_dev = (act_input_cost - est_input_cost) / est_input_cost
    output_cost_dev = (act_output_cost - est_output_cost) / est_output_cost

    # 真实毛利率：真实收入 vs 真实总成本（月口径）
    act_margin = (revenue - act_total_monthly_cost) / revenue if revenue > 0 else D(0)
    target_margin_d = D(str(target_margin))
    margin_dev_pt = (act_margin - target_margin_d) * 100  # 百分点

    input_tps_dev = (act_input_tps - est_input_tps) / est_input_tps
    output_tps_dev = (act_output_tps - est_output_tps) / est_output_tps
    utilization_dev_pt = (act_utilization - est_utilization) * 100  # 百分点
    cache_hit_dev_pt = (act_cache_hit_rate - est_cache_hit_rate) * 100  # 百分点

    # ---- 3. 真实毛利（按真实用量结构加权混合成本）----
    total_tok = in_tok + out_tok + cache_tok
    if total_tok > 0:
        in_w = in_tok / total_tok
        out_w = out_tok / total_tok
        cache_w = cache_tok / total_tok
    else:
        in_w, out_w, cache_w = D("0.5"), D("0.5"), D("0")
    # 缓存命中时 prefill 成本≈0，仅剩 KV 读取带宽成本（Anthropic 缓存读取=0.1x 输入价，成本侧同理取输入成本 10%）
    act_cache_cost = act_input_cost * D("0.1")
    blended_act_cost = in_w * act_input_cost + out_w * act_output_cost + cache_w * act_cache_cost
    # 混合收入价（按售价加权）
    blended_price = in_w * est_input_price + out_w * est_output_price + cache_w * est_cache_price
    blended_margin = (blended_price - blended_act_cost) / blended_price if blended_price > 0 else D(0)

    # ---- 4. 请求级结构判定（用真实用量结构模拟租户/请求聚合）----
    # 每百万 token 的毛利（分别看输入/输出/缓存三类请求；缓存成本≠输入成本）
    per_margin = {
        "input": float(est_input_price - act_input_cost),
        "output": float(est_output_price - act_output_cost),
        "cache": float(est_cache_price - act_cache_cost),
    }
    # 结构健康度：负毛利占比（按 token 量加权）
    loss_tokens = D(0)
    if per_margin["input"] < 0:
        loss_tokens += in_tok
    if per_margin["output"] < 0:
        loss_tokens += out_tok
    if per_margin["cache"] < 0:
        loss_tokens += cache_tok
    loss_ratio = (loss_tokens / total_tok) if total_tok > 0 else D(0)
    structure_type = "亏损型结构" if per_margin["output"] < 0 or loss_ratio > D("0.3") else "盈利型结构"

    # ---- 5. 调价建议（成本加成反推，恢复目标毛利）----
    new_input_price = act_input_cost / (D(1) - target_margin_d)
    new_output_price = act_output_cost / (D(1) - target_margin_d)
    input_adj = (new_input_price - est_input_price) / est_input_price if est_input_price > 0 else D(0)
    output_adj = (new_output_price - est_output_price) / est_output_price if est_output_price > 0 else D(0)

    # ---- 6. 风险扫描触发 ----
    risks = []
    if act_margin < target_margin_d:
        risks.append({"risk": "毛利不达标", "severity": "高",
                      "detail": f"真实毛利率 {float(act_margin)*100:.1f}% < 目标 {float(target_margin_d)*100:.0f}%",
                      "suggestion": "上调输出单价（待人工确认）"})
    if output_tps_dev < D("-0.3"):
        risks.append({"risk": "吞吐衰减", "severity": "高",
                      "detail": f"真实输出吞吐 {float(act_output_tps):.0f} tok/s，比理论低 {abs(float(output_tps_dev)*100):.0f}%",
                      "suggestion": "优化调度/检查带宽瓶颈（国产卡高发）"})
    if act_utilization < D("0.5"):
        risks.append({"risk": "GPU利用率过低", "severity": "中",
                      "detail": f"真实利用率 {float(act_utilization)*100:.0f}%",
                      "suggestion": "批量任务填空闲、扩客户结构"})
    if per_margin["output"] < 0:
        risks.append({"risk": "输出侧负毛利", "severity": "高",
                      "detail": f"输出售价 {float(est_output_price)} < 真实输出成本 {float(act_output_cost)}",
                      "suggestion": "单独上调输出单价或限制超长生成"})
    if act_cache_hit_rate < D("0.2"):
        risks.append({"risk": "缓存命中率过低", "severity": "中",
                      "detail": f"真实命中率 {float(act_cache_hit_rate)*100:.0f}%",
                      "suggestion": "实现/优化 prefix caching，降低 prefill 成本"})

    return {
        "review_period": actual.get("period", datetime.now().strftime("%Y-%m")),
        "deviation_analysis": {
            "cost_deviation": {
                "input_cost": {"estimated": float(est_input_cost), "actual": float(act_input_cost),
                               "deviation_pct": float(input_cost_dev)},
                "output_cost": {"estimated": float(est_output_cost), "actual": float(act_output_cost),
                                "deviation_pct": float(output_cost_dev)},
                "formula": "真实成本复用 cost_calculator.py 口径：总月成本=单卡月成本÷真实利用率×(1+overhead)；单token成本=总月成本÷(真实吞吐×月有效秒数)×1e6"
            },
            "margin_deviation": {
                "actual_margin": float(act_margin),
                "target_margin": float(target_margin_d),
                "deviation_pt": float(margin_dev_pt),
                "blended_margin_by_usage_structure": float(blended_margin),
                "note": "毛利率偏差单位为百分点(pt)，非比率"
            },
            "throughput_deviation": {
                "input": {"estimated": float(est_input_tps), "actual": float(act_input_tps),
                          "deviation_pct": float(input_tps_dev)},
                "output": {"estimated": float(est_output_tps), "actual": float(act_output_tps),
                           "deviation_pct": float(output_tps_dev)}
            },
            "utilization_deviation_pt": float(utilization_dev_pt),
            "cache_hit_deviation_pt": float(cache_hit_dev_pt),
            "root_cause_summary": _root_cause(input_cost_dev, output_cost_dev, margin_dev_pt,
                                              output_tps_dev, utilization_dev_pt, cache_hit_dev_pt)
        },
        "real_cost": {
            "total_monthly_cost": float(act_total_monthly_cost),
            "input_cost_per_million": float(act_input_cost),
            "output_cost_per_million": float(act_output_cost),
            "formula": "有效成本=单卡月成本÷真实利用率；总月成本=有效成本×(1+overhead)；与 cost_calculator.py 一致"
        },
        "structure_competitiveness": {
            "per_million_margin": per_margin,
            "loss_token_ratio": float(loss_ratio),
            "structure_type": structure_type,
            "verdict": "亏损型结构" if structure_type == "亏损型结构" else "盈利型结构",
            "note": "单请求毛利=Σ(用量×售价)−Σ(用量×真实成本)"
        },
        "pricing_adjustment_plan": {
            "status": "待人工确认",
            "formula": "新售价 = 真实成本 ÷ (1 − 目标毛利)；调整幅度 = (新售价 − 旧售价) ÷ 旧售价",
            "before_after": [
                {"item": "输入单价", "before": float(est_input_price), "after": float(money(new_input_price)),
                 "change_pct": float(input_adj)},
                {"item": "输出单价", "before": float(est_output_price), "after": float(money(new_output_price)),
                 "change_pct": float(output_adj)},
                {"item": "缓存单价", "before": float(est_cache_price), "after": float(est_cache_price),
                 "change_pct": 0.0}
            ]
        },
        "risk_scan": risks,
        "manual_confirmation": {
            "required": True,
            "rule": "反向修正定价参数禁止自动直接修改：本脚本只输出定价调整方案（前后对比），须经人工确认后才回炉 token-cost-pricing 重算",
            "if_confirmed": "回炉 token-cost-pricing（市场价>7天重验）→ 同步 token-billing 计费规则",
            "if_rejected": "维持现价，记录驳回原因"
        },
        "notes": "全部计算由 review_calculator.py 执行（Decimal 定点，禁止心算）；预估侧来自 token-cost-pricing 输出，真实侧来自 token-billing/token-reconciliation",
    }


def _root_cause(input_cost_dev, output_cost_dev, margin_dev_pt, output_tps_dev, util_dev_pt, cache_dev_pt):
    parts = []
    if output_cost_dev > D("0.3"):
        parts.append(f"输出成本比预估高 {float(output_cost_dev)*100:.0f}%")
    if output_tps_dev < D("-0.3"):
        parts.append(f"输出吞吐比理论低 {abs(float(output_tps_dev)*100):.0f}%（带宽瓶颈）")
    if util_dev_pt < D("-20"):
        parts.append(f"利用率比预估低 {abs(float(util_dev_pt)):.0f}pt（摊薄成本上升）")
    if cache_dev_pt < D("-20"):
        parts.append(f"缓存命中率比预估低 {abs(float(cache_dev_pt)):.0f}pt（prefill 成本高）")
    if margin_dev_pt < 0:
        parts.append(f"毛利低于目标 {abs(float(margin_dev_pt)):.0f}pt")
    return "；".join(parts) if parts else "偏差在合理范围内"


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Token 工厂商业化评审计算器（禁止心算，必须跑本脚本）")
    parser.add_argument("--estimated", required=True, help="Skill A 输出 JSON（token_cost_pricing_output.json）")
    parser.add_argument("--actual", required=True, help="真实侧输入 JSON（B/C 产出，结构见脚本头部注释）")
    parser.add_argument("--target-margin", type=float, default=0.6, help="目标毛利率（缺省 0.6）")
    parser.add_argument("--period", default=None, help="评审账期，如 2026-08")
    parser.add_argument("--output", default=None, help="输出 JSON 路径（缺省打印到 stdout）")
    args = parser.parse_args()

    with open(args.estimated, "r", encoding="utf-8") as f:
        estimated = json.load(f)
    with open(args.actual, "r", encoding="utf-8") as f:
        actual = json.load(f)
    if args.period:
        actual["period"] = args.period

    result = compute_review(estimated, actual, args.target_margin)
    out = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"已输出: {args.output}")
    else:
        print(out)


if __name__ == "__main__":
    main()
