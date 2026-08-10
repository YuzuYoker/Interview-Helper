"""性能检查：缓存 HIT/MISS 对比 + 流式首字计时 + 限流验证。

用法（需后端运行且已配置 REDIS_URL）：
    python backend/scripts/perf_check.py
"""
import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests

BASE = "http://localhost:8000/api"
QUESTION = "如何准备系统设计面试？"


def main() -> None:
    # 0. health：redis 状态
    h = requests.get(f"{BASE}/health").json()
    print(f"[health] redis_connected={h.get('redis_connected')} device={h.get('device')}")

    # 1. 非流式缓存：第一次 MISS，第二次 HIT
    t0 = time.time()
    r1 = requests.post(f"{BASE}/chat", json={"question": QUESTION, "top_k": 4})
    dt1 = time.time() - t0
    assert r1.status_code == 200, r1.text
    print(f"[chat #1] X-Cache={r1.headers.get('X-Cache')} 耗时 {dt1:.2f}s")

    t0 = time.time()
    r2 = requests.post(f"{BASE}/chat", json={"question": QUESTION, "top_k": 4})
    dt2 = time.time() - t0
    assert r2.headers.get("X-Cache") == "HIT", "第二次应为缓存命中"
    assert r1.json()["answer"] == r2.json()["answer"], "命中内容应一致"
    print(f"[chat #2] X-Cache=HIT 耗时 {dt2:.3f}s  ← 缓存命中加速 {dt1 / max(dt2, 1e-6):.0f}x")

    # 2. 流式首字计时（缓存命中回放）
    t0 = time.time()
    first_delta = None
    r = requests.post(
        f"{BASE}/chat/stream",
        json={"question": QUESTION, "top_k": 4},
        stream=True,
        timeout=60,
    )
    for line in r.iter_lines(decode_unicode=True):
        if line.startswith("event: delta"):
            first_delta = time.time() - t0
            break
    print(f"[stream 缓存命中] 首字耗时 {first_delta:.3f}s")

    # 3. 限流验证：连发 11 次 /api/chat（同一 IP，默认限额 10/分钟）
    hits = 0
    for i in range(11):
        r = requests.post(f"{BASE}/chat", json={"question": f"第{i}次", "top_k": 1})
        if r.status_code == 429:
            print(f"[限流] 第 {i + 1} 次请求返回 429 ✓ Retry-After={r.headers.get('retry-after')}")
            break
        hits += 1
    if hits >= 11:
        print("[限流] 未触发 429（11 次都放行？检查配置）")

    print("\n性能检查完成 ✅")


if __name__ == "__main__":
    main()
