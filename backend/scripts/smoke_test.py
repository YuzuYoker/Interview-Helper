"""端到端冒烟测试：health → 上传测试文档 → 提问 → 列表 → 删除。

用法：先启动服务，再运行
    python backend/scripts/smoke_test.py [BASE_URL]
"""
import sys
import tempfile
import time
from pathlib import Path

# Windows 控制台默认 GBK，先切 UTF-8 避免打印 emoji/中文报错
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import requests

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000/api")

TEST_CONTENT = """公司年假制度
一、适用范围：本制度适用于公司全体员工。
二、年假天数：工作满1年不满10年的员工，每年享有5天带薪年假；工作满10年不满20年的员工，每年享有10天带薪年假；工作满20年以上的员工，每年享有15天带薪年假。
三、请假流程：员工需提前3个工作日通过OA系统提交年假申请，经部门主管审批通过后方可休假。
四、年假有效期：当年年假须在当年12月31日前休完，未休完的年假不予跨年累计（法律规定的法定年假除外）。
五、其他事项：新入职员工自入职之日起连续工作满1年后方可享受带薪年假。
"""


def main() -> None:
    # 0. 健康检查
    health = requests.get(f"{BASE}/health").json()
    assert health["status"] == "ok", f"health check failed: {health}"
    print("[ok] health:", health)

    # 1. 上传测试文档（异步：202 + 轮询 status）
    with tempfile.NamedTemporaryFile(
        "w", suffix=".txt", encoding="utf-8", delete=False
    ) as f:
        f.write(TEST_CONTENT)
        tmp = f.name
    try:
        with open(tmp, "rb") as f:
            r = requests.post(
                f"{BASE}/documents/upload",
                files={"file": (Path(tmp).name, f, "text/plain")},
            )
        assert r.status_code == 202, f"upload failed: {r.text}"
        up = r.json()
        assert up["status"] == "processing", f"unexpected: {up}"
        print(f"[ok] uploaded: doc_id={up['document_id']} (后台处理中)")
        for _ in range(60):  # 轮询后台任务（最长 2 分钟）
            st = requests.get(
                f"{BASE}/documents/{up['document_id']}/status"
            ).json()
            if st["status"] == "done":
                assert st["chunk_count"] >= 1, f"no chunks: {st}"
                print(f"[ok] 后台完成: chunks={st['chunk_count']}")
                break
            if st["status"] == "error":
                raise AssertionError(f"后台任务失败: {st}")
            time.sleep(2)
        else:
            raise AssertionError("后台任务超时")
    finally:
        Path(tmp).unlink(missing_ok=True)

    # 2. 提问（不依赖 LLM key：只验证检索链路）
    r = requests.post(
        f"{BASE}/chat", json={"question": "公司年假有几天？", "top_k": 4}
    )
    if r.status_code == 503:
        print("[skip] chat 未验证：DEEPSEEK_API_KEY 未配置（检索链路待 key 填好后验证）")
    else:
        assert r.status_code == 200, f"chat failed: {r.text}"
        body = r.json()
        assert body["sources"], "未检索到引用片段"
        assert any(
            s["filename"] == up["filename"] for s in body["sources"]
        ), "sources 未命中上传文档"
        print("[ok] answer:", body["answer"][:200])
        print(f"[ok] sources: {len(body['sources'])} 条")

    # 3. 列表
    lst = requests.get(f"{BASE}/documents").json()
    assert lst["total"] >= 1, "列表为空"
    print(f"[ok] list: total={lst['total']}")

    # 4. 删除
    r = requests.delete(f"{BASE}/documents/{up['document_id']}")
    assert r.status_code == 200, f"delete failed: {r.text}"
    print("[ok] deleted:", up["document_id"])

    print("\n全部冒烟测试通过 ✅")


if __name__ == "__main__":
    main()
