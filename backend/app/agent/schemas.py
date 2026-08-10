"""结构化输出模型：配合 `with_structured_output`（Pydantic 校验 LLM 输出）。

Agent 内所有"让 LLM 产出结构化数据"的地方（记忆抽取/标题/标签/评估/计划/文档策略）
统一走这些 Pydantic 模型。
"""
from pydantic import BaseModel, Field


class Title(BaseModel):
    """会话标题。"""
    title: str = Field(description="不超过 20 字的会话标题")


class SearchEvaluation(BaseModel):
    """搜索结果质量评估。"""
    quality: str = Field(description="good 或 poor")
    reason: str = Field(description="一句话原因")
    suggest: str = Field(description="stop / web_search / retrieve_more")


class DocumentTags(BaseModel):
    """文档标签/关键词/摘要。"""
    tags: list[str] = Field(default_factory=list, description="≤5 个中文标签")
    keywords: list[str] = Field(default_factory=list, description="≤5 个关键词")
    summary: str = Field(default="", description="一句话摘要")


class DocumentPlan(BaseModel):
    """文档解析策略。"""
    type: str = Field(description="pdf / docx / xlsx / txt / image_ocr / unsupported")
    strategy: str = Field(description="解析策略一句话")
    reason: str = Field(description="选择原因一句话")


class SubTask(BaseModel):
    """Plan-and-Execute 子任务。"""
    id: int
    title: str = Field(description="子任务标题")
    objective: str = Field(description="要做什么")
    tools_hint: str = Field(default="", description="建议工具，如 retrieve_knowledge/web_search")


class TaskPlan(BaseModel):
    """复杂问题拆解。"""
    tasks: list[SubTask] = Field(default_factory=list, description="≤4 个子任务；简单问题为空列表")


class MemoryFact(BaseModel):
    """长期记忆事实（从对话中抽取）。"""
    key: str = Field(description="语义化键名，如 目标岗位/期望薪资/技能/偏好")
    content: str = Field(description="事实内容一句话")
    category: str = Field(default="fact", description="类别：user_profile / preference / decision / conclusion / fact")


class MemoryExtraction(BaseModel):
    """从一轮对话中抽取的长期记忆。"""
    memories: list[MemoryFact] = Field(default_factory=list, description="本轮对话值得记住的用户事实")


class Intent(BaseModel):
    """用户问题意图抽取。"""
    needs_knowledge: bool = Field(default=True, description="是否需要知识库检索")
    needs_web: bool = Field(default=False, description="是否涉及时效信息/需联网核实")
    search_query: str = Field(default="", description="需要联网时的简洁搜索词（天气类写'<城市>天气预报'）")
    memory_query: str = Field(default="", description="检索长期记忆的关键词；没有为空")
