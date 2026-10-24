"""文档加载器注册表：{扩展名: 加载函数}，加载函数返回 list[Document]。

PDF 按页读取，每页一个 Document（保留页码 metadata，分块后 chunk 继承页码）；
图片统一走 app.rag.analyzer（DeepSeek 多模态，与聊天模型同一 API Key）。
"""
from typing import Callable

from langchain_core.documents import Document


def load_pdf(path: str, filename: str) -> list[Document]:
    from pypdf import PdfReader

    reader = PdfReader(path)
    docs = []
    for i, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            docs.append(
                Document(
                    page_content=text,
                    metadata={"source": filename, "page": i, "type": "pdf"},
                )
            )
    return docs


def load_docx(path: str, filename: str) -> list[Document]:
    from docx import Document as DocxDocument

    doc = DocxDocument(path)
    text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    return [
        Document(
            page_content=text,
            metadata={"source": filename, "page": 1, "type": "docx"},
        )
    ]


def load_xlsx(path: str, filename: str) -> list[Document]:
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    docs = []
    for ws in wb.worksheets:  # 每张 sheet 一个 Document，行号入内容
        lines = []
        for idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
            vals = [str(c) for c in row if c is not None]
            if vals:
                lines.append(f"第{idx}行: " + " | ".join(vals))
        if lines:
            docs.append(
                Document(
                    page_content="\n".join(lines),
                    metadata={"source": filename, "sheet": ws.title, "page": 1, "type": "xlsx"},
                )
            )
    return docs


def load_txt(path: str, filename: str) -> list[Document]:
    with open(path, encoding="utf-8") as f:
        text = f.read()
    return [
        Document(
            page_content=text,
            metadata={"source": filename, "page": 1, "type": "txt"},
        )
    ]


def load_image(path: str, filename: str) -> list[Document]:
    """图片识别：走统一的 analyzer（DeepSeek 多模态，不再有独立 OCR 模型）。"""
    from app.rag.analyzer import analyze_image

    text = analyze_image(path, filename)
    return [
        Document(
            page_content=text,
            metadata={"source": filename, "page": 1, "type": "image"},
        )
    ]


# 注册表：支持的上传类型（图片格式与 DeepSeek Vision 支持范围一致）
LOADERS: dict[str, Callable[[str, str], list[Document]]] = {
    ".pdf": load_pdf,
    ".docx": load_docx,
    ".xlsx": load_xlsx,
    ".txt": load_txt,
    ".jpg": load_image,
    ".jpeg": load_image,
    ".png": load_image,
    ".gif": load_image,
    ".webp": load_image,
}
