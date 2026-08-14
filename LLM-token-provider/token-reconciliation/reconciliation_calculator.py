#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reconciliation_calculator.py - Token 平台对账与账单计算器（token-reconciliation Skill C 配套脚本）

固化以下规则（逻辑参照私有化 MaaS 平台对账&账单设计 + 行业财务标准）：
  1. 三源对账（request_id 为主键匹配）：
     - 源A（推理原始日志=真相源）有、源B（计量库）无  -> 漏记（进入补偿队列）
     - 源B 有、源A 无                               -> 异常记录（疑似重复/伪造，需人工核查）
     - 同一 request_id 在源B 出现多次                -> 重复上报（去重，多余作废）
     - 源A 与源B 均存在但 token 数不一致             -> 金额/用量不符（以源A 为准）
  2. 聚合指标对比：总条数、总输入 token、总输出 token（A vs B，计算差值）
  3. 差异率 = 差异笔数 ÷ 总笔数（分母 = 真相源 A 的总请求数）
  4. 阈值判定：差异率 < 容忍阈值 -> 微量差异（记录不修正）；>= 阈值 -> 超阈值（生成差异修正单）
  5. 调账汇总：adjust_amount = Σ(正向补收为 +，反向冲减为 -)
  6. 账单主表：final_amount = original_amount + adjust_amount（原始计算金额 + 调账净额）
  7. OOM 补记：卡时消耗 × 卡时单价 = 补记金额（不能 0 元）

金额全程 Decimal 定点（沿用 billing_calculator.py 的精度方案），禁止 float。

用法示例：
  python3 reconciliation_calculator.py \
    --raw-log test/LLM-token-provider/source_a.jsonl \
    --metering test/LLM-token-provider/source_b.jsonl \
    --adjust test/LLM-token-provider/adjustments.jsonl \
    --threshold 0.00005 \
    --gpu-second-price 0.05

输出：严格 JSON（对账结果 + 差异清单 + 调账汇总 + 账单主表）
"""

import argparse
import json
import sys
from collections import defaultdict
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


# ============================================================
# 对账核心
# ============================================================

def reconcile(raw_logs: list, metering: list, threshold: float,
              gpu_second_price: Decimal) -> dict:
    """三源对账 + 差异分类 + 差异率 + 调账 + 账单主表"""

    # ---- 索引（request_id -> 记录）----
    log_by_id = {}
    for rec in raw_logs:
        rid = rec["request_id"]
        # 源A 重复 request_id：异常（真相源不应重复），取第一条并标记
        if rid in log_by_id:
            log_by_id[rid].setdefault("_dup", True)
        else:
            log_by_id[rid] = dict(rec)
        log_by_id[rid].setdefault("_dup", False)

    meter_by_id = defaultdict(list)  # 同一 request_id 可能有多条（重复上报）
    for rec in metering:
        meter_by_id[rec["request_id"]].append(rec)

    # ---- 1. 逐笔匹配 ----
    matched = []      # A、B 均有
    missing = []      # 漏记：A 有 B 无
    orphan = []       # 异常：B 有 A 无
    duplicated = []   # 重复上报：B 中同 id 多条

    all_ids = set(log_by_id.keys()) | set(meter_by_id.keys())
    for rid in sorted(all_ids):
        in_a = rid in log_by_id
        in_b = rid in meter_by_id
        if in_a and in_b:
            log = log_by_id[rid]
            meter = meter_by_id[rid][0]  # 主记录取第一条
            # 用量不符检测（以真相源 A 为准）
            token_diff = False
            diff_detail = {}
            for dim, a_key, b_key in (("input", "prompt_tokens", "input"),
                                      ("output", "completion_tokens", "output"),
                                      ("cache", "cached_tokens", "cache_hit")):
                a_val = int(log.get(a_key, 0))
                b_val = int(meter.get("tokens", {}).get(b_key, 0)) if isinstance(meter.get("tokens"), dict) else 0
                if a_val != b_val:
                    token_diff = True
                    diff_detail[dim] = {"source_a": a_val, "source_b": b_val}
            if len(meter_by_id[rid]) > 1:
                duplicated.append({"request_id": rid, "count": len(meter_by_id[rid]),
                                   "action": "去重，多余记录作废"})
            matched.append({"request_id": rid,
                            "token_diff": token_diff,
                            "diff_detail": diff_detail if diff_detail else None,
                            "action": "一致" if not token_diff else "以源A为准，生成差异修正单"})
        elif in_a and not in_b:
            log = log_by_id[rid]
            missing.append({"request_id": rid,
                            "prompt_tokens": int(log.get("prompt_tokens", 0)),
                            "completion_tokens": int(log.get("completion_tokens", 0)),
                            "finish_reason": log.get("finish_reason", "unknown"),
                            "action": "漏记，进入补偿队列（MQ消息丢失）"})
        elif not in_a and in_b:
            orphan.append({"request_id": rid,
                           "count": len(meter_by_id[rid]),
                           "action": "源B有源A无，疑似重复/伪造，人工核查"})

    # ---- 2. 聚合指标对比（用原始行数，暴露重复上报的量级偏差）----
    a_total_req = len(log_by_id)
    b_total_req = len(metering)  # 原始入库行数（含重复上报），去重后为 len(meter_by_id)
    b_dedup_req = len(meter_by_id)
    a_in = sum(int(r.get("prompt_tokens", 0)) for r in log_by_id.values())
    a_out = sum(int(r.get("completion_tokens", 0)) for r in log_by_id.values())
    b_in = 0
    b_out = 0
    for r in metering:  # 原始行汇总（重复上报不合并，让差异可见）
        tok = r.get("tokens", {})
        if isinstance(tok, dict):
            b_in += int(tok.get("input", 0))
            b_out += int(tok.get("output", 0))

    # ---- 3. 差异率 ----
    diff_requests = len(missing) + len(orphan) + sum(1 for m in matched if m["token_diff"]) + len(duplicated)
    total_base = a_total_req if a_total_req > 0 else 1  # 分母 = 真相源 A 总请求数
    diff_rate = Decimal(diff_requests) / Decimal(total_base)
    threshold_dec = Decimal(str(threshold))
    if diff_rate == 0:
        verdict = "一致"
    elif diff_rate < threshold_dec:
        verdict = "微量差异（记录不修正，人工巡检）"
    else:
        verdict = "超阈值差异（生成差异修正单）"

    # ---- 4. OOM 补记（卡时 × 单价，不能 0 元）----
    oom_adjusts = []
    for rec in log_by_id.values():
        if rec.get("finish_reason") in ("oom", "error", "interrupted"):
            gpu_sec = D(rec.get("gpu_physical_seconds", 0))
            if gpu_sec > 0:
                amt = money(gpu_sec * gpu_second_price)
                oom_adjusts.append({"request_id": rec["request_id"],
                                    "gpu_physical_seconds": float(gpu_sec),
                                    "gpu_second_price": float(gpu_second_price),
                                    "adjust_amount": float(amt),
                                    "reason": "OOM/中断，按真实GPU卡时补记（不能0元）"})

    # ---- 5. 调账汇总（正向补收 +，反向冲减 -）----
    adjust_total = Decimal("0")
    adjust_detail = []
    for adj in oom_adjusts:  # 先并入 OOM 补记
        adjust_total += D(adj["adjust_amount"])
        adjust_detail.append(adj)

    # 外部调账记录（adjustments.jsonl 传入）
    external_adjusts = []
    for adj in _external_adjusts:
        amt = D(adj.get("amount", 0))  # 带符号：+补收 / -冲减
        adjust_total += amt
        external_adjusts.append({
            "adjust_id": adj.get("adjust_id"),
            "tenant_id": adj.get("tenant_id"),
            "request_id": adj.get("request_id"),
            "delta_amount": float(amt),
            "reason": adj.get("reason", ""),
        })
    adjust_detail.extend(external_adjusts)
    adjust_total = money(adjust_total)

    # ---- 6. 账单主表 ----
    original_amount = Decimal("0")
    total_in = 0
    total_out = 0
    for rec in metering:
        if not rec.get("charged", True):
            continue
        original_amount += D(rec["cost"]["total"])
        tok = rec.get("tokens", {})
        if isinstance(tok, dict):
            total_in += int(tok.get("input", 0))
            total_out += int(tok.get("output", 0))
    original_amount = money(original_amount)
    final_amount = money(original_amount + adjust_total)

    # ---- 7. 账单明细（bill_item）：按租户×模型×计费模式聚合计费明细，+ 调账记录子项 ----
    bill_items = []
    item_idx = 0
    from collections import defaultdict as _dd
    agg = _dd(lambda: {"input": 0, "output": 0, "cache": 0, "amount": Decimal("0")})
    agg_meta = {}
    for rec in metering:
        if not rec.get("charged", True):
            continue
        key = (rec.get("tenant_id", "unknown"), rec.get("model", "unknown"),
               rec.get("billing_mode", "按量"))
        tok = rec.get("tokens", {})
        if isinstance(tok, dict):
            agg[key]["input"] += int(tok.get("input", 0))
            agg[key]["output"] += int(tok.get("output", 0))
            agg[key]["cache"] += int(tok.get("cache_hit", 0))
        agg[key]["amount"] += D(rec["cost"]["total"])
        agg_meta[key] = rec
    for key, v in sorted(agg.items()):
        tenant_id, model, mode = key
        item_idx += 1
        bill_items.append({
            "item_no": f"ITEM-{item_idx:04d}",
            "item_type": "按token计费",
            "tenant_id": tenant_id,
            "model": model,
            "billing_mode": mode,
            "input_tokens": v["input"],
            "output_tokens": v["output"],
            "cache_tokens": v["cache"],
            "amount": float(money(v["amount"])),
            "remark": "计费明细聚合（可下钻 request_id）",
        })
    # 调账记录作为 bill_item 子项
    for adj in adjust_detail:
        item_idx += 1
        amt = adj.get("adjust_amount") if "adjust_amount" in adj else adj.get("delta_amount", 0)
        ref = adj.get("adjust_id") or adj.get("request_id") or "—"
        bill_items.append({
            "item_no": f"ITEM-{item_idx:04d}",
            "item_type": "调账记录",
            "tenant_id": adj.get("tenant_id", "—"),
            "model": "—",
            "billing_mode": "—",
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_tokens": 0,
            "amount": float(money(D(amt))),
            "remark": f"调账流水 {ref}：{adj.get('reason', '')}",
        })

    return {
        "reconciliation": {
            "match": {
                "matched": len(matched),
                "missing_leak": len(missing),
                "orphan_anomaly": len(orphan),
                "duplicated": len(duplicated),
            },
            "aggregate_comparison": {
                "request_count": {"source_a": a_total_req, "source_b_raw": b_total_req, "source_b_dedup": b_dedup_req, "diff_raw": a_total_req - b_total_req},
                "total_input_tokens": {"source_a": a_in, "source_b": b_in, "diff": a_in - b_in},
                "total_output_tokens": {"source_a": a_out, "source_b": b_out, "diff": a_out - b_out},
            },
            "difference_rate": float(diff_rate),
            "difference_rate_formula": f"差异笔数({diff_requests}) ÷ 真相源A总请求数({a_total_req})",
            "tolerance_threshold": threshold,
            "verdict": verdict,
        },
        "missing_queue": missing,
        "orphan_queue": orphan,
        "duplicated_list": duplicated,
        "token_mismatch_list": [m for m in matched if m["token_diff"]],
        "adjustments": adjust_detail,
        "adjust_total": float(adjust_total),
        "bill_main": {
            "total_token_in": total_in,
            "total_token_out": total_out,
            "original_amount": float(original_amount),
            "adjust_amount": float(adjust_total),
            "final_amount": float(final_amount),
            "formula": "final_amount = original_amount + adjust_amount",
            "bill_item_types": ["按token计费", "卡时消耗", "套餐固定费用", "调账记录"],
        },
        "bill_items": bill_items,
        "notes": "金额由 reconciliation_calculator.py 计算（Decimal 定点，非心算）；源A=推理原始日志(真相源)，源B=计量/计费明细(来自 token-billing)",
    }


# 全局变量：外部调账记录（由 main 注入）
_external_adjusts = []


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Token 对账与账单计算器（禁止心算，必须跑本脚本）")
    parser.add_argument("--raw-log", required=True, help="源A 推理原始日志 JSONL（真相源，含OOM）")
    parser.add_argument("--metering", required=True, help="源B 计量/计费明细 JSONL（来自 token-billing 输出）")
    parser.add_argument("--adjust", default=None, help="调账记录 JSONL（可选，amount 带符号：+补收/-冲减）")
    parser.add_argument("--threshold", type=float, default=0.00005,
                        help="差异容忍阈值（默认万分之0.5=0.00005）")
    parser.add_argument("--gpu-second-price", type=float, default=0.05,
                        help="GPU 卡时单价（元/卡秒，OOM 补记用）")
    args = parser.parse_args()

    global _external_adjusts
    raw_logs, metering = [], []
    for path in (args.raw_log, args.metering):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    (raw_logs if path == args.raw_log else metering).append(json.loads(line))

    if args.adjust:
        with open(args.adjust, "r", encoding="utf-8") as f:
            _external_adjusts = [json.loads(l) for l in f if l.strip()]

    result = reconcile(raw_logs, metering, args.threshold, D(args.gpu_second_price))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
