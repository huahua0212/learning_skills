#!/usr/bin/env python3
# mock-bill-submission — L2 编码定价层（别名→SKU + 单位归一 + 取价）
# 输入：data/orders_raw.csv
# 输出：data/orders_matched.csv（含 sku_id / sku_name / qty_std / unit / unit_price）
import csv
import os

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("ORDER_DATA") or os.path.join(SKILL_DIR, "data")
RAW = os.path.join(DATA, "orders_raw.csv")
MAP = os.path.join(DATA, "sku_mapping.csv")
PRICE = os.path.join(DATA, "prices.csv")
CUST = os.path.join(DATA, "customers.csv")
OUT = os.path.join(DATA, "orders_matched.csv")
UNIT_TO_JIN = {"斤": 1.0, "kg": 2.0, "千克": 2.0, "公斤": 2.0, "克": 0.002,
               "吨": 2000.0, "份": 1.0, "个": 1.0, "箱": 1.0, "袋": 1.0}
FIELDS = ["order_id", "customer_id", "customer_type", "item", "sku_id", "sku_name",
          "qty_std", "unit", "unit_price", "remark", "deliver_date", "received_at"]


def load_csv(p):
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def build_alias():
    return {r["alias"].strip().lower(): r for r in load_csv(MAP)}


def build_prices():
    return {r["sku_id"].strip(): r for r in load_csv(PRICE)}


def build_customers():
    return {r["customer_id"].strip(): r for r in load_csv(CUST)}


def match():
    alias = build_alias()
    prices = build_prices()
    custs = build_customers()
    rows = load_csv(RAW)
    out = []
    unknown = 0
    for o in rows:
        cid = o["customer_id"].strip()
        item = o.get("item", "").strip()
        qty = float(o.get("qty", 0) or 0)
        unit = o.get("unit", "").strip()
        c = custs.get(cid, {})
        ctype = c.get("type", "普通")
        sku = alias.get(item.lower())
        qty_std = qty * UNIT_TO_JIN.get(unit, 1.0)
        sku_id = sku_name = std_unit = ""
        price = 0.0
        if sku:
            sku_id = sku["sku_id"]
            sku_name = sku["sku_name"]
            std_unit = sku["std_unit"]
            pr = prices.get(sku_id, {})
            # 定价规则：大B 用协议价，普通客户用市价
            price = (float(pr.get("contract_price", 0)) if ctype == "大B"
                     else float(pr.get("market_price", 0)))
        else:
            unknown += 1
        out.append({"order_id": o["order_id"].strip(), "customer_id": cid,
                    "customer_type": ctype, "item": item, "sku_id": sku_id,
                    "sku_name": sku_name, "qty_std": qty_std, "unit": std_unit or unit,
                    "unit_price": price, "remark": o.get("remark", "").strip(),
                    "deliver_date": o.get("deliver_date", ""),
                    "received_at": o.get("received_at", "")})
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(out)
    print(f"[L2 match] 已编码 {len(out)} 单 -> {OUT}（其中 {unknown} 单未知商品，交给 L3 处理）")
    for r in out:
        tag = f"❓未知({r['item']})" if not r["sku_id"] else f"{r['sku_id']}/{r['sku_name']} ¥{r['unit_price']}"
        print(f"  {r['order_id']} {r['item']} {r['qty_std']}{r['unit']} -> {tag}")


if __name__ == "__main__":
    match()
