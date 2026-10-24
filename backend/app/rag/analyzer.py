"""统一的多模态内容识别入口。

所有"需要模型看图"的上传内容（图片，以及后续可扩展的扫描件）都走这里：
DeepSeek OpenAI 兼容端点 + 同一个 DEEPSEEK_API_KEY，不再维护独立的 OCR 模型。

DeepSeek Vision（deepseek-flash）支持 JPEG / PNG / GIF / WebP，格式按文件内容识别，
单张图片 ≤32MB、请求体 ≤48MB（见官方 Vision 文档）。
"""
import base64
import mimetypes
from pathlib import Path

from openai import OpenAI

from app.utils.config import settings

# DeepSeek Vision 支持的图片类型（按扩展名映射 MIME；格式由服务端按内容再校验）
SUPPORTED_IMAGE_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

DOC_EXTRACT_PROMPT = (
    "这是一份面试相关材料。请完整提取图片中的全部文字，保留原始层级、"
    "版式与表格结构；如果图片中没有文字，请客观描述其中的关键信息"
    "（人物、图表、界面等）。不要额外解释。"
)

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    """复用同一个客户端；密钥/地址与聊天模型完全一致。"""
    global _client
    if not settings.deepseek_api_key:
        raise ValueError("DEEPSEEK_API_KEY 未配置，无法识别图片")
    if _client is None:
        _client = OpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            timeout=settings.agent_timeout,
        )
    return _client


def _image_data_url(path: str | Path, filename: str = "") -> str:
    """本地图片 → base64 data URL（DeepSeek 三种传图方式中最适合本地文件的一种）。"""
    p = Path(path)
    suffix = p.suffix.lower()
    mime = SUPPORTED_IMAGE_TYPES.get(suffix) or mimetypes.guess_type(filename or p.name)[0]
    if mime not in SUPPORTED_IMAGE_TYPES.values():
        raise ValueError(
            f"不支持的图片格式: {suffix or mime}（DeepSeek 支持 "
            f"{' / '.join(sorted({m.split('/')[-1].upper() for m in SUPPORTED_IMAGE_TYPES.values()}))}）"
        )
    b64 = base64.b64encode(p.read_bytes()).decode()
    return f"data:{mime};base64,{b64}"


def analyze_image(
    path: str | Path,
    filename: str = "",
    prompt: str = DOC_EXTRACT_PROMPT,
    max_tokens: int = 4096,
) -> str:
    """用多模态模型识别一张本地图片，返回提取/描述出的文字。"""
    resp = _get_client().chat.completions.create(
        model=settings.model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": _image_data_url(path, filename)}},
                ],
            }
        ],
        max_tokens=max_tokens,
    )
    text = (resp.choices[0].message.content or "").strip()
    if not text:
        raise ValueError("模型未返回图片内容")
    return text
