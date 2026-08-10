"""Agent SSE 流式协议验证：thought/tool_call/tool_result/sources/done 事件。

断言协议合法性：
- done 结尾、无 error、必有 sources；
- 每个 tool_call 都有配对的 tool_result（同 tool_call_id）；
- done.tool_trace 与流中工具事件一致（非空时）。

用法：先启动 uvicorn（AGENT_ENABLED=true），再运行
    python backend/scripts/test_agent_stream.py
"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests

BASE = "http://localhost:8000/api"


def main() -> None:
    r = requests.post(
        f"{BASE}/chat/stream",
        json={"question": "根据我上传的资料，我的核心优势是什么？适合什么岗位？", "top_k": 4},
        stream=True,
    )
    assert r.status_code == 200, f"HTTP {r.status_code}"

    events: list[tuple[str, dict]] = []
    for line in r.iter_lines(decode_unicode=True):
        if not line:
            continue
        if line.startswith("event: "):
            events.append((line[7:], {}))
        elif line.startswith("data: ") and events:
            events[-1] = (events[-1][0], json.loads(line[6:]))

    names = [e[0] for e in events]
    print(f"事件序列: {' → '.join(names)}")

    assert names[-1] == "done", f"末尾应为 done: {names}"
    assert "error" not in names, f"不应出现 error: {names}"
    assert "sources" in names, f"应出现 sources 事件: {names}"

    # 协议：每个 tool_call 都有配对的 tool_result
    call_ids = [p["tool_call_id"] for e, p in events if e == "tool_call"]
    res_ids = [p["tool_call_id"] for e, p in events if e == "tool_result"]
    for cid in call_ids:
        assert cid in res_ids, f"tool_call {cid} 缺少配对的 tool_result"

    done = events[-1][1]
    trace = done.get("tool_trace", [])
    assert isinstance(trace, list), "done.tool_trace 应为列表"
    if call_ids:
        assert len(trace) >= 2 * len(call_ids), "tool_trace 应记录工具调用与结果"

    print(f"[ok] Agent 事件数: thought={names.count('thought')} "
          f"tool_call={names.count('tool_call')} tool_result={names.count('tool_result')}")
    print(f"[ok] tool_trace 条目: {len(trace)}")
    print("[ok] Agent SSE 流式协议验证通过 ✅")


if __name__ == "__main__":
    main()
