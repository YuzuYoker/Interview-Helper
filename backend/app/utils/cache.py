"""Redis 回答缓存：懒加载单例 + 知识版本号即时失效 + TTL 兜底。

- 只缓存完整回答（answer + sources JSON）——命中直接跳过"检索+生成"整条链路；
- key = rag:chat:{知识版本号}:{sha256(question+history+top_k+web维度)[:16]}；
  文档上传/删除成功时 incr_kv()，版本号变化 → 旧缓存全部即时失效；
  web 维度（联网开关 + 搜索结果哈希）并入 key：联网/不联网的回答互不污染，
  搜索结果变了（页级缓存过期重抓）→ 新哈希 → 重新生成；
- 版本号内存双写兜底：防 Redis 在文档变更瞬间宕机导致缓存未失效的窗口；
- Redis 不可用时所有操作静默降级（0.5s 连接超时，不拖慢请求）。
"""
import hashlib
import json
import threading
import time
from typing import Optional

from app.utils.config import settings

_client = None
_client_lock = threading.Lock()
_redis_ok = False

# 知识版本号：内存兜底（Redis 不可用时也递增）
_mem_kv = 0
_kv_lock = threading.Lock()


def _get_client():
    global _client, _redis_ok
    if _client is None and settings.redis_url:
        with _client_lock:
            if _client is None:
                try:
                    import redis

                    _client = redis.Redis.from_url(
                        settings.redis_url,
                        decode_responses=True,
                        socket_connect_timeout=0.5,
                        socket_timeout=1.0,
                    )
                    _client.ping()
                    _redis_ok = True
                except Exception:
                    _client = None
                    _redis_ok = False
    return _client


def redis_ok() -> bool:
    """真实探测：已建立的连接也 ping 一次（感知 Redis 中途宕机）。"""
    global _redis_ok
    c = _get_client()
    if c is None:
        return False
    try:
        _redis_ok = bool(c.ping())
    except Exception:
        _redis_ok = False
    return _redis_ok


def get_kv() -> int:
    """知识版本号（取 Redis 与内存的较大值，防抖动窗口）。"""
    kv = _mem_kv
    c = _get_client()
    if c is not None:
        try:
            kv = max(kv, int(c.get("rag:kv") or 0))
        except Exception:
            pass
    return kv


def incr_kv() -> None:
    """文档增删成功后调用：版本号 +1 → 全部缓存即时失效。"""
    global _mem_kv
    with _kv_lock:
        _mem_kv += 1
    c = _get_client()
    if c is not None:
        try:
            c.incr("rag:kv")
        except Exception:
            pass  # 内存已 +1，兜底生效


def _key(
    question: str,
    history: list[dict] | None,
    top_k: int,
    web_search: bool = False,
    web_hash: str = "",
) -> str:
    payload = json.dumps(
        [question, history or [], top_k, bool(web_search), web_hash],
        ensure_ascii=False,
    )
    h = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return f"rag:chat:{get_kv()}:{h}"


def get_cached_answer(
    question: str,
    history: list[dict] | None,
    top_k: int,
    web_search: bool = False,
    web_hash: str = "",
) -> Optional[dict]:
    """命中返回 {"answer": str, "sources": [dict,...]}，未命中/不可用返回 None。

    web_search/web_hash：联网维度并入 key——联网与不联网的回答互不污染，
    搜索结果变化（web_hash 变）也视为不同回答。
    """
    if not settings.redis_url:
        return None
    c = _get_client()
    if c is None:
        return None
    try:
        raw = c.get(_key(question, history, top_k, web_search, web_hash))
        return json.loads(raw) if raw else None
    except Exception:
        return None


def set_cached_answer(
    question: str,
    history: list[dict] | None,
    top_k: int,
    payload: dict,
    web_search: bool = False,
    web_hash: str = "",
) -> None:
    if not settings.redis_url:
        return
    c = _get_client()
    if c is None:
        return
    try:
        c.setex(
            _key(question, history, top_k, web_search, web_hash),
            settings.cache_ttl,
            json.dumps(payload, ensure_ascii=False),
        )
    except Exception:
        pass


# ---- 联网搜索结果缓存（页级，独立于回答缓存）----


def _web_key(query: str) -> str:
    h = hashlib.sha256(query.encode()).hexdigest()[:16]
    return f"rag:web:{h}"


def get_web_cache(query: str) -> Optional[list]:
    """命中返回 [{title,url,snippet,content}, ...]，未命中/不可用返回 None。

    TTL 更短语义：搜索结果 30 分钟级有效，重复提问不重复抓取 Bing 与网页。
    """
    if not settings.redis_url:
        return None
    c = _get_client()
    if c is None:
        return None
    try:
        raw = c.get(_web_key(query))
        return json.loads(raw) if raw else None
    except Exception:
        return None


def set_web_cache(query: str, items: list, ttl: int | None = None) -> None:
    if not settings.redis_url:
        return
    c = _get_client()
    if c is None:
        return
    try:
        c.setex(
            _web_key(query),
            ttl or settings.web_cache_ttl,
            json.dumps(items, ensure_ascii=False),
        )
    except Exception:
        pass
