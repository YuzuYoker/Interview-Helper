"""Interview Helper 种子知识库：将 interview-master skill 的 references 灌入向量库。

用法（需先停止 uvicorn 服务，Qdrant local 模式单实例）：
    python backend/scripts/seed_knowledge.py
"""
import sys
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rag.splitter import get_splitter
from app.rag.vectorstore import get_store
from app.utils.config import ROOT_DIR, settings

# 本项目内（不依赖 /tmp clone，用户删除克隆目录后仍可重建）
REFERENCES_DIR = (
    ROOT_DIR / ".claude" / "skills" / "interview-master" / "references"
)

SUPPORTED = {".md", ".txt", ".markdown"}


def main() -> None:
    settings.init_env()
    import argparse

    parser = argparse.ArgumentParser(description="Interview Helper 种子知识库灌入")
    parser.add_argument("--force", action="store_true", help="collection 非空时仍重灌")
    args = parser.parse_args()

    # 幂等守卫：参考库已有数据则跳过（容器重启/entrypoint 重复执行安全）
    # 种子只灌入 kb_references（与用户上传的 kb_documents 分离）
    from app.rag.vectorstore import collection_count

    if not args.force:
        count = collection_count(settings.qdrant_reference_collection)
        if count > 0:
            print(f"参考库非空（{count} 个向量），跳过 seed（--force 可重灌）")
            return

    files = sorted(
        p for p in REFERENCES_DIR.iterdir() if p.suffix.lower() in SUPPORTED
    )
    if not files:
        print(f"未找到参考资料: {REFERENCES_DIR}")
        sys.exit(1)

    docs = []
    for f in files:
        text = f.read_text(encoding="utf-8")
        if text.strip():
            from langchain_core.documents import Document

            docs.append(
                Document(
                    page_content=text,
                    metadata={"source": f.name, "page": 1, "type": "interview-reference"},
                )
            )
    print(f"加载 {len(docs)} 份面试参考资料")

    chunks = get_splitter().split_documents(docs)
    doc_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    for i, c in enumerate(chunks):
        c.metadata.update(
            {
                "doc_id": doc_id,
                "filename": f"面试资料库（{len(files)}篇参考文献）",
                "chunk_index": i,
                "created_at": now,
            }
        )
    ids = [
        str(uuid.uuid5(uuid.NAMESPACE_DNS, c.page_content)) for c in chunks
    ]
    from app.utils.embedding import get_embedding_model

    get_store(
        get_embedding_model(), collection=settings.qdrant_reference_collection
    ).add_documents(chunks, ids=ids)
    print(f"已入库 {len(chunks)} 个分块（doc_id={doc_id}）")
    print("提示：同一内容重复执行会按内容 ID 自动去重")


if __name__ == "__main__":
    main()
