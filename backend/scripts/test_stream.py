"""SSE 流式接口验证：兼容 Agent 事件（thought/tool_call/tool_result 前置），
断言 sources → delta* → done 契约与中文完整性。

用法：先启动 uvicorn，再运行
    python backend/scripts/test_stream.py
"""
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests

BASE = "http://localhost:8000/api"


def main() -> None:
    r = requests.post(
        f"{BASE}/chat/stream",
        json={"question": "行为面试怎么准备？STAR法则是什么？", "top_k": 4},
        stream=True,
    )
    assert r.status_code == 200, f"HTTP {r.status_code}"
    assert r.headers["content-type"].startswith("text/event-stream")

    events: list[tuple[str, dict]] = []
    for line in r.iter_lines(decode_unicode=True):
        if not line:
            continue
        if line.startswith("event: "):
            events.append((line[7:], {}))
        elif line.startswith("data: ") and events:
            events[-1] = (events[-1][0], json.loads(line[6:]))

    names = [e[0] for e in events]
    assert names[-1] == "done", f"末尾应为 done: {names}"
    assert "error" not in names, f"不应出现 error: {names}"
    assert "sources" in names, f"应出现 sources 事件: {names}"

    # Agent 模式下 sources 可多次累积（每次检索后全量发出）→ 取最后一次
    sources = [e[1]["sources"] for e in events if e[0] == "sources"][-1]
    assert sources, "sources 不应为空"
    done = events[-1][1]
    answer = done["answer"]
    # 引用编号从检索到的 sources 序号起算：不能死板要求 "[1]"，改为校验存在任意 [n]
    assert re.search(r"\[\d+\]", answer), "回答应含引用标注 [n]"
    assert done["source_count"] == len(sources)
    assert isinstance(done.get("tool_trace", []), list), "done 应携带 tool_trace"

    # 中文完整性：delta 拼接应等于 done 的完整回答（工具轮 DeepSeek content 为空，不污染）
    streamed = "".join(e[1]["content"] for e in events if e[0] == "delta")
    assert streamed == answer, "流式拼接与完整回答不一致"

    print(f"[ok] 事件顺序: {' → '.join(names[:5])} … → {names[-1]}")
    print(f"[ok] delta 数: {sum(1 for n in names if n == 'delta')}, 引用数: {len(sources)}")
    print(f"[ok] 回答开头: {answer[:80]}…")
    print("[ok] SSE 流式验证通过 ✅")


if __name__ == "__main__":
    main()
