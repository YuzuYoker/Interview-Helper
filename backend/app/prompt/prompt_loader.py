"""Prompt 模板加载：按名称从 backend/prompts 目录读取 .prompt 文件。

业务方只传逻辑名，不关心路径；提示词以外部文件维护。
"""
from pathlib import Path

# app/prompt/prompt_loader.py 的 parents[2] = backend/（项目后端根）
_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


def load_prompt(name: str) -> str:
    """读取 prompts/{name}.prompt 模板内容。"""
    path = _PROMPTS_DIR / f"{name}.prompt"
    return path.read_text(encoding="utf-8")
