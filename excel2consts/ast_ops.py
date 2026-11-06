# -*- coding: utf-8 -*-
import ast
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

_ALLOWED = (str, int, float, bool, type(None), list, tuple, dict)


def _literal(value: Any) -> ast.expr:
    if not isinstance(value, _ALLOWED):
        raise TypeError(f"不支持写入 consts 的值类型: {type(value).__name__!r}，值={value!r}")
    if isinstance(value, float) and (value != value or value in (float('inf'), float('-inf'))):
        raise ValueError(f"非有限浮点数不能写入 consts: {value!r}")
    if isinstance(value, dict):
        return ast.Dict(keys=[_literal(k) for k in value.keys()],
                        values=[_literal(v) for v in value.values()])
    if isinstance(value, (list, tuple)):
        node = ast.List if isinstance(value, list) else ast.Tuple
        return node(elts=[_literal(v) for v in value])
    return ast.Constant(value=value)


def _assign(name: str, value: Any) -> ast.Assign:
    node = ast.Assign(targets=[ast.Name(id=name, ctx=ast.Store())], value=_literal(value))
    ast.fix_missing_locations(node)
    return node


def load_module(path: Path) -> ast.Module:
    """读取并解析源文件；文件不存在时返回空模块。"""
    if not path.exists():
        return ast.Module(body=[], type_ignores=[])
    return ast.parse(path.read_text(encoding='utf-8'), filename=str(path))


def replace_assign(tree: ast.Module, name: str, value: Any) -> None:
    """同名赋值整条替换（保留原位置）；原来没有就追加到末尾。只处理顶层赋值。"""
    new_node = _assign(name, value)
    for i, node in enumerate(tree.body):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            ast.copy_location(new_node, node)
            tree.body[i] = new_node
            return
    if tree.body:
        ast.copy_location(new_node, tree.body[-1])
    tree.body.append(new_node)


def replace_assigns(tree: ast.Module, items: dict) -> None:
    for name, value in items.items():
        replace_assign(tree, name, value)


def dump_module(tree: ast.Module) -> str:
    """ast.unparse 后自查：必须能重新 parse，且所有目标赋值都存在。"""
    ast.fix_missing_locations(tree)
    source = ast.unparse(tree)
    ast.parse(source)  # 语法自检，失败直接抛异常，不会落到磁盘
    return source + '\n'


def write_module_atomic(path: Path, source: str, required_names: Iterable[str] = ()) -> None:
    """原子落盘：同目录临时文件 -> fsync -> os.replace。

    任意一步失败都只留下临时文件（由 finally 清理），path 保持旧内容。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(source)
            f.flush()
            os.fsync(f.fileno())
        # 临时文件也必须是合法语法，并且关键字典都在
        tree = ast.parse(Path(tmp).read_text(encoding='utf-8'))
        names = {t.id for n in tree.body if isinstance(n, ast.Assign)
                 for t in n.targets if isinstance(t, ast.Name)}
        missing = [n for n in required_names if n not in names]
        if missing:
            raise ValueError(f"生成结果缺少必需赋值: {missing}")
        os.replace(tmp, path)  # 同文件系统内原子替换
        _fsync_dir(path.parent)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _fsync_dir(dir_path: Path) -> None:
    """确保目录项（replace 结果）落盘；不支持的平台忽略。"""
    try:
        fd = os.open(str(dir_path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
