#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reconciliation_report.py - Token 对账与账单 HTML 报告渲染器（token-reconciliation Skill C 演示层）

读取 reconciliation_calculator.py 输出的 JSON，渲染为一页式 HTML 报告（给老板/客户/演示用）。
**HTML 内每个数字都来自输入 JSON，禁止手填**——与计算脚本同源，保证"报告和数字对不上"永不发生。

用法：
  python3 reconciliation_report.py \
    --input test/LLM-token-provider/reconciliation_output.json \
    --output test/LLM-token-provider/reconciliation_report_2026-08-14.html \
    --title "Token 平台 · 对账与账单报告" \
    --period "2026-08-14" --tenant "全租户" --cycle "月结"

输出：自包含 HTML（内联 CSS，无外部依赖，浏览器直开，可打印 PDF）
"""

import argparse
import html
import json

# ============================================================
# 渲染工具
# ============================================================

def esc(v):
    return html.escape(str(v))


def fmt_money(v):
    """金额 2 位小数（带 ¥）"""
    return f"¥{float(v):,.2f}"


def fmt_num(v):
    return f"{int(v):,}" if float(v) == int(v) else f"{float(v):,.2f}"


def fmt_pct(v):
    return f"{float(v) * 100:.2f}%"


def fmt_wanfen(v):
    """万分之格式化：0.00005 -> 万分之 0.5（避免 0.005% 被舍入成 0.01% 的语义错误）"""
    return f"万分之 {float(v) * 10000:.1f}"


def verdict_tag(verdict):
    if "一致" in verdict and "差异" not in verdict:
        return ("g", "✅ 一致")
    if "微量" in verdict:
        return ("a", "⚠️ 微量差异")
    return ("r", "❌ 超阈值")


def row_diff_cell(diff):
    if diff == 0:
        return '<td class="num diff-ok">0</td>'
    cls = "diff-neg" if diff < 0 else "diff-pos"
    sign = "" if diff < 0 else "+"
    return f'<td class="num {cls}">{sign}{diff}</td>'


# ============================================================
# 报告生成
# ============================================================

def render(data: dict, title: str, period: str, tenant: str, cycle: str) -> str:
    rec = data["reconciliation"]
    match = rec["match"]
    agg = rec["aggregate_comparison"]
    bill = data["bill_main"]

    vtag, vlabel = verdict_tag(rec["verdict"])
    diff_total = (match["missing_leak"] + match["orphan_anomaly"]
                  + match["duplicated"] + len(data["token_mismatch_list"]))

    # ---- 差异清单表 ----
    rows_diff = ""
    for item in data["missing_queue"]:
        rows_diff += (f'<tr><td><span class="tag r">漏记</span></td><td>{esc(item["request_id"])}</td>'
                      f'<td>输入 {fmt_num(item.get("prompt_tokens",0))} / 输出 {fmt_num(item.get("completion_tokens",0))}</td>'
                      f'<td>{esc(item.get("finish_reason",""))}</td><td>{esc(item["action"])}</td></tr>')
    for item in data["orphan_queue"]:
        rows_diff += (f'<tr><td><span class="tag a">孤儿</span></td><td>{esc(item["request_id"])}</td>'
                      f'<td>源B {fmt_num(item.get("count",1))} 条</td><td>—</td><td>{esc(item["action"])}</td></tr>')
    for item in data["duplicated_list"]:
        rows_diff += (f'<tr><td><span class="tag a">重复</span></td><td>{esc(item["request_id"])}</td>'
                      f'<td>源B {fmt_num(item.get("count",2))} 条</td><td>—</td><td>{esc(item["action"])}</td></tr>')
    for item in data["token_mismatch_list"]:
        detail = item.get("diff_detail") or {}
        parts = []
        for dim, d in detail.items():
            parts.append(f'{dim}: 源A {fmt_num(d["source_a"])} vs 源B {fmt_num(d["source_b"])}')
        parts_str = "；".join(parts)
        rows_diff += (f'<tr><td><span class="tag r">用量不符</span></td><td>{esc(item["request_id"])}</td>'
                      f'<td>{parts_str}</td><td>—</td><td>{esc(item["action"])}</td></tr>')
    if not rows_diff:
        rows_diff = '<tr><td colspan="5" class="ok-cell">无差异 ✓</td></tr>'

    # ---- 调账流水表 ----
    rows_adj = ""
    for item in data["adjustments"]:
        if "adjust_id" in item:  # 外部调账
            amt = item.get("delta_amount", 0)
            tag = "g" if amt >= 0 else "r"
            label = "补收" if amt >= 0 else "冲减"
            ref = item.get("adjust_id", "") or item.get("request_id", "")
        else:  # OOM 补记
            amt = item.get("adjust_amount", 0)
            tag = "g"
            label = "OOM补记"
            ref = item.get("request_id", "")
        rows_adj += (f'<tr><td>{esc(ref)}</td><td><span class="tag {tag}">{label}</span></td>'
                     f'<td class="num">{fmt_money(amt)}</td><td>{esc(item.get("reason",""))}</td></tr>')
    if not rows_adj:
        rows_adj = '<tr><td colspan="4" class="ok-cell">本期无调账</td></tr>'

    # ---- 账单明细类型 ----
    item_types = "、".join(bill.get("bill_item_types", []))

    # ---- 账单明细（bill_item）行 ----
    rows_items = ""
    for it in data.get("bill_items", []):
        amt = float(it["amount"])
        cls_tag = "g" if amt >= 0 else "r"
        type_tag = ("g" if it["item_type"] == "调账记录" and amt >= 0 else
                    "r" if it["item_type"] == "调账记录" else "b")
        rows_items += (
            f'<tr><td>{esc(it["item_no"])}</td>'
            f'<td><span class="tag {type_tag}">{esc(it["item_type"])}</span></td>'
            f'<td>{esc(it["tenant_id"])}</td><td>{esc(it["model"])}</td>'
            f'<td class="num">{fmt_num(it["input_tokens"])}</td>'
            f'<td class="num">{fmt_num(it["output_tokens"])}</td>'
            f'<td class="num">{fmt_money(amt)}</td>'
            f'<td>{esc(it["remark"])}</td></tr>'
        )
    if not rows_items:
        rows_items = '<tr><td colspan="8" class="ok-cell">本期无账单明细</td></tr>'

    # ---- 双视图：租户侧（隐藏成本）vs 内部财务（显示成本/毛利）----
    rows_tenant = ""
    rows_internal = ""
    for it in data.get("bill_items", []):
        amt = float(it["amount"])
        rows_tenant += (
            f'<tr><td>{esc(it["item_no"])}</td>'
            f'<td>{esc(it["item_type"])}</td><td>{esc(it["model"])}</td>'
            f'<td class="num">{fmt_num(it["input_tokens"])}</td>'
            f'<td class="num">{fmt_num(it["output_tokens"])}</td>'
            f'<td class="num">{fmt_money(amt)}</td></tr>'
        )
        rows_internal += (
            f'<tr><td>{esc(it["item_no"])}</td>'
            f'<td>{esc(it["item_type"])}</td><td>{esc(it["model"])}</td>'
            f'<td class="num">{fmt_num(it["input_tokens"])}</td>'
            f'<td class="num">{fmt_num(it["output_tokens"])}</td>'
            f'<td class="num">{fmt_money(amt)}</td>'
            f'<td class="num">—</td><td class="num">—</td></tr>'
        )
    if not rows_tenant:
        rows_tenant = '<tr><td colspan="6" class="ok-cell">本期无账单明细</td></tr>'
        rows_internal = '<tr><td colspan="8" class="ok-cell">本期无账单明细</td></tr>'

    # ---- 状态流转 ----
    status_flow = "待确认 → 已确认 → 已调账 → 已关闭"

    # ---- 聚合对比表 ----
    rows_agg = ""
    rows_agg += (f'<tr><td>请求条数</td><td class="num">{fmt_num(agg["request_count"]["source_a"])}</td>'
                 f'<td class="num">{fmt_num(agg["request_count"]["source_b_raw"])}</td>'
                 f'<td class="num">去重后 {fmt_num(agg["request_count"]["source_b_dedup"])}</td>'
                 f'{row_diff_cell(agg["request_count"]["diff_raw"])}</tr>')
    rows_agg += (f'<tr><td>输入 token</td><td class="num">{fmt_num(agg["total_input_tokens"]["source_a"])}</td>'
                 f'<td class="num">{fmt_num(agg["total_input_tokens"]["source_b"])}</td><td class="num">—</td>'
                 f'{row_diff_cell(agg["total_input_tokens"]["diff"])}</tr>')
    rows_agg += (f'<tr><td>输出 token</td><td class="num">{fmt_num(agg["total_output_tokens"]["source_a"])}</td>'
                 f'<td class="num">{fmt_num(agg["total_output_tokens"]["source_b"])}</td><td class="num">—</td>'
                 f'{row_diff_cell(agg["total_output_tokens"]["diff"])}</tr>')

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
  .diff-ok{{color:var(--green)}}
  .diff-pos{{color:var(--red);font-weight:600}}
  .diff-neg{{color:var(--green);font-weight:600}}
  .ok-cell{{text-align:center;color:var(--green);padding:16px}}
  .foot{{margin-top:36px;font-size:12px;color:var(--muted);border-top:1px solid var(--line);padding-top:12px}}
  .flow{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 18px;font-size:14px;letter-spacing:1px}}
  .big{{font-size:19px;font-weight:700}}
</style>
</head>
<body>
<div class="wrap">

  <h1>{esc(title)}</h1>
  <div class="sub">账期 {esc(period)} ｜ 结算 {esc(cycle)} ｜ 租户 {esc(tenant)} ｜ 生成 {esc(data.get("generated_at","—"))} ｜ 三源对账 + 先对账后出账</div>

  <div class="callout {vtag}">
    <b>对账判定：{vlabel}</b> — 差异率 <b>{fmt_pct(rec["difference_rate"])}</b>（{esc(rec["difference_rate_formula"])}，容忍阈值 {esc(fmt_wanfen(rec["tolerance_threshold"]))}）。
    本期账单<b>最终应收 {fmt_money(bill["final_amount"])}</b>（原始 {fmt_money(bill["original_amount"])} + 调账 {fmt_money(bill["adjust_amount"])}）。
  </div>

  <div class="kpis">
    <div class="kpi"><div class="v">{fmt_money(bill["original_amount"])}</div><div class="l">原始计算金额</div></div>
    <div class="kpi"><div class="v">{fmt_money(bill["adjust_amount"])}</div><div class="l">调账净额（±）</div></div>
    <div class="kpi"><div class="v">{fmt_money(bill["final_amount"])}</div><div class="l"><b>最终应收</b></div></div>
    <div class="kpi"><div class="v">{fmt_pct(rec["difference_rate"])}</div><div class="l">差异率（{vlabel}）</div></div>
    <div class="kpi"><div class="v">{match["matched"]}</div><div class="l">对账匹配请求数</div></div>
  </div>

  <h2>① 三源对账 · 聚合对比（源A 真相源 vs 源B 计量库）</h2>
  <table>
    <tr><th>指标</th><th class="num">源A 推理原始日志</th><th class="num">源B 计量入库（原始行）</th><th class="num">源B 去重</th><th class="num">差值</th></tr>
    {rows_agg}
  </table>
  <div class="sub">红 = 源B 多于源A（疑似多计/重复）；绿 = 源B 少于源A（疑似漏记）。对账目标 A ≈ B ≈ C。</div>

  <h2>② 差异清单（{diff_total} 类差异）</h2>
  <table>
    <tr><th>类型</th><th>request_id</th><th>详情</th><th>finish_reason</th><th>处理动作</th></tr>
    {rows_diff}
  </table>

  <h2>③ 调账流水（历史账单禁止原地修改，全部走调账）</h2>
  <table>
    <tr><th>关联单号 / request_id</th><th>类型</th><th class="num">金额（±）</th><th>原因</th></tr>
    {rows_adj}
  </table>
  <div class="sub">调账净额 <b>{fmt_money(data["adjust_total"])}</b>；调账记录在下一期账单体现，已出账数据不做原地修改（财务审计要求）。</div>

  <h2>④ 账单主表（bill_main）</h2>
  <table>
    <tr><th>字段</th><th class="num">值</th><th>说明</th></tr>
    <tr><td>账期输入 token</td><td class="num">{fmt_num(bill["total_token_in"])}</td><td>计费请求汇总</td></tr>
    <tr><td>账期输出 token</td><td class="num">{fmt_num(bill["total_token_out"])}</td><td>计费请求汇总</td></tr>
    <tr><td>原始计算金额 original_amount</td><td class="num">{fmt_money(bill["original_amount"])}</td><td>按 token 计费口径（来自 token-billing）</td></tr>
    <tr><td>调账金额 adjust_amount</td><td class="num">{fmt_money(bill["adjust_amount"])}</td><td>可正可负</td></tr>
    <tr><td><b>最终应收 final_amount</b></td><td class="num big">{fmt_money(bill["final_amount"])}</td><td>{esc(bill["formula"])}</td></tr>
  </table>
  <div class="flow">账单状态流转：{esc(status_flow)}</div>

  <h2>⑤ 账单明细（bill_item，可下钻到 request_id）</h2>
  <table>
    <tr><th>单号</th><th>类型</th><th>租户</th><th>模型</th><th class="num">输入 token</th><th class="num">输出 token</th><th class="num">金额（¥）</th><th>备注</th></tr>
    {rows_items}
  </table>
  <div class="sub">明细类型：{esc(item_types)}——调账记录作为账单子项时，备注注明"对账补记 / 对账冲减"。金额含调账后合计即 bill_main.final_amount。</div>

  <h2>⑥ 双视图（同一份账单明细，两种视角）</h2>

  <h3 style="font-size:15px;margin:14px 0 4px">租户侧视图（客户看到）</h3>
  <table>
    <tr><th>单号</th><th>类型</th><th>模型</th><th class="num">输入 token</th><th class="num">输出 token</th><th class="num">金额（¥）</th></tr>
    {rows_tenant}
  </table>
  <div class="sub">❌ 不展示：GPU 物理卡秒、硬件成本、毛利率（内部敏感）。调账说明以备注形式呈现。</div>

  <h3 style="font-size:15px;margin:18px 0 4px">内部财务视图（后台，租户字段基础上增加）</h3>
  <table>
    <tr><th>单号</th><th>类型</th><th>模型</th><th class="num">输入 token</th><th class="num">输出 token</th><th class="num">金额（¥）</th><th class="num">硬件成本</th><th class="num">毛利率</th></tr>
    {rows_internal}
  </table>
  <div class="sub">内部额外统计：OOM 请求数、KV 缓存命中率、调账明细完整原因；硬件成本与毛利来自 token-cost-pricing / token-review 输出（此处以 — 占位）。</div>

  <h2>⑦ 状态流转与审计</h2>
  <div class="flow">账单状态流转：{esc(status_flow)}</div>
  <div class="callout a"><b>审计铁律：</b>已确认账单不可修改，只能新增调账冲销；每笔差异与调账留痕（单号、租户、±金额、原因、关联对账任务 id），支撑审计与客户争议。</div>

  <div class="foot">
    数据来源：{esc(data.get("notes",""))} ｜ HTML 由 reconciliation_report.py 从计算脚本 JSON 渲染，数字同源、禁止手填 ｜ 用于演示/留档，正式结算以系统数据为准
  </div>

</div>
</body>
</html>
"""


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Token 对账与账单 HTML 报告渲染器")
    parser.add_argument("--input", required=True, help="reconciliation_calculator.py 输出的 JSON")
    parser.add_argument("--output", required=True, help="输出的 HTML 文件路径")
    parser.add_argument("--title", default="Token 平台 · 对账与账单报告", help="报告标题")
    parser.add_argument("--period", default="—", help="账期，如 2026-08-14")
    parser.add_argument("--tenant", default="全租户", help="租户范围")
    parser.add_argument("--cycle", default="月结", help="结算周期")
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 附加生成信息（不改变计算脚本输出的数值）
    from datetime import datetime
    data.setdefault("generated_at", datetime.now().strftime("%Y-%m-%d %H:%M"))

    html_str = render(data, args.title, args.period, args.tenant, args.cycle)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html_str)
    print(f"已生成: {args.output}（{len(html_str)} 字节）")


if __name__ == "__main__":
    main()
