#!/usr/bin/env python3
# mock-bill-submission 重置脚本：清空 L2/L3/L4 产物，保留 orders_raw 与配置
import os

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(SKILL_DIR, "data")
for fn in ["orders_matched.csv", "orders_risk.csv", "orders_forecast.csv", "dispatch.csv"]:
    p = os.path.join(DATA, fn)
    if os.path.exists(p):
        os.remove(p)
print("[reset] 已清空 L2/L3/L4 产物（orders_raw.csv 与配置保留，可重新跑 L2→L3→L4）")
