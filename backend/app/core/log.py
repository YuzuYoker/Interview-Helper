"""日志：每条日志注入 request_id，便于按请求追踪 agent 链路。"""
import logging

from app.core.context import request_id_ctx_var


class RequestIdFilter(logging.Filter):
    """把 ContextVar 中的 request_id 注入到日志记录的 extra。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx_var.get() or "-"
        return True


logger = logging.getLogger("interview.agent")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] [%(request_id)s] %(name)s: %(message)s")
    )
    logger.addHandler(_handler)
    logger.addFilter(RequestIdFilter())
    logger.setLevel(logging.INFO)
    logger.propagate = False
