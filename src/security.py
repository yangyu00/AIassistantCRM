# -*- coding: utf-8 -*-
"""安全工具：日志脱敏 + HTML 转义。

约定：
- 任何可能含手机号 / 身份证号的字符串，写日志前过一遍 mask_sensitive；
- 任何来自外部（IOP 标签、SFTP 库表、Excel 维表）的文本，拼进 HTML 前必须过 html_text；
  我们自己拼接的标签样式用 keep_html 标记放行。
"""
import html
import re

_MOBILE = re.compile(r'(?<!\d)(1[3-9]\d)(\d{4})(\d{4})(?!\d)')
_IDCARD = re.compile(r'(?<!\d)(\d{6})\d{8}(\d{3}[0-9Xx])(?!\d)')


def mask_sensitive(text) -> str:
    """手机号 -> 138****5678；18 位身份证 -> 前 6 后 4 打码。"""
    if not isinstance(text, str):
        text = str(text)
    text = _MOBILE.sub(r'\1****\3', text)
    text = _IDCARD.sub(r'\1********\2', text)
    return text


def html_text(value) -> str:
    """转义外部文本，换行转 <br>，防止库表/维表内容注入 HTML。"""
    if value is None:
        return ''
    return html.escape(str(value), quote=True).replace('\n', '<br>')


class keep_html(str):
    """标记“这段 HTML 是代码自己拼的、已转义过内容”，外层不再二次转义。"""
    __slots__ = ()


def render(value) -> str:
    """拼装最终话术：keep_html 原样输出，其余一律转义。"""
    if isinstance(value, keep_html):
        return str(value)
    return html_text(value)


def tag(text, *names, color=None) -> keep_html:
    """带样式的标签片段，内容先转义再包样式。"""
    escaped = html_text(text)
    for name in names:
        escaped = f'<{name}>{escaped}</{name}>'
    if color:
        escaped = f'<span style="color:{color};">{escaped}</span>'
    return keep_html(escaped)
