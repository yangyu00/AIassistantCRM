# -*- coding: utf-8 -*-
from pathlib import Path
"""维表快照：consts.py 落盘后，由主进程整体读入一次，存成只读快照。

替换原来的 importlib.reload(consts) 热更新：
- 业务代码只通过 get() 拿到同一个不可变 Mapping 引用，读到的一定是一整份自洽的数据；
- 更新 = 构造新快照后一次性替换引用（详见 refresh / _Snapshot），不存在半成品窗口；
- 新快照加载失败时保留旧快照，服务继续用旧数据跑；
- 子进程不再负责 reload，只负责把 consts.py 原子替换掉，语义与主进程一致。
"""
import importlib
import threading
from types import MappingProxyType
from typing import Any, Mapping

try:
    from src.log_util import my_log
except Exception:  # 日志模块不可用时不阻断维表加载
    import logging
    my_log = logging.getLogger('dimension_tables')

CONSTS_MODULE = 'src.consts'

_REQUIRED = ('product_selling_point_match_dict', 'cut_in_script_dict', 'label_name_mapping')


class _Snapshot:
    """只读快照：属性访问返回 MappingProxyType，底层 dict 外部改不了。"""

    __slots__ = ('_data', 'version', 'source_mtime')

    def __init__(self, data: Mapping[str, Any], version: int, source_mtime: float = 0.0):
        self._data = {k: MappingProxyType(v) if isinstance(v, dict) else v
                      for k, v in data.items()}
        self.version = version
        self.source_mtime = source_mtime

    def __getattr__(self, item):
        try:
            return self._data[item]
        except KeyError:
            raise AttributeError(item) from None

    def __getitem__(self, item):
        return self._data[item]

    def __contains__(self, item):
        return item in self._data

    def as_dict(self) -> dict:
        return dict(self._data)


    def __repr__(self):
        sizes = {k: len(v) for k, v in self._data.items() if hasattr(v, '__len__')}
        return f'<DimensionSnapshot v{self.version} {sizes}>'


_lock = threading.RLock()
_snapshot = _Snapshot({k: {} for k in _REQUIRED}, version=0)


def _load_from_module(path=None) -> dict:
    """在独立命名空间里执行 consts.py，不 import、不进 sys.modules，也不触发 reload。"""
    import importlib.util
    if path is None:
        parent = sys.modules.get(CONSTS_MODULE.rsplit('.', 1)[0])
        f = getattr(parent, '__file__', None)
        path = Path(f).with_name('consts.py') if f else Path('src/consts.py')
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f'维表常量文件不存在: {path}')
    code = compile(path.read_text(encoding='utf-8'), str(path), 'exec')
    ns = {}
    exec(code, ns)
    return {k: ns.get(k, {}) for k in _REQUIRED}


def load_snapshot(source_mtime: float = 0.0, source_path=None) -> _Snapshot:
    """从当前磁盘上的 consts.py 读一份新快照（不替换全局）。加载失败抛异常。"""
    data = _load_from_module(source_path)
    missing = [k for k in _REQUIRED if not isinstance(data.get(k), dict) or not data[k]]
    if missing:
        raise ValueError(f'consts.py 缺少必需的字典: {missing}')
    return _Snapshot(data, version=_snapshot.version + 1, source_mtime=source_mtime)


def refresh(source_mtime: float = 0.0, source_path=None) -> _Snapshot:
    """加载并原子替换全局快照；失败保留旧快照。"""
    global _snapshot
    with _lock:
        try:
            new = load_snapshot(source_mtime, source_path)
        except Exception as e:
            my_log.error(f'新维表 consts.py 加载失败，保留旧快照: {e}')
            return _snapshot
        if new.as_dict() == _snapshot.as_dict():
            my_log.info(f'维表内容未变化，跳过切换（consts mtime={source_mtime}）')
            return _snapshot
        _snapshot = new
        my_log.info(f'维表快照已切换: {new!r}')
        return _snapshot


def get() -> _Snapshot:
    """业务代码统一入口：拿到当前只读快照。"""
    return _snapshot


def preflight(excel_path, builder) -> bool:
    """先用 Excel 试跑一次转换，不通过就不动 consts.py。"""
    try:
        builder(excel_path)
        return True
    except Exception as e:
        my_log.error(f'维表预校验失败，本次跳过更新: {e}')
        return False
