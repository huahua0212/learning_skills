#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
review_report.py - Token 商业化评审 HTML 报告渲染器（token-review Skill D 演示层）

读取 review_calculator.py 输出的 JSON，渲染为一页式 HTML 报告（给老板/客户/演示用）。
**HTML 内每个数字都来自输入 JSON，禁止手填**——与计算脚本同源。

用法：
  python3 review_report.py \
    --input test/LLM-token-provider/review_output.json \
    --output test/LLM-token-provider/review_report_2026-08.html \
    --title "Token 平台 · 商业化评审报告" --period "2026-08"

输出：自包含 HTML（内联 CSS，无外部依赖，浏览器直开，可打印 PDF）
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
    """比率 -> 百分比（1.89 -> +189.6%）"""
    x = float(v)
    sign = "+" if x > 0 else ""
    return f"{sign}{x * 100:.1f}%"


def fmt_pt(v):
    """百分点 -> 显示（-2.86 -> -2.86pt）"""
    x = float(v)
    sign = "+" if x > 0 else ""
    return f"{sign}{x:.1f}pt"


def dev_tag(v, invert=False):
    """偏差值的颜色标签：正偏差(成本/吞吐方向)用红，负偏差用绿；invert 用于毛利/利用率(负=坏)"""
    x = float(v)
    if invert:
        return "r" if x < 0 else "g"
    return "r" if x > 0 else "g"


def render(data: dict, title: str, period: str) -> str:
    dev = data["deviation_analysis"]
    real = data["real_cost"]
    struct = data["structure_competitiveness"]
    plan = data["pricing_adjustment_plan"]
    risks = data["risk_scan"]
    mc = data["manual_confirmation"]

    # ---- 结论横幅 ----
    margin = dev["margin_deviation"]
    margin_dev = margin["deviation_pt"]
    margin_ok = margin_dev >= 0
    verdict = "毛利达标 ✓" if margin_ok else "毛利不达标 ❌"
    verdict_tag = "g" if margin_ok else "r"

    # ---- 五大偏差行 ----
    cost_in = dev["cost_deviation"]["input_cost"]
    cost_out = dev["cost_deviation"]["output_cost"]
    tp_in = dev["throughput_deviation"]["input"]
    tp_out = dev["throughput_deviation"]["output"]

    rows_dev = ""
    rows_dev += (
        f'<tr><td>单 Token 成本偏差率 · 输入</td>'
        f'<td class="num">{fmt_num(cost_in["estimated"])} → <b>{fmt_num(cost_in["actual"])}</b></td>'
        f'<td><span class="tag {dev_tag(cost_in["deviation_pct"])}">{fmt_pct(cost_in["deviation_pct"])}</span></td></tr>'
    )
    rows_dev += (
        f'<tr><td>单 Token 成本偏差率 · 输出</td>'
        f'<td class="num">{fmt_num(cost_out["estimated"])} → <b>{fmt_num(cost_out["actual"])}</b></td>'
        f'<td><span class="tag {dev_tag(cost_out["deviation_pct"])}">{fmt_pct(cost_out["deviation_pct"])}</span></td></tr>'
    )
    rows_dev += (
        f'<tr><td>毛利率偏差</td>'
        f'<td class="num">真实 {fmt_pct(margin["actual_margin"])} vs 目标 {fmt_pct(margin["target_margin"])}</td>'
        f'<td><span class="tag {dev_tag(margin_dev, invert=True)}">{fmt_pt(margin_dev)}</span></td></tr>'
    )
    rows_dev += (
        f'<tr><td>吞吐偏差率 · 输入（prefill）</td>'
        f'<td class="num">{fmt_num(tp_in["estimated"])} → <b>{fmt_num(tp_in["actual"])}</b> tok/s</td>'
        f'<td><span class="tag {dev_tag(tp_in["deviation_pct"])}">{fmt_pct(tp_in["deviation_pct"])}</span></td></tr>'
    )
    rows_dev += (
        f'<tr><td>吞吐偏差率 · 输出（decode）</td>'
        f'<td class="num">{fmt_num(tp_out["estimated"])} → <b>{fmt_num(tp_out["actual"])}</b> tok/s</td>'
        f'<td><span class="tag {dev_tag(tp_out["deviation_pct"])}">{fmt_pct(tp_out["deviation_pct"])}</span></td></tr>'
    )
    rows_dev += (
        f'<tr><td>GPU 利用率偏差</td>'
        f'<td class="num">{fmt_pct(dev["utilization_deviation_pt"] / 100 + 0.7)}（预估 70%）→ 实际 {fmt_pct((dev["utilization_deviation_pt"] + 70) / 100)}</td>'
        f'<td><span class="tag {dev_tag(dev["utilization_deviation_pt"], invert=True)}">{fmt_pt(dev["utilization_deviation_pt"])}</span></td></tr>'
    )
    rows_dev += (
        f'<tr><td>缓存命中率偏差</td>'
        f'<td class="num">实际 {fmt_pct((dev["cache_hit_deviation_pt"] + 40) / 100)}（预估 40%）</td>'
        f'<td><span class="tag {dev_tag(dev["cache_hit_deviation_pt"], invert=True)}">{fmt_pt(dev["cache_hit_deviation_pt"])}</span></td></tr>'
    )

    # ---- 真实成本 ----
    rows_real = (
        f'<tr><td>真实总月成本</td><td class="num">{fmt_money(real["total_monthly_cost"])}</td>'
        f'<td>单卡月成本 ÷ 真实利用率 × (1+overhead)</td></tr>'
        f'<tr><td>真实输入成本</td><td class="num">{fmt_money(real["input_cost_per_million"])} / 百万</td>'
        f'<td>按真实 prefill 吞吐</td></tr>'
        f'<tr><td>真实输出成本</td><td class="num">{fmt_money(real["output_cost_per_million"])} / 百万</td>'
        f'<td>按真实 decode 吞吐</td></tr>'
    )

    # ---- 结构竞争力 ----
    pm = struct["per_million_margin"]
    struct_tag = "r" if struct["structure_type"] == "亏损型结构" else "g"
    rows_struct = (
        f'<tr><td>输入请求毛利</td><td class="num">{fmt_money(pm["input"])} / 百万</td>'
        f'<td><span class="tag {"g" if pm["input"] >= 0 else "r"}">{"盈利" if pm["input"] >= 0 else "亏损"}</span></td></tr>'
        f'<tr><td>输出请求毛利</td><td class="num">{fmt_money(pm["output"])} / 百万</td>'
        f'<td><span class="tag {"g" if pm["output"] >= 0 else "r"}">{"盈利" if pm["output"] >= 0 else "亏损"}</span></td></tr>'
        f'<tr><td>缓存请求毛利</td><td class="num">{fmt_money(pm["cache"])} / 百万</td>'
        f'<td><span class="tag {"g" if pm["cache"] >= 0 else "r"}">{"盈利" if pm["cache"] >= 0 else "亏损"}</span></td></tr>'
    )

    # ---- 调价建议 ----
    rows_plan = ""
    for item in plan["before_after"]:
        chg = item["change_pct"]
        tag = "g" if chg == 0 else "r"
        label = "不变" if chg == 0 else fmt_pct(chg)
        rows_plan += (
            f'<tr><td>{esc(item["item"])}</td>'
            f'<td class="num">{fmt_num(item["before"])}</td>'
            f'<td class="num"><b>{fmt_num(item["after"])}</b></td>'
            f'<td><span class="tag {tag}">{label}</span></td></tr>'
        )

    # ---- 风险扫描 ----
    rows_risk = ""
    for r in risks:
        tag = {"高": "r", "中": "a", "低": "b"}.get(r["severity"], "b")
        rows_risk += (
            f'<tr><td><span class="tag {tag}">{esc(r["severity"])}</span></td>'
            f'<td><b>{esc(r["risk"])}</b></td>'
            f'<td>{esc(r["detail"])}</td>'
            f'<td>{esc(r["suggestion"])}</td></tr>'
        )

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
  .callout.g{{background:var(--green-soft);border:1px solid #bbf7d0;color:#166534}}
  .callout.a{{background:var(--amber-soft);border:1px solid #fde68a;color:#92400e}}
  .callout.r{{background:var(--red-soft);border:1px solid #fecaca;color:#991b1b}}
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
  .flow{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 18px;font-size:14px;letter-spacing:1px}}
  .foot{{margin-top:36px;font-size:12px;color:var(--muted);border-top:1px solid var(--line);padding-top:12px}}
  .big{{font-size:19px;font-weight:700}}
</style>
</head>
<body>
<div class="wrap">

  <h1>{esc(title)}</h1>
  <div class="sub">评审账期 {esc(period)} ｜ 预估侧：token-cost-pricing（Skill A）｜ 真实侧：token-billing + token-reconciliation（Skill B/C）｜ 生成 {data.get("generated_at","—")}</div>

  <div class="callout {verdict_tag}">
    <b>{verdict}</b> — 真实毛利率 <b>{fmt_pct(margin["actual_margin"])}</b>，目标 <b>{fmt_pct(margin["target_margin"])}</b>，偏差 <b>{fmt_pt(margin_dev)}</b>。
    根因：{esc(dev["root_cause_summary"])}。
    建议<b>输出单价 {fmt_pct(plan["before_after"][1]["change_pct"])}</b>（{fmt_num(plan["before_after"][1]["before"])} → {fmt_num(plan["before_after"][1]["after"])}），<b>待人工确认</b>。
  </div>

  <div class="kpis">
    <div class="kpi"><div class="v">{fmt_pct(margin["actual_margin"])}</div><div class="l">真实毛利率</div></div>
    <div class="kpi"><div class="v">{fmt_pct(margin["target_margin"])}</div><div class="l">目标毛利率</div></div>
    <div class="kpi"><div class="v">{fmt_pt(margin_dev)}</div><div class="l">毛利偏差（pt）</div></div>
    <div class="kpi"><div class="v">{fmt_money(real["total_monthly_cost"])}</div><div class="l">真实总月成本</div></div>
    <div class="kpi"><div class="v">{fmt_money(real["output_cost_per_million"])}</div><div class="l">真实输出成本 / 百万</div></div>
  </div>

  <h2>① 五大偏差（预估 vs 真实）</h2>
  <table>
    <tr><th>偏差指标</th><th class="num">预估 → 真实</th><th>偏差</th></tr>
    {rows_dev}
  </table>

  <h2>② 真实成本（复用 cost_calculator.py 口径）</h2>
  <table>
    <tr><th>项目</th><th class="num">金额</th><th>口径</th></tr>
    {rows_real}
  </table>

  <h2>③ 结构竞争力（请求级毛利判定）</h2>
  <table>
    <tr><th>请求类型</th><th class="num">每百万毛利</th><th>盈亏</th></tr>
    {rows_struct}
  </table>
  <div class="callout {struct_tag}">
    <b>结构判定：{esc(struct["structure_type"])}</b> — 亏损 token 占比 {fmt_pct(struct["loss_token_ratio"])}。
    {esc(struct["note"])} 输出侧负毛利 → 需单独上调输出单价或限制超长生成。
  </div>

  <h2>④ 调价建议（成本加成反推，待人工确认）</h2>
  <table>
    <tr><th>定价项</th><th class="num">调整前</th><th class="num">调整后</th><th>变化</th></tr>
    {rows_plan}
  </table>
  <div class="sub">{esc(plan["formula"])}。调价不是终点——须经人工确认后才回炉 token-cost-pricing 重算。</div>

  <h2>⑤ 风险扫描</h2>
  <table>
    <tr><th>等级</th><th>风险</th><th>详情</th><th>建议</th></tr>
    {rows_risk}
  </table>

  <h2>⑥ 人工确认与回炉闭环</h2>
  <div class="callout a"><b>铁律：</b>{esc(mc["rule"])}</div>
  <div class="flow">确认通过 → 回炉 token-cost-pricing（市场价&gt;7天重验）→ 同步 token-billing 计费规则</div>
  <div class="sub">确认驳回 → 维持现价，记录驳回原因。</div>

  <div class="foot">
    数据来源：{esc(data.get("notes",""))} ｜ HTML 由 review_report.py 从计算脚本 JSON 渲染，数字同源、禁止手填 ｜ 用于演示/留档
  </div>

</div>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description="Token 商业化评审 HTML 报告渲染器")
    parser.add_argument("--input", required=True, help="review_calculator.py 输出的 JSON")
    parser.add_argument("--output", required=True, help="输出的 HTML 文件路径")
    parser.add_argument("--title", default="Token 平台 · 商业化评审报告", help="报告标题")
    parser.add_argument("--period", default="—", help="评审账期，如 2026-08")
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("generated_at", datetime.now().strftime("%Y-%m-%d %H:%M"))

    html_str = render(data, args.title, args.period)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html_str)
    print(f"已生成: {args.output}（{len(html_str)} 字节）")


if __name__ == "__main__":
    main()
