"""请求上下文：用 ContextVar 传递一次请求/一次图执行的上下文变量。

当前维护 request_id（日志链路）与 conversation_id（记忆槽）；并发协程间互不干扰。
"""
from contextvars import ContextVar

request_id_ctx_var: ContextVar[str] = ContextVar("request_id", default="")
conversation_id_ctx_var: ContextVar[str] = ContextVar("conversation_id", default="")
