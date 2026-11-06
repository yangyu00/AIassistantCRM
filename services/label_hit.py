# -*- coding: utf-8 -*-
"""
标签「命中」判定。
"""
from typing import Any

from src.log_util import my_log

# 视为「命中」的取值（已 strip、大小写不敏感）
TRUTHY = {"是", "1", "y", "yes", "有", "true", "t"}
# 视为「未命中」的取值
FALSY = {"否", "0", "n", "no", "无", "false", "f", "", "0.00", "null", "none"}


def is_hit(value: Any, product_id: str = "", label_id: str = "") -> bool:
    text = str(value).strip() if value is not None else ""
    low = text.lower()
    if low in TRUTHY:
        return True
    if low in FALSY:
        return False
    my_log.warning(
        f"标签值无法识别是否为命中，按未命中处理: productId={product_id}, "
        f"labelId={label_id}, value={text!r}"
    )
    return False
