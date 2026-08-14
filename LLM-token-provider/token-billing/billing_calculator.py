#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
billing_calculator.py - Token 平台计量计费计算器（token-billing Skill B 配套脚本）

固化以下规则（全部来自官方文档核验，2026-08-14 verified）：
  1. 基础费用公式：费用 = input/M × input_price + output/M × output_price + cache/M × cache_price
  2. finish_reason 分支计费（Azure OpenAI 官方 FAQ：服务是否执行了处理）
     - stop / tool_calls / length / content_filter -> 按实际 token 收费
     - rate_limit (429) / auth_error (401)          -> 不计费（服务未执行处理）
     - client_disconnect                             -> 收输入 token（平台自定义策略，标注）
  3. 缓存识别：cached_tokens >= 该模型最小可缓存长度 才单独按缓存价计，否则并入 input 按输入价
     （最小可缓存长度按模型划分，Anthropic 官方：512/1024/2048/4096 不等）
  4. 批量折扣：batch=true 时费用 × 0.5（OpenAI 官方 Batch API 50%）
  5. 超长上下文加价（可选规则，OpenAI GPT-5.5 官方）：输入 > long_context_threshold 时全会话输入 × 2、输出 × 1.5
  6. 价格快照：每条明细写入计费当刻价格，调价不重算历史

用法示例（单笔）：
  python3 billing_calculator.py --prices '{"input":7.93,"output":18.87,"cache":0.79}'
    --usage '{"request_id":"req_001","tenant_id":"t_a","model":"deepseek-v4-pro",
              "prompt_tokens":1200,"completion_tokens":320,"cached_tokens":8000,
              "finish_reason":"stop","timestamp":"2026-08-14T13:30:00+08:00"}'

用法示例（批量文件，每行一个 JSON usage 对象）：
  python3 billing_calculator.py --prices '{"input":7.93,"output":18.87,"cache":0.79}'
    --usage-file test/LLM-token-provider/usages.jsonl --batch true

输出：严格 JSON（明细列表 + 汇总 + 规则应用记录）
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

# ============================================================
# 金额精度（重要）
# ============================================================
# 金额必须用 Decimal 定点计算，禁止 float（0.03965 在 float 中会变 0.03964999... 导致舍入错）。
# 行业标准：计费金额用 Decimal + ROUND_HALF_UP（四舍五入），保留 4 位小数（分以下 2 位 = 0.01 分）。
MONEY_QUANTUM = Decimal("0.0001")


def D(x) -> Decimal:
    """安全转 Decimal：优先从字符串构造，避免二进制浮点误差"""
    if isinstance(x, Decimal):
        return x
    if isinstance(x, float):
        return Decimal(str(x))
    return Decimal(str(x))


def money(v) -> Decimal:
    """金额舍入到 4 位小数（四舍五入）"""
    return D(v).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)

# ============================================================
# 配置表（来自官方文档核验，非推断）
# ============================================================

# Anthropic 官方：各模型最小可缓存长度（prompt caching minimum cacheable length）
# 来源: https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching (verified 2026-08-14)
CACHE_MIN_TOKENS = {
    "claude-opus-5": 512, "claude-fable-5": 512, "claude-mythos-5": 512,
    "claude-mythos-preview": 2048, "claude-opus-4.7": 2048,
    "claude-opus-4.6": 4096, "claude-opus-4.5": 4096, "claude-haiku-4.5": 4096,
    "claude-opus-4.8": 1024, "claude-sonnet-5": 1024, "claude-sonnet-4.6": 1024,
    "claude-sonnet-4.5": 1024, "claude-opus-4.1": 1024, "claude-opus-4": 1024,
    "claude-sonnet-4": 1024, "claude-haiku-3.5": 2048,
    # 未知模型默认 1024：最保守，避免小请求享受缓存价导致亏损
    "default": 1024,
}

# finish_reason 计费分支
# key: finish_reason 值 -> (是否收输入, 是否收输出)
# 依据 Azure OpenAI 官方 FAQ：服务执行了处理即收费；429/401 服务未执行处理不收费
# 来源: https://learn.microsoft.com/zh-hk/azure/foundry-classic/openai/faq (verified 2026-08-14)
FINISH_REASON_RULES = {
    "stop":            {"charge_input": True,  "charge_output": True,  "rule": "正常完成，按实际token全收"},
    "tool_calls":      {"charge_input": True,  "charge_output": True,  "rule": "工具调用完成，按实际token全收"},
    "length":          {"charge_input": True,  "charge_output": True,  "rule": "达max_tokens截断，按实际token全收"},
    "content_filter":  {"charge_input": True,  "charge_output": True,  "rule": "内容被过滤，仍收费(Azure官方确认)"},
    "rate_limit":      {"charge_input": False, "charge_output": False, "rule": "429未执行处理，不计费(Azure官方)"},
    "auth_error":      {"charge_input": False, "charge_output": False, "rule": "401未执行处理，不计费(Azure官方)"},
    "client_disconnect": {"charge_input": True, "charge_output": False, "rule": "客户端中断，收输入token(prefill已发生，平台自定义)"},
}

# 批量折扣（OpenAI 官方 Batch API 50%）
BATCH_DISCOUNT = 0.5

# ============================================================
# 核心计算函数
# ============================================================

def get_cache_min_tokens(model: str) -> int:
    """取模型最小可缓存长度；未知模型用 default=1024"""
    return CACHE_MIN_TOKENS.get(model, CACHE_MIN_TOKENS["default"])


def compute_fee(usage: dict, prices: dict, billing_mode: str = "按量",
                long_context_threshold: int = None, batch: bool = False,
                verify_time: str = None) -> dict:
    """
    计算单笔请求费用。返回费用明细 dict（含 price_snapshot 与 applied_rules）。
    禁止心算：所有金额均由本函数计算并保留 4 位小数。
    """
    request_id = usage.get("request_id", "unknown")
    tenant_id = usage.get("tenant_id", "unknown")
    model = usage.get("model", "unknown")
    prompt_tokens = int(usage.get("prompt_tokens", 0))
    completion_tokens = int(usage.get("completion_tokens", 0))
    cached_tokens = int(usage.get("cached_tokens", 0))
    finish_reason = usage.get("finish_reason", "stop")
    error_code = usage.get("error_code")  # 429/401 等 HTTP 错误码
    timestamp = usage.get("timestamp", verify_time or datetime.now(timezone.utc).isoformat())

    # 价格取数（元/百万token），缺省 0，Decimal 定点
    input_price = D(prices.get("input", 0))
    output_price = D(prices.get("output", 0))
    cache_price = D(prices.get("cache", 0))

    applied_rules = []

    # ---- 规则 0：错误码直接判定（服务未执行处理则不收费）----
    if error_code in ("429", "401"):
        return {
            "billing_record_id": None,
            "request_id": request_id,
            "tenant_id": tenant_id,
            "model": model,
            "billing_mode": billing_mode,
            "charged": False,
            "reason": f"error_code={error_code}，服务未执行处理，不计费",
            "tokens": {"input": prompt_tokens, "output": completion_tokens, "cache_hit": cached_tokens},
            "cost": {"input": 0.0, "output": 0.0, "cache_hit": 0.0, "total": 0.0, "currency": "CNY"},
            "price_snapshot": {"input_price_per_million": float(input_price),
                               "output_price_per_million": float(output_price),
                               "cache_hit_price_per_million": float(cache_price),
                               "snapshot_at": timestamp},
            "applied_rules": [f"error_{error_code}_not_charged"],
            "billing_period": usage.get("billing_period"),
            "created_at": verify_time or timestamp,
        }

    # ---- 规则 1：缓存识别（cached_tokens >= 模型最小可缓存长度才单独计）----
    cache_min = get_cache_min_tokens(model)
    if cached_tokens >= cache_min:
        cache_charged_tokens = cached_tokens
        prompt_charged = prompt_tokens
        applied_rules.append(f"cache_recognized(min={cache_min})")
    else:
        # 未达门槛：缓存 token 并入输入，按输入价计（避免小请求享受缓存价导致亏损）
        cache_charged_tokens = 0
        prompt_charged = prompt_tokens + cached_tokens
        applied_rules.append(f"cache_below_min({cache_min})_merged_to_input")

    # ---- 规则 2：finish_reason 分支（是否收费、收哪些维度）----
    rule = FINISH_REASON_RULES.get(finish_reason)
    if rule is None:
        # 未知 finish_reason：保守全收（宁可多收，不可漏收），标注需人工确认
        rule = {"charge_input": True, "charge_output": True,
                "rule": f"未知 finish_reason={finish_reason}，保守全收，需人工确认"}
        applied_rules.append("unknown_finish_reason_conservative")
    else:
        applied_rules.append(f"finish_reason_{finish_reason}")

    # 完全不计费的分支（rate_limit/auth_error）：charged=False，与 error_code 语义一致
    if not rule["charge_input"] and not rule["charge_output"]:
        return {
            "billing_record_id": None,
            "request_id": request_id,
            "tenant_id": tenant_id,
            "model": model,
            "billing_mode": billing_mode,
            "charged": False,
            "reason": f"finish_reason={finish_reason}，{rule['rule']}",
            "tokens": {"input": prompt_tokens, "output": completion_tokens, "cache_hit": cached_tokens},
            "cost": {"input": 0.0, "output": 0.0, "cache_hit": 0.0, "total": 0.0, "currency": "CNY"},
            "price_snapshot": {"input_price_per_million": float(input_price),
                               "output_price_per_million": float(output_price),
                               "cache_hit_price_per_million": float(cache_price),
                               "snapshot_at": timestamp},
            "applied_rules": applied_rules,
            "billing_period": usage.get("billing_period"),
            "created_at": verify_time or timestamp,
        }

    # ---- 规则 3：超长上下文加价（可选；OpenAI GPT-5.5 官方：>272K 全会话输入×2 输出×1.5）----
    input_multiplier, output_multiplier = D(1), D(1)
    if long_context_threshold is not None and prompt_tokens > long_context_threshold:
        input_multiplier, output_multiplier = D(2), D("1.5")
        applied_rules.append(f"long_context_surcharge(>{long_context_threshold},in_x2,out_x1.5)")

    # ---- 计算（单位：token -> 百万 -> 元；全程 Decimal 定点）----
    per_million = D("1000000")
    input_cost = (D(prompt_charged) / per_million) * input_price * input_multiplier if rule["charge_input"] else D(0)
    output_cost = (D(completion_tokens) / per_million) * output_price * output_multiplier if rule["charge_output"] else D(0)
    cache_cost = (D(cache_charged_tokens) / per_million) * cache_price if rule["charge_input"] else D(0)

    # ---- 规则 4：批量折扣 ----
    if batch:
        input_cost *= D(BATCH_DISCOUNT)
        output_cost *= D(BATCH_DISCOUNT)
        cache_cost *= D(BATCH_DISCOUNT)
        applied_rules.append(f"batch_discount_x{BATCH_DISCOUNT}")

    # 分段与合计均舍入到 4 位小数（分以下 2 位）
    input_cost = money(input_cost)
    output_cost = money(output_cost)
    cache_cost = money(cache_cost)
    total_cost = money(input_cost + output_cost + cache_cost)

    return {
        "billing_record_id": f"bill_{request_id}",
        "request_id": request_id,
        "tenant_id": tenant_id,
        "model": model,
        "billing_mode": billing_mode,
        "charged": True,
        "tokens": {"input": prompt_tokens, "output": completion_tokens, "cache_hit": cached_tokens,
                   "charged_input": prompt_charged, "charged_cache": cache_charged_tokens},
        "cost": {"input": float(input_cost), "output": float(output_cost),
                 "cache_hit": float(cache_cost), "total": float(total_cost), "currency": "CNY"},
        "price_snapshot": {"input_price_per_million": float(input_price),
                           "output_price_per_million": float(output_price),
                           "cache_hit_price_per_million": float(cache_price),
                           "snapshot_at": timestamp},
        "applied_rules": applied_rules,
        "billing_period": usage.get("billing_period"),
        "created_at": verify_time or timestamp,
    }


def summarize(results: list) -> dict:
    """汇总：总量、总费用、分模式汇总（Decimal 累加，避免浮点误差）"""
    charged = [r for r in results if r.get("charged")]
    total_cost = money(sum(D(r["cost"]["total"]) for r in charged))
    total_input_tokens = sum(r["tokens"]["input"] for r in charged)
    total_output_tokens = sum(r["tokens"]["output"] for r in charged)
    total_cache_tokens = sum(r["tokens"]["cache_hit"] for r in charged)

    by_mode = {}
    for r in charged:
        m = r["billing_mode"]
        by_mode.setdefault(m, {"records": 0, "total_cost": 0.0})
        by_mode[m]["records"] += 1
        by_mode[m]["total_cost"] = float(money(D(by_mode[m]["total_cost"]) + D(r["cost"]["total"])))

    not_charged = [r for r in results if not r.get("charged")]

    return {
        "total_records": len(results),
        "charged_records": len(charged),
        "not_charged_records": len(not_charged),
        "not_charged_reasons": [r["reason"] for r in not_charged],
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_cache_tokens": total_cache_tokens,
        "total_cost": float(total_cost),
        "cost_by_billing_mode": by_mode,
    }


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Token 平台计量计费计算器（禁止心算，费用必须跑本脚本）")
    parser.add_argument("--prices", required=True,
                        help='价格 JSON：{"input":7.93,"output":18.87,"cache":0.79}（元/百万token）')
    parser.add_argument("--usage", help="单笔用量 JSON")
    parser.add_argument("--usage-file", help="批量用量文件（JSONL，每行一个 JSON 对象）")
    parser.add_argument("--batch", action="store_true", help="标记为批量任务（费用×0.5）")
    parser.add_argument("--billing-mode", default="按量", help="计费模式：按量/包周期/批量")
    parser.add_argument("--long-context-threshold", type=int, default=None,
                        help="超长上下文加价阈值（输入 token 数），默认不启用")
    parser.add_argument("--verify-time", default=datetime.now(timezone.utc).isoformat(),
                        help="核验/计费时间（ISO 8601），用于 price_snapshot")
    args = parser.parse_args()

    try:
        prices = json.loads(args.prices)
    except json.JSONDecodeError:
        print(json.dumps({"error": "prices 不是合法 JSON"}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)

    usages = []
    if args.usage:
        try:
            usages.append(json.loads(args.usage))
        except json.JSONDecodeError:
            print(json.dumps({"error": "usage 不是合法 JSON"}, ensure_ascii=False), file=sys.stderr)
            sys.exit(1)
    if args.usage_file:
        with open(args.usage_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    usages.append(json.loads(line))

    if not usages:
        print(json.dumps({"error": "必须提供 --usage 或 --usage-file"}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)

    results = []
    for u in usages:
        batch = args.batch or str(u.get("batch", "false")).lower() == "true"
        results.append(compute_fee(u, prices, billing_mode=args.billing_mode,
                                   long_context_threshold=args.long_context_threshold,
                                   batch=batch, verify_time=args.verify_time))

    output = {
        "fee_details": results,
        "summary": summarize(results),
        "notes": "费用由 billing_calculator.py 计算（非心算）；规则来源见脚本头部注释；价格来自 token-cost-pricing 或官方核验",
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
