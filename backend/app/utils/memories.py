"""长期记忆：SQLite 事实表（跨会话持久，独立于对话记录）。

memories 表存用户画像/偏好/决策/结论等事实，agent 通过 store_memory/search_memory
工具读写，dynamic_prompt 中间件每请求把相关记忆注入系统提示。

复用 conversations.db 同一个文件（WAL + 短连接 + 本模块独立锁），表独立。
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
CREATE TABLE IF NOT EXISTS memories(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL DEFAULT 'fact',
    key TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    access_count INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_key ON memories(key);
"""


def _db() -> Path:
    global _db_path
    if _db_path is None:
        _db_path = settings.conversations_db_abs
        _db_path.parent.mkdir(parents=True, exist_ok=True)
    if _db_path not in _initialized:
        with _lock:
            conn = sqlite3.connect(str(_db_path))
            try:
                conn.executescript(_SCHEMA)
            finally:
                conn.close()
        _initialized.add(_db_path)
    return _db_path


@contextmanager
def _tx():
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


def save_memory(key: str, content: str, category: str = "fact", source: str = "") -> dict:
    """写入/更新一条记忆（按 key 唯一，upsert）。"""
    key = " ".join((key or "").strip().split())
    content = " ".join((content or "").strip().split())
    if not key or not content:
        return {"ok": False, "error": "key/content 不能为空"}
    now = _now()
    with _tx() as conn:
        conn.execute(
            """
            INSERT INTO memories(category, key, content, source, created_at, updated_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(key) DO UPDATE SET
                category=excluded.category, content=excluded.content,
                source=excluded.source, updated_at=excluded.updated_at
            """,
            (category, key, content, source, now, now),
        )
        row = conn.execute("SELECT id FROM memories WHERE key=?", (key,)).fetchone()
        return {"ok": True, "id": row["id"], "key": key}


def search_memories(query: str, category: str = "", limit: int = 5) -> list[dict]:
    """按 key/内容模糊匹配检索记忆（动态提示注入用）。"""
    with _tx() as conn:
        sql = "SELECT * FROM memories WHERE (key LIKE ? OR content LIKE ?)"
        args: list = [f"%{query}%", f"%{query}%"]
        if category:
            sql += " AND category=?"
            args.append(category)
        sql += " ORDER BY access_count DESC, updated_at DESC LIMIT ?"
        args.append(limit)
        rows = conn.execute(sql, args).fetchall()
        ids = [r["id"] for r in rows]
        if ids:
            conn.executemany(
                "UPDATE memories SET access_count=access_count+1 WHERE id=?",
                [(i,) for i in ids],
            )
        return [dict(r) for r in rows]


def list_memories(category: str = "") -> list[dict]:
    with _tx() as conn:
        if category:
            rows = conn.execute(
                "SELECT * FROM memories WHERE category=? ORDER BY updated_at DESC", (category,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM memories ORDER BY updated_at DESC").fetchall()
        return [dict(r) for r in rows]


def get_memory(memory_id: int) -> dict | None:
    with _tx() as conn:
        row = conn.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
        return dict(row) if row else None


def delete_memory(memory_id: int) -> bool:
    with _tx() as conn:
        cur = conn.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        return cur.rowcount > 0


def memory_stats() -> dict:
    with _tx() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"]
        by_cat = {
            r["category"]: r["c"]
            for r in conn.execute("SELECT category, COUNT(*) c FROM memories GROUP BY category").fetchall()
        }
        return {"total": total, "by_category": by_cat}
