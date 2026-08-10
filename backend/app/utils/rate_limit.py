"""滑动窗口限流中间件（纯 ASGI，不引 slowapi）。

- 纯 ASGI 而非 BaseHTTPMiddleware：后者对 SSE 流式响应有缓冲问题；
- 内存滑动窗口：deque 存请求时间戳，先清过期再计数；
- 规则：chat 与 chat/stream 共享每 IP 限额，upload 单独限额，其余不限；
- SSE 长连接只计发起时刻，不按持续时间计。
"""
import json
import threading
import time
from collections import defaultdict, deque

from app.utils.config import settings

# 路径前缀 -> 限额规则
CHAT_PATHS = ("/api/chat",)
CHAT_STREAM_PATHS = ("/api/chat/stream",)
UPLOAD_PATHS = ("/api/documents/upload",)

_windows: dict[str, deque] = defaultdict(deque)
_lock = threading.Lock()


def _client_ip(scope: dict) -> str:
    if settings.rate_limit_trust_proxy:
        for name, value in scope.get("headers", []):
            if name == b"x-forwarded-for":
                return value.decode().split(",")[0].strip()
    client = scope.get("client") or ("unknown", 0)
    return str(client[0])


def _check(key: str, limit: int, window: float) -> bool:
    """True = 放行；False = 超限。"""
    now = time.monotonic()
    with _lock:
        dq = _windows[key]
        while dq and now - dq[0] > window:
            dq.popleft()
        if len(dq) >= limit:
            return False
        dq.append(now)
        return True


class RateLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        ip = _client_ip(scope)
        allowed = True
        retry_after = settings.rate_limit_chat_per_min

        if path.startswith(CHAT_PATHS) or path.startswith(CHAT_STREAM_PATHS):
            allowed = _check(f"chat:{ip}", settings.rate_limit_chat_per_min, 60)
        elif path.startswith(UPLOAD_PATHS):
            allowed = _check(f"up:{ip}", settings.rate_limit_upload_per_min, 60)
            retry_after = settings.rate_limit_upload_per_min

        if not allowed:
            body = json.dumps(
                {"detail": "请求过于频繁，请稍后再试"}, ensure_ascii=False
            ).encode()
            headers = [
                (b"content-type", b"application/json"),
                (b"retry-after", str(retry_after).encode()),
            ]
            await send(
                {
                    "type": "http.response.start",
                    "status": 429,
                    "headers": headers,
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        await self.app(scope, receive, send)
