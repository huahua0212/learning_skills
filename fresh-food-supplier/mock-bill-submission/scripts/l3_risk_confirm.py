#!/usr/bin/env python3
# mock-bill-submission — L3 异常确认层（超量/截单/未知 → 自动确认 or 转人工 + AI建议）
# 输入：data/orders_matched.csv
# 输出：data/orders_risk.csv（含 status / exception / ai_suggestion / amount）+ 三态返回
import csv
import os
from datetime import datetime

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("ORDER_DATA") or os.path.join(SKILL_DIR, "data")
MATCH = os.path.join(DATA, "orders_matched.csv")
CUST = os.path.join(DATA, "customers.csv")
OUT = os.path.join(DATA, "orders_risk.csv")
OVERQTY = 50.0
FIELDS = ["order_id", "customer_id", "customer_type", "item", "sku_id", "sku_name",
          "qty_std", "unit", "unit_price", "amount", "status", "exception",
          "ai_suggestion", "remark", "deliver_date"]


def load_csv(p):
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def build_customers():
    return {r["customer_id"].strip(): r for r in load_csv(CUST)}


def parse_time(s):
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            pass
    return None


def risk():
    custs = build_customers()
    rows = load_csv(MATCH)
    out = []
    for o in rows:
        oid = o["order_id"].strip()
        cid = o["customer_id"].strip()
        item = o.get("item", "").strip()
        sku_id = o.get("sku_id", "").strip()
        sku_name = o.get("sku_name", "").strip()
        qty_std = float(o.get("qty_std", 0) or 0)
        unit = o.get("unit", "").strip()
        price = float(o.get("unit_price", 0) or 0)
        cutoff = custs.get(cid, {}).get("cutoff_time", "23:59")
        status = exc = sugg = ""
        amount = round(qty_std * price, 2)

        if not sku_id:
            status, exc, sugg = "needs_human", "未知商品，无SKU映射", "转人工匹配标准SKU或补充别名映射"
        elif qty_std > OVERQTY:
            status, exc, sugg = ("needs_human",
                                 f"下单量{qty_std:.0f}{unit}超阈值(>{OVERQTY:.0f}{unit})，需人工复核",
                                 "建议：拆分订单或人工确认大单")
        else:
            rt = parse_time(o.get("received_at", ""))
            if rt:
                rh, rm = (int(x) for x in cutoff.split(":"))
                if (rt.hour, rt.minute) > (rh, rm):
                    status, exc, sugg = ("needs_human",
                                         f"接单时间{rt.strftime('%H:%M')}超过截单时间{cutoff}",
                                         "建议：改派次日配送或人工特批")
            if status == "":
                status = "auto_confirmed"

        out.append({"order_id": oid, "customer_id": cid, "customer_type": o.get("customer_type", ""),
                    "item": item, "sku_id": sku_id, "sku_name": sku_name, "qty_std": qty_std,
                    "unit": unit, "unit_price": price, "amount": amount, "status": status,
                    "exception": exc, "ai_suggestion": sugg, "remark": o.get("remark", ""),
                    "deliver_date": o.get("deliver_date", "")})

    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(out)

    auto = [r for r in out if r["status"] == "auto_confirmed"]
    human = [r for r in out if r["status"] == "needs_human"]
    if not out:
        print("【接单结果】⚪ 无订单数据")
    elif not human:
        amt = sum(float(r["amount"]) for r in auto)
        print(f"【接单结果】✅ 全部接单成功（{len(auto)} 单）| 成交 ¥{amt:.2f} | 本批零人工")
    elif not auto:
        print(f"【接单结果】🔴 无法接单（{len(human)} 单均转人工）")
        for r in human:
            print(f"   ❌ {r['order_id']} {r['customer_id']} {r['item']} {r['qty_std']}{r['unit']} / {r['exception']}")
    else:
        amt = sum(float(r["amount"]) for r in auto)
        print(f"【接单结果】🟡 部分接单（自动确认 {len(auto)} 单 / 转人工 {len(human)} 单）| 成交 ¥{amt:.2f}")
        print("   ✅ 已自动确认：")
        for r in auto:
            print(f"      {r['order_id']} {r['sku_name']} {r['qty_std']}{r['unit']} ¥{r['amount']}")
        print("   🔶 转人工复核：")
        for r in human:
            print(f"      {r['order_id']} {r['sku_name'] or '?'} {r['qty_std']}{r['unit']} / {r['exception']} / {r['ai_suggestion']}")
    print(f"[L3 risk] 已写入 {OUT}")


if __name__ == "__main__":
    risk()
