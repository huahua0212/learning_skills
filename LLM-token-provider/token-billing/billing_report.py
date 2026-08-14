#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
billing_report.py - Token 计量计费 HTML 报告渲染器（token-billing Skill B 演示层）

读取 billing_calculator.py 输出的 JSON，渲染为一页式 HTML 报告（给老板/客户/演示用）。
**HTML 内每个数字都来自输入 JSON，禁止手填**——与计算脚本同源。

用法：
  python3 billing_report.py \
    --input test/LLM-token-provider/billing_output.json \
    --output test/LLM-token-provider/billing_report_2026-08.html \
    --title "Token 平台 · 计量计费报告" --period "2026-08"

输出：自包含 HTML（内联 CSS，无外部依赖，浏览器直开，可打印 PDF）
"""

import argparse
import html
import json
from collections import Counter
from datetime import datetime


def esc(v):
    return html.escape(str(v))

def fmt_money(v):
    return f"¥{float(v):,.2f}"


def fmt_num(v):
    return f"{int(v):,}" if float(v) == int(v) else f"{float(v):,.2f}"


def fmt_pct(v):
    return f"{float(v) * 100:.1f}%"


def render(data: dict, title: str, period: str, generated_at: str) -> str:
    fees = data.get("fee_details", [])
    s = data.get("summary", {})
    notes = data.get("notes", "")

    charged = [f for f in fees if f.get("charged")]
    not_charged = [f for f in fees if not f.get("charged")]

    # ---- 结论横幅 ----
    not_charged_reasons = s.get("not_charged_reasons", [])
    reason_txt = "；".join(not_charged_reasons) if not_charged_reasons else "无"
    callout_cls = "g" if not_charged_reasons else "a"

    # ---- KPI ----
    total_cost = s.get("total_cost", 0)
    total_in = s.get("total_input_tokens", 0)
    total_out = s.get("total_output_tokens", 0)
    total_cache = s.get("total_cache_tokens", 0)
    cache_ratio = total_cache / (total_in + total_cache) if (total_in + total_cache) > 0 else 0
    kpis = (
        f'<div class="kpi"><div class="v">{fmt_money(total_cost)}</div><div class="l">本期总费用</div></div>'
        f'<div class="kpi"><div class="v">{s.get("charged_records", 0)} / {s.get("total_records", 0)}</div><div class="l">计费 / 总请求</div></div>'
        f'<div class="kpi"><div class="v">{fmt_num(total_in)}</div><div class="l">输入 token</div></div>'
        f'<div class="kpi"><div class="v">{fmt_num(total_out)}</div><div class="l">输出 token</div></div>'
        f'<div class="kpi"><div class="v">{fmt_num(total_cache)}（{fmt_pct(cache_ratio)}）</div><div class="l">缓存 token（占输入侧）</div></div>'
    )

    # ---- 费用明细表 ----
    rows = ""
    for f in fees:
        charged_flag = f.get("charged", True)
        tok = f.get("tokens", {})
        cost = f.get("cost", {})
        rules = "、".join(f.get("applied_rules", []))
        if charged_flag:
            rows += (
                f'<tr><td>{esc(f.get("request_id",""))}</td>'
                f'<td>{esc(f.get("tenant_id",""))}</td>'
                f'<td>{esc(f.get("billing_mode",""))}</td>'
                f'<td class="num">{fmt_num(tok.get("input",0))}</td>'
                f'<td class="num">{fmt_num(tok.get("output",0))}</td>'
                f'<td class="num">{fmt_num(tok.get("cache_hit",0))}</td>'
                f'<td class="num">{fmt_money(cost.get("total",0))}</td>'
                f'<td>{esc(rules)}</td></tr>'
            )
        else:
            rows += (
                f'<tr class="not-charged"><td>{esc(f.get("request_id",""))}</td>'
                f'<td>{esc(f.get("tenant_id",""))}</td>'
                f'<td>{esc(f.get("billing_mode",""))}</td>'
                f'<td class="num">{fmt_num(tok.get("input",0))}</td>'
                f'<td class="num">{fmt_num(tok.get("output",0))}</td>'
                f'<td class="num">{fmt_num(tok.get("cache_hit",0))}</td>'
                f'<td class="num">—</td>'
                f'<td><span class="tag a">不计费</span> {esc(rules)}</td></tr>'
            )
    if not rows:
        rows = '<tr><td colspan="8" class="ok-cell">无费用明细</td></tr>'

    # ---- 计费模式分布 ----
    by_mode = s.get("cost_by_billing_mode", {})
    rows_mode = ""
    for mode, m in sorted(by_mode.items(), key=lambda x: -x[1].get("total_cost", 0)):
        share = m.get("total_cost", 0) / total_cost if total_cost > 0 else 0
        rows_mode += (
            f'<tr><td>{esc(mode)}</td>'
            f'<td class="num">{m.get("records",0)} 笔</td>'
            f'<td class="num">{fmt_money(m.get("total_cost",0))}</td>'
            f'<td class="num">{fmt_pct(share)}</td></tr>'
        )
    if not rows_mode:
        rows_mode = '<tr><td colspan="4" class="ok-cell">无计费模式分布</td></tr>'

    # ---- 规则应用统计 ----
    rule_counter = Counter()
    for f in fees:
        for r in f.get("applied_rules", []):
            rule_counter[r] += 1
    rows_rules = ""
    for rule, cnt in rule_counter.most_common():
        rows_rules += f'<tr><td>{esc(rule)}</td><td class="num">{cnt} 笔</td></tr>'
    if not rows_rules:
        rows_rules = '<tr><td colspan="2" class="ok-cell">无规则统计</td></tr>'

    # ---- 价格快照（取第一笔）----
    price_snap = ""
    if fees:
        ps = fees[0].get("price_snapshot", {})
        price_snap = (
            f'<table><tr><th>价格项</th><th class="num">值（¥/百万 token）</th><th>说明</th></tr>'
            f'<tr><td>输入（缓存未命中）</td><td class="num">{fmt_num(ps.get("input_price_per_million",0))}</td><td>来自 token-cost-pricing（Skill A）</td></tr>'
            f'<tr><td>输出</td><td class="num">{fmt_num(ps.get("output_price_per_million",0))}</td><td>来自 token-cost-pricing（Skill A）</td></tr>'
            f'<tr><td>缓存命中</td><td class="num">{fmt_num(ps.get("cache_hit_price_per_million",0))}</td><td>缓存识别阈值按模型配置</td></tr>'
            f'<tr><td>快照时间</td><td class="num">{esc(ps.get("snapshot_at","—"))}</td><td>计费当刻锁价，调价不重算历史</td></tr></table>'
        )
    else:
        price_snap = '<div class="sub">无费用明细，价格快照不可用</div>'

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
  .kpis{{display:flex;flex-wrap:wrap;gap:12px;margin:16px 0}}
  .kpi{{flex:1 1 160px;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px}}
  .kpi .v{{font-size:20px;font-weight:700;color:var(--brand)}}
  .kpi .l{{font-size:12.5px;color:var(--muted);margin-top:2px}}
  table{{border-collapse:collapse;width:100%;margin:10px 0;font-size:13.5px;background:#fff}}
  th,td{{border:1px solid var(--line);padding:8px 11px;text-align:left}}
  th{{background:#f1f5f9;font-weight:600}}
  td.num{{text-align:right;font-variant-numeric:tabular-nums}}
  tr.not-charged{{background:var(--amber-soft);opacity:.75}}
  .tag{{display:inline-block;font-size:12px;padding:2px 9px;border-radius:999px;font-weight:600}}
  .tag.r{{background:var(--red-soft);color:var(--red)}}
  .tag.a{{background:var(--amber-soft);color:var(--amber)}}
  .tag.g{{background:var(--green-soft);color:var(--green)}}
  .tag.b{{background:var(--brand-soft);color:var(--brand)}}
  .ok-cell{{text-align:center;color:var(--muted);padding:16px}}
  .foot{{margin-top:36px;font-size:12px;color:var(--muted);border-top:1px solid var(--line);padding-top:12px}}
</style>
</head>
<body>
<div class="wrap">

  <h1>{esc(title)}</h1>
  <div class="sub">计费周期 {esc(period)} ｜ 生成 {generated_at} ｜ 价格来源：token-cost-pricing（Skill A）｜ 网关机采 + 幂等 + 价格快照</div>

  <div class="callout {callout_cls}">
    <b>计费结论：</b>本期 <b>{s.get("charged_records",0)}</b> 笔计费、<b>{s.get("not_charged_records",0)}</b> 笔不计费，
    总费用 <b>{fmt_money(total_cost)}</b>。不计费原因：{esc(reason_txt)}。
  </div>

  <div class="kpis">{kpis}</div>

  <h2>① 费用明细（每笔请求）</h2>
  <table>
    <tr><th>request_id</th><th>租户</th><th>计费模式</th><th class="num">输入 token</th><th class="num">输出 token</th><th class="num">缓存 token</th><th class="num">费用（¥）</th><th>应用规则</th></tr>
    {rows}
  </table>
  <div class="sub">黄色行 = 不计费请求（rate_limit / auth_error 等，服务未执行处理）。费用由 billing_calculator.py 计算（Decimal 定点）。</div>

  <h2>② 计费模式分布</h2>
  <table>
    <tr><th>计费模式</th><th class="num">笔数</th><th class="num">费用（¥）</th><th class="num">占比</th></tr>
    {rows_mode}
  </table>

  <h2>③ 规则应用统计</h2>
  <table>
    <tr><th>应用规则</th><th class="num">触发笔数</th></tr>
    {rows_rules}
  </table>

  <h2>④ 价格快照（计费当刻锁价）</h2>
  {price_snap}
  <div class="sub">价格快照原则：调价不重算历史；反查争议按快照时间截断。</div>

  <div class="foot">
    数据来源：{esc(notes)} ｜ HTML 由 billing_report.py 从计算脚本 JSON 渲染，数字同源、禁止手填 ｜ 用于演示/留档
  </div>

</div>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description="Token 计量计费 HTML 报告渲染器")
    parser.add_argument("--input", required=True, help="billing_calculator.py 输出的 JSON")
    parser.add_argument("--output", required=True, help="输出的 HTML 文件路径")
    parser.add_argument("--title", default="Token 平台 · 计量计费报告", help="报告标题")
    parser.add_argument("--period", default="—", help="计费周期，如 2026-08")
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    html_str = render(data, args.title, args.period, datetime.now().strftime("%Y-%m-%d %H:%M"))
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html_str)
    print(f"已生成: {args.output}（{len(html_str)} 字节）")


if __name__ == "__main__":
    main()
