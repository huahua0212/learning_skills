#!/usr/bin/env python3
# mock-bill-submission — L4 汇总层（需求预测 + 分单，输出两份可推送产物）
# 输入：data/orders_risk.csv
# 输出：
#   orders_forecast.csv —— 采购建议单（按 SKU 汇总次日需求 + 安全系数建议采购量）
#   dispatch.csv        —— 分车/分仓单（按客户聚合 + 路由仓）
import csv
import math
import os

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("ORDER_DATA") or os.path.join(SKILL_DIR, "data")
RISK = os.path.join(DATA, "orders_risk.csv")
CUST = os.path.join(DATA, "customers.csv")
FCAST = os.path.join(DATA, "orders_forecast.csv")
DISP = os.path.join(DATA, "dispatch.csv")
SAFETY = 1.10  # 安全系数 10%


def load_csv(p):
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def build_customers():
    return {r["customer_id"].strip(): r for r in load_csv(CUST)}


def aggregate():
    custs = build_customers()
    rows = load_csv(RISK)

    # 需求预测：按 SKU 汇总（自动确认 + 将履约的转人工单，均视为需求）
    dem = {}
    for r in rows:
        s = r.get("sku_name", "")
        if s:
            q = float(r.get("qty_std", 0) or 0)
            dem.setdefault(s, {"qty": 0.0, "unit": r.get("unit", "")})
            dem[s]["qty"] += q

    with open(FCAST, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sku_name", "total_qty", "unit", "suggest_purchase_qty"])
        for s, d in dem.items():
            sug = math.ceil(d["qty"] * SAFETY / 10) * 10  # 取整到 10 的倍数
            w.writerow([s, f"{d['qty']:.0f}", d["unit"], sug])

    # 分单：按客户聚合 + 路由仓（大B→中央仓A，普通→前置仓B）
    byc = {}
    for r in rows:
        byc.setdefault(r["customer_id"], []).append(r)
    with open(DISP, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["customer_id", "customer_name", "order_count", "route_warehouse"])
        for cid, rs in byc.items():
            c = custs.get(cid, {})
            name = c.get("name", cid)
            ctype = c.get("type", "普通")
            wh = "中央仓A" if ctype == "大B" else "前置仓B"
            w.writerow([cid, name, len(rs), wh])

    print(f"[L4 aggregate] 需求预测 -> {FCAST}；分单 -> {DISP}")
    print("📦 采购建议单：")
    for s, d in dem.items():
        sug = math.ceil(d["qty"] * SAFETY / 10) * 10
        print(f"   {s}: 需求 {d['qty']:.0f}{d['unit']} -> 建议采购 {sug}{d['unit']}")
    print("🚚 分车/分仓单：")
    for cid, rs in byc.items():
        c = custs.get(cid, {})
        wh = "中央仓A" if c.get("type", "普通") == "大B" else "前置仓B"
        print(f"   {cid} {c.get('name', cid)}: {len(rs)} 单 -> {wh}")


if __name__ == "__main__":
    aggregate()
