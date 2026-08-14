#!/usr/bin/env python3
# mock-bill-submission — L1 录入层（强校验 + 落库）
# 职责：把"已弹窗确认商户"的结构化参数做入参强校验，通过则写入 data/orders_raw.csv。
# 注意：弹窗确认商户是 conductor（对话 agent）的职责，本层只收"已确认"的结构化参数。
import csv
import argparse
import os
import sys
from datetime import datetime, timedelta

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("ORDER_DATA") or os.path.join(SKILL_DIR, "data")
RAW = os.path.join(DATA, "orders_raw.csv")
CUST = os.path.join(DATA, "customers.csv")
UNIT_TO_JIN = {"斤": 1.0, "kg": 2.0, "千克": 2.0, "公斤": 2.0, "克": 0.002,
               "吨": 2000.0, "份": 1.0, "个": 1.0, "箱": 1.0, "袋": 1.0}
RAW_FIELDS = ["order_id", "customer_id", "item", "qty", "unit", "remark", "deliver_date", "received_at"]


def load_csv(p):
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def build_customers():
    return {r["customer_id"].strip(): r for r in load_csv(CUST)}


def validate(cid, item, qty, unit, custs):
    """返回错误列表；空列表表示通过。"""
    errs = []
    if cid not in custs:
        errs.append(f"未知客户 {cid}（customers.csv 中不存在）")
    if not str(item).strip():
        errs.append("商品(item)为空")
    try:
        if float(qty) <= 0:
            errs.append(f"数量({qty})必须 > 0")
    except (ValueError, TypeError):
        errs.append(f"数量({qty})不是有效数字")
    if str(unit).strip() not in UNIT_TO_JIN:
        errs.append(f"单位({unit})非法，仅支持 {list(UNIT_TO_JIN.keys())}")
    return errs


def next_oid():
    if not os.path.exists(RAW) or os.path.getsize(RAW) == 0:
        return "O001"
    nums = [int(r["order_id"].strip()[1:]) for r in load_csv(RAW)
            if r.get("order_id", "").strip()[1:].isdigit()]
    return f"O{max(nums)+1:03d}" if nums else "O001"


def add(args):
    custs = build_customers()
    cid = args.customer.strip()
    item = args.item.strip()
    qty = args.qty
    unit = args.unit.strip()
    remark = args.remark or ""
    # —— 强校验：不过则拒绝落库，直接返回"无法接单" ——
    errs = validate(cid, item, qty, unit, custs)
    if errs:
        print("🔴 无法接单：入参校验失败")
        for e in errs:
            print(f"   - {e}")
        print("请修正后重新提交（必填：客户 + 商品 + 数量(单位)；备注可选）。")
        sys.exit(2)

    oid = next_oid()
    deliver = args.deliver or (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    received = args.received or datetime.now().strftime("%Y-%m-%d %H:%M")
    row = {k: "" for k in RAW_FIELDS}
    row.update({"order_id": oid, "customer_id": cid, "item": item, "qty": qty,
                "unit": unit, "remark": remark, "deliver_date": deliver, "received_at": received})
    writeheader = not os.path.exists(RAW) or os.path.getsize(RAW) == 0
    with open(RAW, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=RAW_FIELDS)
        if writeheader:
            w.writeheader()
        w.writerow(row)
    cname = custs[cid].get("name", cid)
    print(f"✅ 已接收 {oid} | 客户 {cname}({cid}) | {item} {qty}{unit} | 备注：{remark or '无'} | 待接单")


def show():
    print("=== L1 接单入口(orders_raw.csv) ===")
    for r in load_csv(RAW):
        print(f"  {r['order_id']} | {r['customer_id']} | {r['item']} {r['qty']}{r['unit']}"
              f" | 备注:{r.get('remark','') or '无'} | 送达{r['deliver_date']} | 接单{r['received_at']}")


def main():
    ap = argparse.ArgumentParser("mock-bill-submission L1 录入层")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pa = sub.add_parser("add", help="结构化接单入口（强校验）")
    pa.add_argument("--customer", required=True)
    pa.add_argument("--item", required=True)
    pa.add_argument("--qty", required=True)
    pa.add_argument("--unit", required=True)
    pa.add_argument("--remark", default="")
    pa.add_argument("--deliver", default="")
    pa.add_argument("--received", default="")
    sub.add_parser("show", help="展示已录入的原始单")
    args = ap.parse_args()
    if args.cmd == "add":
        add(args)
    else:
        show()


if __name__ == "__main__":
    main()
