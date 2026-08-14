#!/usr/bin/env python3
"""
Token 工厂算力成本核算脚本（Skill A 配套工具）

功能：
  根据 GPU/模型参数、利用率、吞吐等，计算：
  - 单卡每月有效成本（含运营成本系数）
  - 吞吐估算（roofline：prefill 算力受限，decode 内存带宽受限）
  - 显存可行性检查（权重 + KV cache + 框架开销）
  - 单 token 成本（输入/输出，元/百万 token）

用法（命令行）：
  手动吞吐：
  python3 cost_calculator.py \
    --cost-mode monthly --cost 25000 --utilization 0.7 \
    --input-tps 8000 --output-tps 4000 --load-factor 0.6

  自动估算吞吐：
  python3 cost_calculator.py \
    --cost-mode monthly --cost 25000 --utilization 0.7 \
    --params-billion 8 --bytes-per-param 2 \
    --num-layers 32 --num-kv-heads 8 --head-dim 128 \
    --avg-prompt-tokens 2048 --avg-output-tokens 512 \
    --memory-bandwidth-gbps 3350 --peak-tflops 989 \
    --vram-gb 80

  一次性采购价：
  python3 cost_calculator.py \
    --cost-mode one-time --cost 200000 --depreciation 3 \
    --utilization 0.7 --input-tps 8000 --output-tps 4000
"""

import argparse
import json
import math
import sys

# 行业常见默认值（可被命令行覆盖）
DEFAULT_LOAD_FACTOR = 0.6       # 实际负载折算：非 100% 时间都打满吞吐
DEFAULT_OVERHEAD_RATIO = 0.2    # 电费/带宽/存储/集群/运维等非硬件成本系数
DEFAULT_PREFILL_MFU = 0.65      # prefill 阶段模型 FLOPS 利用率（vLLM 常见值）
DEFAULT_DECODE_EFFICIENCY = 0.80  # decode 阶段内存带宽利用率
DEFAULT_BATCH_EFFICIENCY = 0.90   # 高 batch 下聚合吞吐效率
DEFAULT_FRAMEWORK_OVERHEAD_GB = 2.0  # 推理框架/激活等额外显存开销
DAYS_PER_MONTH = 30
HOURS_PER_DAY = 24


def _monthly_cost(cost: float, cost_mode: str, depreciation: float) -> float:
    """把输入成本换算为单卡每月成本（元/月）"""
    if cost_mode == "monthly":
        return cost
    elif cost_mode == "one-time":
        # 一次性采购 → 折旧 → 月成本
        return cost / depreciation / 12
    else:
        raise ValueError(f"未知 cost_mode: {cost_mode}，应为 monthly 或 one-time")


def _estimate_throughput(
    params_billion: float,
    active_params_billion: float,
    bytes_per_param: float,
    memory_bandwidth_gbps: float,
    peak_tflops: float,
    prefill_mfu: float,
    decode_efficiency: float,
    batch_efficiency: float,
    batch_size: int | None,
) -> tuple[float, float, int, float, dict]:
    """用 roofline 模型估算 prefill/decode 吞吐，返回 (input_tps, output_tps, batch, per_seq_tps, details)"""
    params_total = params_billion * 1e9
    params_active = active_params_billion * 1e9

    # prefill 是算力受限：tokens/s = GPU_FLOPS × MFU / (2 × active_params)
    prefill_tps = (peak_tflops * 1e12 * prefill_mfu) / (2 * params_active)

    # decode 是内存带宽受限（单序列）：tokens/s = 内存带宽 / 激活权重字节 × 效率
    active_weights_bytes = params_active * bytes_per_param
    decode_tps_per_seq = (memory_bandwidth_gbps * 1e9 / active_weights_bytes) * decode_efficiency

    details = {
        "params_billion": params_billion,
        "active_params_billion": active_params_billion,
        "bytes_per_param": bytes_per_param,
        "prefill_mfu": prefill_mfu,
        "decode_efficiency": decode_efficiency,
        "decode_tokens_per_sec_per_seq": round(decode_tps_per_seq, 2),
        "notes": "prefill=算力受限；decode=内存带宽受限；吞吐为估算值，需压测校准",
    }
    return prefill_tps, decode_tps_per_seq, batch_size or 1, decode_tps_per_seq, details


def calculate_token_cost(
    cost: float,
    cost_mode: str,
    utilization: float,
    input_tps: float | None = None,
    output_tps: float | None = None,
    depreciation: float = 3.0,
    load_factor: float = DEFAULT_LOAD_FACTOR,
    overhead_ratio: float = DEFAULT_OVERHEAD_RATIO,
    gpu_type: str = "",
    model: str = "",
    # 自动估算吞吐所需参数
    params_billion: float | None = None,
    active_params_billion: float | None = None,
    bytes_per_param: float = 2.0,
    memory_bandwidth_gbps: float | None = None,
    peak_tflops: float | None = None,
    prefill_mfu: float = DEFAULT_PREFILL_MFU,
    decode_efficiency: float = DEFAULT_DECODE_EFFICIENCY,
    batch_efficiency: float = DEFAULT_BATCH_EFFICIENCY,
    batch_size: int | None = None,
    num_layers: int | None = None,
    num_kv_heads: int | None = None,
    head_dim: int | None = None,
    avg_prompt_tokens: int = 2048,
    avg_output_tokens: int = 512,
    kv_bytes_per_param: float = 2.0,
    vram_gb: float | None = None,
    framework_overhead_gb: float = DEFAULT_FRAMEWORK_OVERHEAD_GB,
) -> dict:
    """
    计算 Token 工厂算力成本。

    手动模式：直接提供 input_tps / output_tps。
    自动模式：提供模型与硬件参数，用 roofline 模型估算吞吐，并做显存可行性检查。
    """
    if cost <= 0:
        raise ValueError(f"cost 必须大于 0，收到 {cost}")
    if not (0 < utilization <= 1):
        raise ValueError(f"utilization 必须在 (0,1] 区间，收到 {utilization}")
    if not (0 < load_factor <= 1):
        raise ValueError(f"load_factor 必须在 (0,1] 区间，收到 {load_factor}")
    if overhead_ratio < 0:
        raise ValueError(f"overhead_ratio 不能小于 0，收到 {overhead_ratio}")

    manual = input_tps is not None or output_tps is not None
    if manual and (input_tps is None or output_tps is None):
        raise ValueError("手动吞吐模式需要同时提供 --input-tps 和 --output-tps")
    if manual and (input_tps <= 0 or output_tps <= 0):
        raise ValueError("input_tps 和 output_tps 必须大于 0")

    monthly = _monthly_cost(cost, cost_mode, depreciation)
    effective_cost = monthly / utilization
    total_monthly_cost = effective_cost * (1 + overhead_ratio)
    seconds_per_month = DAYS_PER_MONTH * HOURS_PER_DAY * 3600
    effective_seconds = seconds_per_month * load_factor

    vram_check = None
    if manual:
        throughput_estimate = {
            "mode": "manual",
            "input_tokens_per_sec": input_tps,
            "output_tokens_per_sec": output_tps,
            "notes": "手动吞吐，需实际压测校准",
        }
    else:
        if params_billion is None or memory_bandwidth_gbps is None or peak_tflops is None:
            raise ValueError("自动估算吞吐需要 --params-billion、--memory-bandwidth-gbps、--peak-tflops")
        if params_billion <= 0 or memory_bandwidth_gbps <= 0 or peak_tflops <= 0:
            raise ValueError("params_billion / memory_bandwidth_gbps / peak_tflops 必须大于 0")
        if bytes_per_param <= 0:
            raise ValueError(f"bytes_per_param 必须大于 0，收到 {bytes_per_param}")
        if not (0 < prefill_mfu <= 1) or not (0 < decode_efficiency <= 1) or not (0 < batch_efficiency <= 1):
            raise ValueError("prefill_mfu / decode_efficiency / batch_efficiency 必须在 (0,1] 区间")

        active_params_billion = active_params_billion or params_billion
        prefill_tps, decode_tps_per_seq, default_batch, _, estimate_details = _estimate_throughput(
            params_billion=params_billion,
            active_params_billion=active_params_billion,
            bytes_per_param=bytes_per_param,
            memory_bandwidth_gbps=memory_bandwidth_gbps,
            peak_tflops=peak_tflops,
            prefill_mfu=prefill_mfu,
            decode_efficiency=decode_efficiency,
            batch_efficiency=batch_efficiency,
            batch_size=batch_size,
        )

        effective_batch = default_batch
        weights_gb = params_billion * 1e9 * bytes_per_param / 1e9
        if vram_gb is not None:
            if num_layers is None or num_kv_heads is None or head_dim is None:
                vram_check = {
                    "status": "not_calculated",
                    "reason": "缺少 --num-layers / --num-kv-heads / --head-dim，无法计算 KV cache",
                }
            else:
                kv_per_seq_gb = (
                    2 * num_layers * num_kv_heads * head_dim
                    * (avg_prompt_tokens + avg_output_tokens) * kv_bytes_per_param
                ) / 1e9
                free_vram = vram_gb - weights_gb - framework_overhead_gb
                max_batch_by_vram = max(1, math.floor(free_vram / kv_per_seq_gb)) if kv_per_seq_gb > 0 else 1
                effective_batch = min(batch_size or max_batch_by_vram, max_batch_by_vram)
                total_vram_required = weights_gb + kv_per_seq_gb * effective_batch + framework_overhead_gb
                vram_check = {
                    "weights_gb": round(weights_gb, 2),
                    "kv_cache_per_seq_gb": round(kv_per_seq_gb, 3),
                    "effective_batch": effective_batch,
                    "kv_cache_total_gb": round(kv_per_seq_gb * effective_batch, 2),
                    "framework_overhead_gb": framework_overhead_gb,
                    "total_vram_required_gb": round(total_vram_required, 2),
                    "vram_gb": vram_gb,
                    "fits": total_vram_required <= vram_gb,
                }

        input_tps = prefill_tps
        output_tps = decode_tps_per_seq * effective_batch * batch_efficiency
        throughput_estimate = {
            "mode": "estimated",
            "input_tokens_per_sec": round(input_tps, 2),
            "output_tokens_per_sec": round(output_tps, 2),
            "effective_batch": effective_batch,
            **estimate_details,
        }

    input_cost_per_million = total_monthly_cost / (input_tps * effective_seconds) * 1_000_000
    output_cost_per_million = total_monthly_cost / (output_tps * effective_seconds) * 1_000_000

    return {
        "gpu_type": gpu_type,
        "model": model,
        "cost_mode": cost_mode,
        "single_card_monthly_cost": round(monthly, 2),
        "utilization": utilization,
        "effective_card_cost_per_month": round(effective_cost, 2),
        "overhead_ratio": overhead_ratio,
        "total_monthly_cost": round(total_monthly_cost, 2),
        "load_factor": load_factor,
        "throughput_estimate": throughput_estimate,
        "token_cost": {
            "input_cost_per_million": round(input_cost_per_million, 4),
            "output_cost_per_million": round(output_cost_per_million, 4),
            "notes": "输入成本按 prefill 吞吐、输出成本按 decode 聚合吞吐计算；估算值需压测校准",
        },
        "vram_check": vram_check,
        "notes": "吞吐为估算值；总成本含非硬件成本系数；实际定价需结合官方市场价核验",
    }


def _main(argv=None):
    parser = argparse.ArgumentParser(description="Token 工厂算力成本核算")
    parser.add_argument("--gpu-type", default="", help="GPU 类型（不参与计算，仅输出标注）")
    parser.add_argument("--model", default="", help="模型名（不参与计算，仅输出标注）")
    parser.add_argument("--cost-mode", choices=["monthly", "one-time"], default="monthly", help="成本模式")
    parser.add_argument("--cost", type=float, required=True, help="单卡成本（元）")
    parser.add_argument("--utilization", type=float, required=True, help="利用率（0-1）")
    parser.add_argument("--depreciation", type=float, default=3.0, help="折旧期（年，仅 one-time）")
    parser.add_argument("--load-factor", type=float, default=DEFAULT_LOAD_FACTOR, help="负载折算（默认0.6）")
    parser.add_argument("--overhead-ratio", type=float, default=DEFAULT_OVERHEAD_RATIO, help="非硬件成本系数（默认0.2）")
    parser.add_argument("--input-tps", type=float, default=None, help="输入吞吐 token/秒（手动模式）")
    parser.add_argument("--output-tps", type=float, default=None, help="输出吞吐 token/秒（手动模式）")
    parser.add_argument("--params-billion", type=float, default=None, help="模型总参数量（十亿，自动估算）")
    parser.add_argument("--active-params-billion", type=float, default=None, help="MoE 激活参数量（十亿，缺省=总参数量）")
    parser.add_argument("--bytes-per-param", type=float, default=2.0, help="权重精度字节数（FP16=2, INT8=1, INT4=0.5）")
    parser.add_argument("--memory-bandwidth-gbps", type=float, default=None, help="GPU 内存带宽 GB/s（自动估算）")
    parser.add_argument("--peak-tflops", type=float, default=None, help="GPU FP16/BF16 峰值 TFLOPS（自动估算）")
    parser.add_argument("--prefill-mfu", type=float, default=DEFAULT_PREFILL_MFU, help="prefill MFU（默认0.65）")
    parser.add_argument("--decode-efficiency", type=float, default=DEFAULT_DECODE_EFFICIENCY, help="decode 内存带宽效率（默认0.8）")
    parser.add_argument("--batch-efficiency", type=float, default=DEFAULT_BATCH_EFFICIENCY, help="高 batch 聚合效率（默认0.9）")
    parser.add_argument("--batch-size", type=int, default=None, help="目标并发/批大小；缺省按显存余量自动填满")
    parser.add_argument("--num-layers", type=int, default=None, help="模型层数（显存检查）")
    parser.add_argument("--num-kv-heads", type=int, default=None, help="KV heads（显存检查）")
    parser.add_argument("--head-dim", type=int, default=None, help="head 维度（显存检查）")
    parser.add_argument("--avg-prompt-tokens", type=int, default=2048, help="平均输入 token 数")
    parser.add_argument("--avg-output-tokens", type=int, default=512, help="平均输出 token 数")
    parser.add_argument("--kv-bytes-per-param", type=float, default=2.0, help="KV cache 每元素字节数（默认2）")
    parser.add_argument("--vram-gb", type=float, default=None, help="单卡显存 GB（显存检查）")
    parser.add_argument("--framework-overhead-gb", type=float, default=DEFAULT_FRAMEWORK_OVERHEAD_GB, help="框架/激活额外显存 GB（默认2）")
    args = parser.parse_args(argv)

    try:
        result = calculate_token_cost(
            cost=args.cost,
            cost_mode=args.cost_mode,
            utilization=args.utilization,
            input_tps=args.input_tps,
            output_tps=args.output_tps,
            depreciation=args.depreciation,
            load_factor=args.load_factor,
            overhead_ratio=args.overhead_ratio,
            gpu_type=args.gpu_type,
            model=args.model,
            params_billion=args.params_billion,
            active_params_billion=args.active_params_billion,
            bytes_per_param=args.bytes_per_param,
            memory_bandwidth_gbps=args.memory_bandwidth_gbps,
            peak_tflops=args.peak_tflops,
            prefill_mfu=args.prefill_mfu,
            decode_efficiency=args.decode_efficiency,
            batch_efficiency=args.batch_efficiency,
            batch_size=args.batch_size,
            num_layers=args.num_layers,
            num_kv_heads=args.num_kv_heads,
            head_dim=args.head_dim,
            avg_prompt_tokens=args.avg_prompt_tokens,
            avg_output_tokens=args.avg_output_tokens,
            kv_bytes_per_param=args.kv_bytes_per_param,
            vram_gb=args.vram_gb,
            framework_overhead_gb=args.framework_overhead_gb,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _main()
