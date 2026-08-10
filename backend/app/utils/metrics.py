"""性能指标采集（内存，线程安全）：缓存命中、耗时、工具调用数。

Agent 的 analyze_performance 工具读取聚合数据；重启清零（单机够用）。
环形保留最近 N 条，避免无限增长。
"""
import threading
import time

_lock = threading.Lock()
_records: list[dict] = []
_MAX_RECORDS = 200


def record(**kwargs) -> None:
    """记录一条请求指标：{cache_hit?, total_ms?, retrieval_ms?, tool_calls?, rounds?}。"""
    with _lock:
        _records.append({"ts": time.time(), **kwargs})
        if len(_records) > _MAX_RECORDS:
            del _records[: len(_records) - _MAX_RECORDS]


def get_summary() -> dict:
    """聚合最近请求的指标（analyze_performance 工具读取）。"""
    with _lock:
        n = len(_records)
        if not n:
            return {"requests": 0, "note": "暂无请求数据"}

        def avg(key: str):
            vals = [r[key] for r in _records if key in r]
            return round(sum(vals) / len(vals), 1) if vals else None

        hits = sum(1 for r in _records if r.get("cache_hit"))
        tool_vals = [r["tool_calls"] for r in _records if "tool_calls" in r]
        return {
            "requests": n,
            "cache_hit_rate": round(hits / n, 3),
            "avg_total_ms": avg("total_ms"),
            "avg_generate_ms": avg("generate_ms"),
            "avg_tool_calls": round(sum(tool_vals) / len(tool_vals), 1) if tool_vals else None,
            "total_tool_calls": sum(tool_vals),
        }
