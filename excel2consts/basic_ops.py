# -*- coding: utf-8 -*-
"""维表分片渲染工具。

原来这里用 ast「读源码 → 删节点 → 加节点 → 写回」反复改同一个 src/consts.py：
多脚本轮流改同一文件，后写的一方会覆盖先写的一方（维表丢字典的根因之一），
而且 ast.unparse 会把格式和注释冲掉。
现在每个脚本只把自己要的 dict 渲染成独立分片文件，合并交给 dim_table。
"""
import ast
import os
import re
import tempfile
from pathlib import Path

PART_PREFIX = 'consts_part_'


def render_module(name_to_dict) -> str:
    """{变量名: dict} → 一段合法的 Python 源码（分片文件内容）。"""
    body = []
    for node_name, my_dict in name_to_dict.items():
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', node_name):
            raise ValueError(f'非法的维表变量名: {node_name!r}')
        body.append(ast.Assign(
            targets=[ast.Name(id=node_name, ctx=ast.Store())],
            value=ast.Dict(keys=[ast.Constant(value=k) for k in my_dict.keys()],
                           values=[_literal(v) for v in my_dict.values()])))
    module = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(module)
    return ast.unparse(module)


def _literal(value):
    if isinstance(value, dict):
        return ast.Dict(keys=[ast.Constant(value=k) for k in value.keys()],
                        values=[_literal(v) for v in value.values()])
    if isinstance(value, (list, tuple)):
        cls = ast.List if isinstance(value, list) else ast.Tuple
        return cls(elts=[_literal(v) for v in value])
    if value is None or isinstance(value, (str, int, float, bool)):
        return ast.Constant(value=value)
    raise TypeError(f'维表值不支持的类型: {type(value).__name__}')


def write_part_atomic(file_path, source: str) -> None:
    """[FIX] 原子写分片：先写同目录临时文件并 flush+fsync，再 os.replace 覆盖。
    原来直接 write_text，写到一半进程被杀/掉电会留下半个分片，
    下次启动 merge_part_files 可能读到损坏内容或混入旧数据。
    """
    file_path = Path(file_path)
    fd, tmp = tempfile.mkstemp(prefix=file_path.name + '.', suffix='.tmp', dir=str(file_path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(source)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, file_path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def format_script_block(text: str) -> str:
    """话术里每段「小标题：内容」的小标题加粗标蓝，与原逻辑一致；无冒号的整段返回。

    [FIX] tail（小标题后的内容）原来原样拼进 HTML，维表里出现 < > & 时会
    撑坏页面结构，这里做转义；head 来自正则清洗后的标题，同样转义。
    """
    out_lines = []
    for line in str(text or '').splitlines():
        line = line.strip()
        if not line:
            continue
        head, _, tail = line.replace(':', '：').partition('：')
        head = escape(head)
        tail = escape(tail)
        head = re.sub(r'^\d+\s?[.、]\s?', '', head).strip()
        if not head:
            out_lines.append(tail)
            continue
        out_lines.append(f'<strong><span style="color: blue;">{head}：</span></strong>{tail}')
    return '\n'.join(out_lines)


def escape(text) -> str:
    """[FIX] HTML 转义，与 src.security.html_text 保持一致的行为"""
    return (str(text or '')
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
            .replace("'", '&#39;'))


def keep_chinese_and_alphanumeric(text):
    return re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9]+', '', str(text))
