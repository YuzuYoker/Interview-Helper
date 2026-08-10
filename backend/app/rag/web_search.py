"""联网搜索：后端作为 MCP 客户端连接 fetch MCP server（modelcontextprotocol/servers/src/fetch）。

fetch server（2026.7.10）只提供 fetch 工具（search/Brave 已移除），因此：
- 搜索 = fetch Bing 结果页（raw HTML）→ 正则解析 b_algo 结果块（实测直链，无重定向包装）；
- 抓正文 = 并行 fetch 各结果页（readability 提取 markdown，超长截断，失败用摘要兜底）；
- 全程 best-effort：任何一步失败返回空列表，调用方降级为纯知识库回答，绝不影响主链路。

MCP 客户端是 asyncio，而本服务是同步 FastAPI → 专用后台线程常驻事件循环，
asyncio.run_coroutine_threadsafe 桥接；子进程异常自动重启重连一次。
"""
import asyncio
import concurrent.futures
import hashlib
import html
import json
import logging
import re
import shlex
import threading
from dataclasses import asdict, dataclass
from typing import Optional
from urllib.parse import quote

from app.utils.cache import get_web_cache, set_web_cache
from app.utils.config import settings

logger = logging.getLogger(__name__)

# 浏览器 UA：fetch server 默认 UA（ModelContextProtocol/1.0 Autonomous）会被大量站点拒绝
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

BING_URL = "https://www.bing.com/search?q={q}&mkt=zh-CN"

# Bing 结果块：<li class="b_algo">…</li>，直链 <h2><a href="https://…">标题</a></h2> + <p>摘要</p>
_BING_BLOCK = re.compile(r'<li class="b_algo".*?</li>', re.S)
_BING_LINK = re.compile(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_BING_SNIPPET = re.compile(r"<p[^>]*>(.*?)</p>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_SKIP_DOMAINS = ("bing.com", "microsoft.com", "msn.com", "go.microsoft.com")


@dataclass
class WebItem:
    title: str
    url: str
    snippet: str = ""
    content: str = ""  # 正文（readability markdown）


def _strip_tags(s: str) -> str:
    return html.unescape(_TAG_RE.sub("", s)).strip()


# ---- MCP fetch 客户端（每次搜索内 开→用→关）----
# stdio_client/ClientSession 的 __aenter__ 与 __aexit__ 必须在同一个 task 里执行
# （anyio 限制），因此不做常驻会话；每次 web_search 在常驻 loop 里跑一个协程，
# 内部打开连接 → 并行抓正文 → 关闭，一次 spawn 覆盖整次搜索，无跨 task 关闭问题。

_loop: Optional[asyncio.AbstractEventLoop] = None
_loop_thread: Optional[threading.Thread] = None
_startup_lock = threading.Lock()


def _ensure_loop() -> None:
    global _loop, _loop_thread
    if _loop_thread and _loop_thread.is_alive():
        return
    with _startup_lock:
        if _loop_thread and _loop_thread.is_alive():
            return
        _loop = asyncio.new_event_loop()
        _loop_thread = threading.Thread(
            target=_loop.run_forever, daemon=True, name="mcp-fetch-loop"
        )
        _loop_thread.start()


def _call(coro, timeout: float):
    """在常驻 loop 上执行协程并同步等待结果（超时取消）。

    coro 是返回协程的函数（每次调用新建协程，避免同一协程对象重复调度）。
    """
    _ensure_loop()
    fut = asyncio.run_coroutine_threadsafe(coro(), _loop)
    try:
        return fut.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        fut.cancel()
        raise TimeoutError(f"联网搜索超时（{timeout:.0f}s）")


def _server_params():
    import sys

    from mcp import StdioServerParameters

    cmd = shlex.split(settings.web_fetch_command)
    # "python -m mcp_server_fetch" → 解析为当前解释器（与 uvicorn 同环境，保证 mcp 版本一致）
    if cmd[0].lower() in ("python", "python3"):
        cmd[0] = sys.executable
    args = list(cmd[1:])
    if settings.web_fetch_ignore_robots:
        args.append("--ignore-robots-txt")
    args += ["--user-agent", BROWSER_UA]
    return StdioServerParameters(command=cmd[0], args=args)


async def _open_session():
    """打开 fetch server 连接，返回 (ClientSession, 退出函数)。"""
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    stream_ctx = stdio_client(_server_params())
    read, write = await stream_ctx.__aenter__()
    session_ctx = ClientSession(read, write)
    session = await session_ctx.__aenter__()
    await session.initialize()

    async def _close():
        for ctx in (session_ctx, stream_ctx):
            try:
                await ctx.__aexit__(None, None, None)
            except Exception:
                pass

    return session, _close


async def _fetch_all(items: list[WebItem]) -> list[WebItem]:
    """打开一个连接，并行抓正文（readability 提取），失败用摘要兜底，最后关闭。"""
    session, close = await _open_session()
    try:
        async def _one(item: WebItem) -> WebItem:
            try:
                result = await session.call_tool(
                    "fetch", {"url": item.url, "max_length": settings.web_search_page_length}
                )
                if getattr(result, "isError", False):
                    raise RuntimeError(f"fetch 失败: {result}")
                content = "\n".join(
                    c.text for c in getattr(result, "content", []) if hasattr(c, "text")
                ).strip()
                if content.startswith("<error>") or not content:
                    raise ValueError(content[:80] or "empty content")
                item.content = content[: settings.web_search_page_length]
            except Exception as e:
                logger.warning("抓取网页失败 %s: %s", item.url, e)
                item.content = item.snippet  # 兜底：正文失败时用摘要
            return item

        return list(await asyncio.gather(*(_one(i) for i in items)))
    finally:
        await close()


# ---- Bing 搜索解析 + 抓正文（整体受 web_search_timeout 约束）----


def _fetch_bing_html(url: str) -> str:
    """抓 Bing 结果页 HTML，优先 curl_cffi（模拟 Chrome TLS 指纹）。

    为什么用 curl_cffi 而非裸 httpx：Bing 对容器环境的 TLS 指纹（JA3，如
    python:3.10-slim 的 OpenSSL）判 bot，直接返回 CAPTCHA 验证页（无 b_algo）；
    impersonate="chrome" 完整模拟 Chrome 指纹后返回真实结果页。宿主 httpx 环境
    实测也能通过，因此 curl_cffi 失败时降级 httpx。正文抓取仍走 MCP fetch 工具。
    """
    headers = {
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
    }
    try:
        from curl_cffi.requests import get as curl_get

        resp = curl_get(
            url, impersonate="chrome", headers=headers, timeout=settings.web_search_timeout
        )
        return resp.text
    except Exception as e:
        logger.warning("curl_cffi 抓 Bing 失败，降级 httpx：%s", e)

    import httpx

    headers["User-Agent"] = BROWSER_UA
    try:
        with httpx.Client(
            follow_redirects=True, headers=headers, timeout=settings.web_search_timeout
        ) as client:
            return client.get(url).text
    except Exception as e:
        logger.warning("Bing 结果页抓取失败：%s", e)
        return ""


def search_bing(query: str) -> list[WebItem]:
    """抓 Bing 结果页 → 解析 b_algo 块取直链结果。失败返回空。"""
    url = BING_URL.format(q=quote(query))
    html_text = _fetch_bing_html(url)
    if not html_text:
        return []

    items: list[WebItem] = []
    seen: set[str] = set()
    for block in _BING_BLOCK.findall(html_text)[:8]:
        m = _BING_LINK.search(block)
        if not m:
            continue
        link, title = m.group(1), _strip_tags(m.group(2))
        if not title or not link.startswith(("http://", "https://")):
            continue
        if any(d in link for d in _SKIP_DOMAINS) or link in seen:
            continue
        seen.add(link)
        sm = _BING_SNIPPET.search(block)
        snippet = _strip_tags(sm.group(1)) if sm else ""
        items.append(WebItem(title=title, url=link, snippet=snippet[:300]))
    return items


async def _do_web_search(query: str) -> list[WebItem]:
    """搜索（httpx）+ 并行抓正文（MCP fetch）；前 max_pages 条带正文，其余只带摘要。"""
    results = search_bing(query)
    if not results:
        logger.warning("Bing 搜索无结果或解析失败，query=%s", query[:50])
        return []
    to_fetch, rest = (
        results[: settings.web_search_max_pages],
        results[settings.web_search_max_pages :],
    )
    fetched = await _fetch_all(to_fetch)
    return list(fetched) + rest[:2]


async def _fetch_single(url: str) -> Optional[WebItem]:
    """抓单个 URL（问题里带链接时直接查看该网页，不走 Bing 搜索）。"""
    session, close = await _open_session()
    try:
        result = await session.call_tool(
            "fetch", {"url": url, "max_length": settings.web_search_page_length}
        )
        if getattr(result, "isError", False):
            raise RuntimeError(f"fetch 失败: {result}")
        content = "\n".join(
            c.text for c in getattr(result, "content", []) if hasattr(c, "text")
        ).strip()
        if content.startswith("<error>") or not content:
            raise ValueError(content[:80] or "empty content")
        return WebItem(
            title=url,
            url=url,
            snippet="",
            content=content[: settings.web_search_page_length],
        )
    except Exception as e:
        logger.warning("直接抓取 %s 失败: %s", url, e)
        return None
    finally:
        await close()


def fetch_url(url: str) -> list[dict]:
    """直接抓取指定 URL（用户问题里带 http(s):// 时走此路径）。失败返回 []。

    冷启动偶发 ConnectError（fetch server 子进程首次连接不稳定），重试一次。
    """
    if not settings.web_search_enabled:
        return []
    for attempt in (1, 2):
        try:
            item = _call(lambda: _fetch_single(url), settings.web_search_timeout)
        except Exception as e:
            logger.warning("直接抓取 %s 第 %d 次失败: %s", url, attempt, e)
            item = None
        if item is not None:
            result = asdict(item)
            set_web_cache(url, [result])  # 页级缓存，重复问同一链接不重复抓
            return [result]
    return []


def web_search(query: str) -> list[dict]:
    """联网搜索入口（同步）：命中 rag:web 缓存直接返回，否则执行搜索+抓取并缓存。

    返回 [{title, url, snippet, content}, ...]；任何失败返回 []（调用方降级纯知识库）。
    """
    if not settings.web_search_enabled:
        return []
    cached = get_web_cache(query)
    if cached is not None:
        return cached
    # 冷启动（spawn fetch server）偶发失败，重试一次（每次都是全新连接）
    for attempt in (1, 2):
        try:
            items = _call(lambda: _do_web_search(query), settings.web_search_timeout)
            break
        except Exception as e:
            logger.warning("联网搜索第 %d 次失败，降级纯知识库：%s", attempt, e)
            items = []
    else:
        return []
    result = [asdict(i) for i in items]
    if result:
        set_web_cache(query, result)
    return result


def web_hash(items: list[dict]) -> str:
    """搜索结果哈希：并入回答缓存 key，搜索结果变化 → 重新生成。"""
    return hashlib.sha256(
        json.dumps(items, ensure_ascii=False).encode()
    ).hexdigest()[:16]
