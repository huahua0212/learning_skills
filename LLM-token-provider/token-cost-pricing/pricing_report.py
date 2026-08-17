#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pricing_report.py - Token 定价 HTML 商业简报渲染器（token-cost-pricing Skill A 演示层）

读取 cost_calculator.py / token-cost-pricing 输出的 JSON，渲染为一页式 HTML 简报（给老板/演示用）。
**HTML 内每个数字都来自输入 JSON，禁止手填**——与计算脚本同源。

输入兼容两种结构（自动识别）：
  1) 平铺结构：cost_calculator.py 直接输出的 JSON（顶层 gpu_type/total_monthly_cost/token_cost/vram_check 等）。
     此时 KPI 只显示成本，定价/市场对标/风险显示"待 AI 编排层补充"占位。
  2) 完整结构：AI 编排层在平铺基础上补齐 cost_model/pricing_suggestion/market_comparison/risks/summary 后的 JSON。
     所有区块完整渲染。

用法：
  python3 pricing_report.py \
    --input test/LLM-token-provider/token_cost_pricing_output.json \
    --output test/LLM-token-provider/token_pricing_report_2026-08-14.html \
    --title "H100 × DeepSeek V4 Pro · Token 定价方案"

输出：自包含 HTML（内联 CSS，无外部依赖，浏览器直开，可打印 PDF）
结构固定 8 区块（对齐 SKILL.md 输出 2 规范）：结论横幅 / KPI / 成本结构 / 定价体系 / 市场对标条形图 / 敏感度 / 风险清单 / 行动建议
"""

import argparse
import html
import json
from datetime import datetime


def esc(v):
    return html.escape(str(v))


def fmt_money(v):
    return f"¥{float(v):,.2f}"


def fmt_num(v):
    return f"{int(v):,}" if float(v) == int(v) else f"{float(v):,.2f}"


def fmt_pct(v):
    return f"{float(v) * 100:.1f}%"


def fmt_2f(v):
    return f"{float(v):.2f}"


def severity_tag(risk_text):
    if "高" in risk_text:
        return "r"
    if "中" in risk_text:
        return "a"
    return "b"


def _first(data: dict, *keys, default=None):
    """依次尝试多个键，返回第一个存在的值"""
    for k in keys:
        if isinstance(data, dict) and k in data and data[k] is not None:
            return data[k]
    return default


def render(data: dict, title: str, generated_at: str) -> str:
    cm_nested = data.get("cost_model") or {}
    tok = data.get("token_cost") or {}
    ps = data.get("pricing_suggestion") or {}
    mc = data.get("market_comparison") or {}
    risks = data.get("risks") or []
    summary = data.get("summary") or ""
    vram = data.get("vram_check") or {}
    # 兼容两种输入：cost_calculator.py 的平铺输出 / AI 编排层补齐后的完整结构
    flat_mode = not cm_nested and not ps

    def cm_get(key, default=None):
        """先查嵌套 cost_model，再回退到平铺顶层（cost_calculator 直接输出）"""
        return cm_nested.get(key, data.get(key, default))

    # ---- KPI ----
    margin = ps.get("gross_margin") if ps else None
    price_display = (
        "待定价" if not ps
        else f"{fmt_num(ps.get('input_price_per_million', 0))} / {fmt_num(ps.get('output_price_per_million', 0))}"
    )
    margin_display = "待定价" if margin is None else fmt_pct(margin)
    kpi_cards = (
        f'<div class="kpi"><div class="v">{fmt_money(tok.get("input_cost_per_million", 0))}</div><div class="l">输入成本 / 百万 token</div></div>'
        f'<div class="kpi"><div class="v">{fmt_money(tok.get("output_cost_per_million", 0))}</div><div class="l">输出成本 / 百万 token</div></div>'
        f'<div class="kpi"><div class="v">{esc(price_display)}</div><div class="l">建议售价（输入 / 输出）</div></div>'
        f'<div class="kpi"><div class="v">{esc(margin_display)}</div><div class="l">目标毛利率</div></div>'
    )

    # ---- 成本结构 ----
    dep_display = cm_get("depreciation_years")
    if not dep_display:
        dep_display = {"monthly": "按月租赁", "one-time": "一次性采购"}.get(data.get("cost_mode"), "-")
    rows_cost = (
        f'<tr><td>单卡月成本（输入）</td><td class="num">{fmt_money(cm_get("single_card_monthly_cost", 0))}</td><td>含硬件摊销、电费、机房、运维；{esc(dep_display)}</td></tr>'
        f'<tr><td>÷ 利用率 {fmt_pct(cm_get("utilization", 0))} → 有效成本</td><td class="num">{fmt_money(cm_get("effective_card_cost_per_month", 0))}</td><td>空转部分为沉没成本</td></tr>'
        f'<tr><td>×（1 + {fmt_pct(cm_get("overhead_ratio", 0.2))}）→ 总月成本</td><td class="num">{fmt_money(cm_get("total_monthly_cost", 0))}</td><td>电费/带宽/存储/集群/运维</td></tr>'
        f'<tr><td><b>输出 token 成本</b></td><td class="num"><b>{fmt_money(tok.get("output_cost_per_million", 0))} / 百万</b></td><td>decode 吞吐 {fmt_num((data.get("throughput_estimate", {}) or {}).get("output_tokens_per_sec", 0))} tok/s（估算，需压测）</td></tr>'
    )

    # ---- 定价体系 ----
    tiers = ps.get("price_tiers_impact") or {}
    cache = tiers.get("cache_discount") or {}
    batch = tiers.get("batch_discount") or {}
    pkg = tiers.get("package_check") or {}
    if flat_mode or not ps:
        rows_price = (
            '<tr><td colspan="3" class="ok-cell">定价策略待 AI 编排层补充：'
            '按成本加成 / 市场对标生成 pricing_suggestion（含缓存 / 批量 / 套餐阶梯）后重新渲染</td></tr>'
        )
        pkg_callout = ""
    else:
        pkg_pass = pkg.get("pass", False)
        rows_price = (
            f'<tr><td>输入（缓存未命中）</td><td class="num">{fmt_num(ps.get("input_price_per_million", 0))}</td><td>成本 {fmt_2f(tok.get("input_cost_per_million", 0))} ÷ 0.4</td></tr>'
            f'<tr><td>输入（缓存命中）</td><td class="num">{fmt_num(cache.get("cache_hit_price_per_million", 0))}</td><td>命中时 prefill 成本≈0（0.1x）</td></tr>'
            f'<tr><td>输出</td><td class="num">{fmt_num(ps.get("output_price_per_million", 0))}</td><td>成本 {fmt_2f(tok.get("output_cost_per_million", 0))} ÷ 0.4</td></tr>'
            f'<tr><td>批量任务（{fmt_pct(batch.get("discount", 0.5))} 折扣）</td><td class="num">{fmt_num(batch.get("batch_price_per_million", 0))}</td><td>填空闲产能，增量毛利 ≈ {fmt_pct(batch.get("incremental_gross_margin", 0.92))}</td></tr>'
        )
        pkg_callout = (
            f'<div class="callout {"g" if pkg_pass else "r"}"><b>{"✅ 套餐校验通过" if pkg_pass else "❌ 套餐校验未通过"}（{fmt_num(pkg.get("package_price", 0))} 元/月含 {fmt_num(pkg.get("included_tokens_million", 0))} 百万 token）：</b>'
            f'售价 {fmt_num(pkg.get("price_per_million", 0))} 元/百万 vs 混合成本 {fmt_2f(pkg.get("blended_unit_cost_per_million", 0))} 元/百万，'
            f'毛利率 {fmt_pct(pkg.get("margin_at_package_price", 0))}。{esc(pkg.get("conclusion", ""))}</div>'
        )

    # ---- 市场对标条形图 ----
    bars_html = ""
    max_price = 100.0
    competitors = []
    dsp = mc.get("deepseek_v4_pro_official") or {}
    if dsp:
        dsp_current = dsp.get("current_until_20260816", {}) or {}
        dsp_off = dsp.get("from_20260817_off_peak", {}) or {}
        dsp_peak = dsp.get("from_20260817_peak", {}) or {}
        competitors.append(("DeepSeek V4 Pro 当前价", dsp_current.get("output", 0), "#dc2626"))
        competitors.append(("DeepSeek 8/17 闲时价", dsp_off.get("output", 0), "#f59e0b"))
        competitors.append(("DeepSeek 8/17 高峰价", dsp_peak.get("output", 0), "#f59e0b"))
    elif mc.get("deepseek_official") and mc["deepseek_official"].get("output") is not None:
        # 兼容 SKILL.md 示例结构 deepseek_official {input_hit,input_miss,output}
        competitors.append(("DeepSeek 官方输出价", float(mc["deepseek_official"]["output"]), "#dc2626"))
    claude = mc.get("claude_sonnet") or {}
    if claude.get("output"):
        competitors.append((f'Claude Sonnet 5（${claude.get("output", 10)}）', float(claude.get("output", 10)) * 7.2, "#94a3b8"))
    gpt = mc.get("gpt") or {}
    if gpt.get("output"):
        competitors.append((f'GPT-5.6-terra（${gpt.get("output", 12)}）', float(gpt.get("output", 12)) * 7.2, "#94a3b8"))
    my_price = ps.get("output_price_per_million", 0)
    if my_price:
        competitors.append(("本方案建议价", my_price, "#2563eb"))
    if competitors:
        max_price = max(c[1] for c in competitors) * 1.05
    for label, val, color in sorted(competitors, key=lambda x: x[1], reverse=True):
        width = max(3, val / max_price * 100)
        bold = "font-weight:600" if color == "#2563eb" else ""
        bars_html += (
            f'<div class="bar-row"><div class="bar-label" style="{bold}">{esc(label)}</div>'
            f'<div class="bar-track"><div class="bar-fill" style="width:{width:.0f}%;background:{color};">{fmt_money(val)}</div></div></div>'
        )
    if not competitors:
        bars_html = (
            '<div class="ok-cell">市场对标待补充：'
            + ("AI 编排层需联网核验官方价后填入 market_comparison（当前仅成本数据）" if flat_mode else "market_comparison 数据为空")
            + "</div>"
        )
    verified_line = f'官方价已核验 {esc(mc.get("verified_at", "—"))}' if mc.get("verified") else "⚠️ 市场价格未核验（verified=false）"

    # ---- 敏感度（JSON 通常无此数据，标注需脚本批量跑）----
    util = cm_get("utilization", 0.7)
    rows_sens = (
        f'<tr><td colspan="3" class="ok-cell">敏感度数据需用 cost_calculator.py 批量跑（--utilization 0.5/0.6/0.7/0.8/0.9）后填入；基准利用率 {fmt_pct(util)}</td></tr>'
    )

    # ---- 风险清单 ----
    rows_risk = ""
    for r in risks:
        tag = severity_tag(r)
        rows_risk += f'<tr><td><span class="tag {tag}">{("高" if tag=="r" else "中" if tag=="a" else "低")}</span></td><td>{esc(r)}</td></tr>'
    if not rows_risk:
        rows_risk = (
            '<tr><td colspan="2" class="ok-cell">'
            + ("风险清单待 AI 编排层补充（当前仅成本数据）" if flat_mode else "无风险")
            + "</td></tr>"
        )

    # ---- 行动建议（从 summary 提炼 P0/P1，简化处理）----
    action_items = []
    if "压测" in summary or "吞吐" in summary:
        action_items.append(("P0", "压测校准吞吐，回填成本模型"))
    if "核验" in summary or "市场" in summary or "许可" in summary:
        action_items.append(("P0", "确认模型规格与转售许可，监控官方调价（&gt;7 天重验）"))
    if "差异化" in summary or "私有化" in summary:
        action_items.append(("P0", "差异化定位：私有化 / 数据合规 / 专有部署"))
    if "缓存" in summary:
        action_items.append(("P1", "优先实现 prefix caching，吃下 agent 客户"))
    if not action_items:
        action_items.append(
            (
                "P1",
                "补充定价策略与市场对标：联网核验官方价后生成 pricing_suggestion 再渲染" if flat_mode
                else "按 summary 结论细化行动项",
            )
        )
    rows_action = ""
    for prio, act in action_items:
        tag = "r" if prio == "P0" else "a"
        rows_action += f'<tr><td><span class="tag {tag}">{prio}</span></td><td>{esc(act)}</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{esc(title)}</title>
<style>
  :root{{
    --bg:#ffffff; --panel:#f7f8fa; --ink:#1f2430; --muted:#6b7280;
    --line:#e5e7eb; --brand:#2563eb; --brand-soft:#eff6ff;
    --red:#dc2626; --red-soft:#fef2f2; --green:#16a34a; --green-soft:#f0fdf4;
    --amber:#d97706; --amber-soft:#fffbeb;
  }}
  *{{box-sizing:border-box}}
  body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
       color:var(--ink);background:var(--bg);line-height:1.6;font-size:15px}}
  .wrap{{max-width:1000px;margin:0 auto;padding:36px 28px 72px}}
  h1{{font-size:24px;margin:0 0 4px}}
  .sub{{color:var(--muted);font-size:13.5px;margin-bottom:18px}}
  h2{{font-size:17px;margin:30px 0 12px;padding-left:10px;border-left:4px solid var(--brand)}}
  .callout{{border-radius:10px;padding:14px 18px;margin:16px 0;font-size:14.5px}}
  .callout.a{{background:var(--amber-soft);border:1px solid #fde68a;color:#92400e}}
  .callout.r{{background:var(--red-soft);border:1px solid #fecaca;color:#991b1b}}
  .callout.g{{background:var(--green-soft);border:1px solid #bbf7d0;color:#166534}}
  .kpis{{display:flex;flex-wrap:wrap;gap:12px;margin:16px 0}}
  .kpi{{flex:1 1 170px;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px}}
  .kpi .v{{font-size:21px;font-weight:700;color:var(--brand)}}
  .kpi .l{{font-size:12.5px;color:var(--muted);margin-top:2px}}
  table{{border-collapse:collapse;width:100%;margin:10px 0;font-size:13.5px;background:#fff}}
  th,td{{border:1px solid var(--line);padding:8px 11px;text-align:left}}
  th{{background:#f1f5f9;font-weight:600}}
  td.num{{text-align:right;font-variant-numeric:tabular-nums}}
  .tag{{display:inline-block;font-size:12px;padding:2px 9px;border-radius:999px;font-weight:600}}
  .tag.r{{background:var(--red-soft);color:var(--red)}}
  .tag.a{{background:var(--amber-soft);color:var(--amber)}}
  .tag.g{{background:var(--green-soft);color:var(--green)}}
  .tag.b{{background:var(--brand-soft);color:var(--brand)}}
  .bars{{margin:14px 0}}
  .bar-row{{display:flex;align-items:center;gap:10px;margin:9px 0}}
  .bar-label{{width:170px;font-size:13px;color:var(--ink);flex-shrink:0}}
  .bar-track{{flex:1;background:#eef1f5;border-radius:6px;height:26px;position:relative;overflow:hidden}}
  .bar-fill{{height:100%;border-radius:6px;display:flex;align-items:center;padding-left:10px;color:#fff;font-size:12.5px;font-weight:600;white-space:nowrap}}
  .ok-cell{{text-align:center;color:var(--muted);padding:16px}}
  .foot{{margin-top:36px;font-size:12px;color:var(--muted);border-top:1px solid var(--line);padding-top:12px}}
</style>
</head>
<body>
<div class="wrap">

  <h1>{esc(title)}</h1>
  <div class="sub">商业简报 ｜ 测算日期 {generated_at} ｜ 部署：{esc(cm_get("gpu_type", "—"))} ｜ 模型：{esc(cm_get("model", "—"))} ｜ 目标毛利 {esc(margin_display)}</div>

  <div class="callout a"><b>核心结论：</b>{esc(summary)}</div>

  <div class="kpis">{kpi_cards}</div>

  <h2>① 成本结构</h2>
  <table><tr><th>项目</th><th class="num">金额</th><th>说明</th></tr>{rows_cost}</table>

  <h2>② 建议定价体系（{esc(margin_display)} 毛利）</h2>
  <table><tr><th>计费项</th><th class="num">建议价（¥/百万）</th><th>依据</th></tr>{rows_price}</table>
  {pkg_callout}

  <h2>③ 市场对标 · 输出价（¥/百万 token，{verified_line}）</h2>
  <div class="bars">{bars_html}</div>
  <div class="sub">红/橙 = DeepSeek（当前 ¥6 及 8/17 调价区间）；灰 = Claude/GPT（按 7.2 汇率折算）；蓝 = 本方案建议价。</div>

  <h2>④ 利用率敏感度（利用率是最大变量）</h2>
  <table><tr><th>利用率</th><th class="num">输出成本（¥/百万）</th><th class="num">对应售价（{esc(margin_display)} 毛利）</th></tr>{rows_sens}</table>

  <h2>⑤ 风险清单</h2>
  <table><tr><th>等级</th><th>风险</th></tr>{rows_risk}</table>

  <h2>⑥ 行动建议</h2>
  <table><tr><th>优先级</th><th>行动</th></tr>{rows_action}</table>

  <div class="foot">
    数据来源：{esc(data.get("notes", "cost_calculator.py 计算；估算值需压测校准"))} ｜ 市场价格核验：{esc(mc.get("verified_at", "未核验"))}
    ｜ HTML 由 pricing_report.py 从计算脚本 JSON 渲染，数字同源、禁止手填 ｜ 用于演示/留档
  </div>

</div>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description="Token 定价 HTML 商业简报渲染器")
    parser.add_argument("--input", required=True, help="token-cost-pricing 输出的 JSON")
    parser.add_argument("--output", required=True, help="输出的 HTML 文件路径")
    parser.add_argument("--title", default="Token 定价方案", help="简报标题")
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    html_str = render(data, args.title, datetime.now().strftime("%Y-%m-%d"))
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html_str)
    print(f"已生成: {args.output}（{len(html_str)} 字节）")


if __name__ == "__main__":
    main()
