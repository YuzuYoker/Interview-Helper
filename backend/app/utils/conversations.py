"""会话持久化：SQLite 本地落盘（对话记录保留本地，重启不丢）。

- 每个会话 = 标题 + 消息序列（role / content / sources），sources 存 JSON；
- 服务端是历史权威来源：带 conversation_id 的请求直接读库取历史，
  不依赖前端回传（前端只负责展示），保证"每次打开就有上下文"；
- SQLite WAL 模式 + 每操作短连接 + 全局锁（uvicorn workers=1，无并发写压力）；
- 落盘 backend/data/conversations.db —— Docker 里落在 backend-data 卷上，
  随容器重启/重建而保留（与 uploads 同目录）。
"""
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from app.utils.config import settings

_db_path: Path | None = None
_initialized: set[Path] = set()
_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations(
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    summary TEXT
);
CREATE TABLE IF NOT EXISTS messages(
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    conv_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    sources TEXT NOT NULL DEFAULT '[]',
    tool_trace TEXT NOT NULL DEFAULT '[]',
    ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conv_id, seq);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """幂等迁移：旧库已有表时 CREATE TABLE IF NOT EXISTS 不会补列，需手动 ALTER。

    - messages.tool_trace：Agent 工具调用轨迹（SSE tool_call/tool_result 序列 JSON）
    - conversations.summary：会话总结（summarize_conversation 落库）
    """
    msg_cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
    if "tool_trace" not in msg_cols:
        conn.execute(
            "ALTER TABLE messages ADD COLUMN tool_trace TEXT NOT NULL DEFAULT '[]'"
        )
    conv_cols = {r[1] for r in conn.execute("PRAGMA table_info(conversations)").fetchall()}
    if "summary" not in conv_cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN summary TEXT")


def _db() -> Path:
    global _db_path
    if _db_path is None:
        _db_path = settings.conversations_db_abs
        _db_path.parent.mkdir(parents=True, exist_ok=True)
    if _db_path not in _initialized:  # 按路径幂等建表（支持测试换库路径）
        with _lock:
            conn = sqlite3.connect(str(_db_path))
            try:
                conn.executescript(_SCHEMA)
                _migrate(conn)
            finally:
                conn.close()
        _initialized.add(_db_path)
    return _db_path


@contextmanager
def _tx():
    # 先确保 schema（_db 内部有自己的锁），再进入本连接锁——避免 _lock 被两次获取死锁
    _db()
    with _lock:
        conn = sqlite3.connect(str(_db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _make_title(content: str, limit: int = 20) -> str:
    text = " ".join(content.split())
    return text if len(text) <= limit else text[:limit] + "…"


def create_conversation(title: str | None = None) -> dict:
    """新建会话，返回基本信息（首条消息发出后标题会自动替换）。"""
    cid = str(uuid.uuid4())
    now = _now()
    title = (title or "新对话").strip() or "新对话"
    with _tx() as conn:
        conn.execute(
            "INSERT INTO conversations(id, title, created_at, updated_at) VALUES(?,?,?,?)",
            (cid, title, now, now),
        )
    return {"id": cid, "title": title, "created_at": now, "updated_at": now, "msg_count": 0}


def list_conversations() -> list[dict]:
    """按最近活跃排序，附带消息条数（侧边栏展示用）。"""
    with _tx() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.title, c.created_at, c.updated_at,
                   (SELECT COUNT(*) FROM messages m WHERE m.conv_id = c.id) AS msg_count
            FROM conversations c
            ORDER BY c.updated_at DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def get_conversation(cid: str) -> dict | None:
    """会话详情（含全部消息，assistant 消息带 sources 引用 + tool_trace 轨迹）。"""
    with _tx() as conn:
        row = conn.execute(
            "SELECT id, title, created_at, updated_at, summary FROM conversations WHERE id=?",
            (cid,),
        ).fetchone()
        if row is None:
            return None
        msgs = conn.execute(
            "SELECT role, content, sources, tool_trace, ts FROM messages WHERE conv_id=? ORDER BY seq",
            (cid,),
        ).fetchall()
    return {
        **dict(row),
        "messages": [
            {
                "role": m["role"],
                "content": m["content"],
                "sources": json.loads(m["sources"]) if m["sources"] else [],
                "tool_trace": json.loads(m["tool_trace"]) if m["tool_trace"] else [],
                "ts": m["ts"],
            }
            for m in msgs
        ],
    }


def get_history(cid: str) -> list[dict]:
    """只取 role/content，供 RAG 作为对话历史（不含当前问题）。"""
    with _tx() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE conv_id=? ORDER BY seq", (cid,)
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def append_message(
    cid: str,
    role: str,
    content: str,
    sources: list | None = None,
    tool_trace: list | None = None,
) -> None:
    """追加消息；首条用户消息自动把标题换成问题摘要（LLM 标题后续覆盖）。"""
    now = _now()
    with _tx() as conn:
        conn.execute(
            "INSERT INTO messages(conv_id, role, content, sources, tool_trace, ts) "
            "VALUES(?,?,?,?,?,?)",
            (
                cid,
                role,
                content,
                json.dumps(sources or [], ensure_ascii=False),
                json.dumps(tool_trace or [], ensure_ascii=False),
                now,
            ),
        )
        conn.execute(
            "UPDATE conversations SET updated_at=? WHERE id=?", (now, cid)
        )
        if role == "user":
            row = conn.execute(
                "SELECT title FROM conversations WHERE id=?", (cid,)
            ).fetchone()
            if row is not None and row["title"] == "新对话":
                conn.execute(
                    "UPDATE conversations SET title=? WHERE id=?", (_make_title(content), cid)
                )


def update_conversation_title(cid: str, title: str) -> None:
    """更新会话标题（LLM 自动生成标题时调用）。"""
    title = " ".join((title or "").split())
    if not title:
        return
    with _tx() as conn:
        conn.execute(
            "UPDATE conversations SET title=?, updated_at=? WHERE id=?",
            (_make_title(title, limit=30), _now(), cid),
        )


def update_conversation_summary(cid: str, summary: str) -> None:
    """写入会话总结（summarize_conversation）。"""
    with _tx() as conn:
        conn.execute("UPDATE conversations SET summary=? WHERE id=?", (summary, cid))


def delete_conversation(cid: str) -> bool:
    """删除会话及其全部消息，返回是否存在。"""
    with _tx() as conn:
        cur = conn.execute("DELETE FROM conversations WHERE id=?", (cid,))
        conn.execute("DELETE FROM messages WHERE conv_id=?", (cid,))
        return cur.rowcount > 0
