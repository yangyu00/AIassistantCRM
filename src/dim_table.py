# -*- coding: utf-8 -*-
"""维表快照：让热更新对已导入的模块真正真正生效，并保证两张维表互不覆盖。

【原有 bug】
1. 卖点维表与标签转换维表各跑一个生成脚本，都往同一个 src/consts.py 写，
   后跑的脚本先删再写自己的字典，把先跑那个的字典整段冲掉。现象：线上只剩
   label_name_mapping，product_selling_point_match_dict 消失，所有产品走
   ProductInfoRaw 兜底、返回 code=2「无产品名称」。
2. 生成脚本直接 import src.consts，受 sys.modules 缓存影响，第二轮读到的是
   第一轮的内存副本，磁盘上的中间态和内存不一致。
3. 业务代码多是 `from src.consts import xxx_dict` 或把 consts.YYY 缓存成实例
   属性，importlib.reload(consts) 只换模块里的名字，这些已经绑走的引用仍指向
   旧 dict，于是「维表更新了、话术没变」，不重启不恢复。

【怎么修】
- 业务代码只从本模块取数，本模块持有单例容器，热更新时**原地清空再灌入**，
  容器对象身份不变，已经 import 走的名字自动看到新内容；
- 每个生成脚本只写自己的临时分片（consts_part_*.py），全部编译成功后由
  dim_table 合并成一份再原子替换 consts.py；任何一张表失败都保留上一版本。
"""
import os
import re
import shutil
import threading
from pathlib import Path
from typing import Any, Dict, List, Tuple

from src.log_util import my_log

MAIN_PATH = Path(__file__).resolve().parent.parent
CONSTS_FILE = MAIN_PATH / 'src' / 'consts.py'
BAK_FILE = MAIN_PATH / 'src' / 'consts.py.bak'
PART_PREFIX = 'consts_part_'
PART_SUFFIX = '.py'

# 维表 Excel → 负责把它编译成 consts 分片的脚本
DIM_TABLES: Dict[str, str] = {
    '卖点维表.xlsx': 'excel2consts/product_selling_point_match.py',
    '标签转换维表.xlsx': 'excel2consts/labelName1_to_labelName2.py',
}

# 合并后必须存在的三个字典，缺任何一个都说明生成脚本没跑完
REQUIRED_ATTRS: Tuple[str, ...] = (
    'product_selling_point_match_dict',
    'cut_in_script_dict',
    'label_name_mapping',
)

_lock = threading.RLock()
_version = 0

# 单例容器：业务代码拿到的永远是这几个对象，热更新只改内容不换对象
product_selling_point_match_dict: Dict[str, Any] = {}
cut_in_script_dict: Dict[str, Any] = {}
label_name_mapping: Dict[str, Any] = {}


def _containers() -> Dict[str, Dict[str, Any]]:
    return {
        'product_selling_point_match_dict': product_selling_point_match_dict,
        'cut_in_script_dict': cut_in_script_dict,
        'label_name_mapping': label_name_mapping,
    }


def version() -> int:
    return _version


def _load_namespaces(source: str):
    """把一段 consts 源码编译出来，返回 {字典名: dict}。不走 sys.modules。"""
    import types
    module = types.ModuleType('_dim_table_eval')
    exec(compile(source, '<dim_table>', 'exec'), module.__dict__)
    return {a: getattr(module, a) for a in REQUIRED_ATTRS if hasattr(module, a)}


def load_from_source(source: str) -> int:
    """原地灌入一份完整 consts 源码，返回新版本号。"""
    ns = _load_namespaces(source)
    missing = [a for a in REQUIRED_ATTRS if a not in ns]
    if missing:
        raise AttributeError(f'维表内容缺少字段: {missing}')

    with _lock:
        boxes = _containers()
        for attr, box in boxes.items():
            data = ns[attr]
            if not isinstance(data, dict):
                raise TypeError(f'{attr} 应为 dict，实际是 {type(data).__name__}')
            box.clear()
            box.update(data)
        globals()['_version'] += 1
        sizes = {k: len(v) for k, v in boxes.items()}
    my_log.info(f'维表已加载，版本 v{_version}，条目数 {sizes}')
    return _version


def load_current() -> bool:
    """启动时加载磁盘上已有的 consts.py。不存在或损坏时保持空维表，不阻断启动。"""
    try:
        if not CONSTS_FILE.exists():
            my_log.warning('src/consts.py 尚不存在（维表还没编译过），先以空维表启动')
            return False
        load_from_source(CONSTS_FILE.read_text(encoding='utf-8'))
        return True
    except Exception as exc:
        my_log.error(f'维表初始加载失败（将以空维表启动，可 POST /api/dimTable/reload 重试）: {exc}')
        return False


def merge_part_files() -> Tuple[bool, str]:
    """把所有 consts_part_*.py 分片合并成完整 consts 并原子替换，失败则整体保留旧版。"""
    parts = sorted(MAIN_PATH.joinpath('src').glob(f'{PART_PREFIX}*{PART_SUFFIX}'))
    if not parts:
        return False, '没有找到任何维表分片文件'

    merged: Dict[str, Any] = {}
    for p in parts:
        try:
            ns = _load_namespaces(p.read_text(encoding='utf-8'))
        except Exception as exc:
            return False, f'分片 {p.name} 解析失败: {exc}'
        for attr, data in ns.items():
            if not isinstance(data, dict):
                return False, f'{p.name} 的 {attr} 应为 dict，实际 {type(data).__name__}'
            merged.setdefault(attr, {}).update(data)

    missing = [a for a in REQUIRED_ATTRS if a not in merged]
    if missing:
        return False, f'合并后缺少字段 {missing}（分片: {[p.name for p in parts]}）'

    # 先校验再落盘：内存里试灌一遍，出问题不动现有 consts.py
    try:
        with _lock:
            ns = _load_namespaces(_render(merged))
    except Exception as exc:
        return False, f'合并结果校验不通过: {exc}'

    try:
        if CONSTS_FILE.exists():
            shutil.copy2(CONSTS_FILE, BAK_FILE)
        CONSTS_FILE.write_text(_render(merged), encoding='utf-8')
        for p in parts:
            p.unlink()
        load_from_source(CONSTS_FILE.read_text(encoding='utf-8'))
    except Exception as exc:
        return False, f'维表落盘失败（继续使用上一版本）: {exc}'
    return True, ''


def _render(merged: Dict[str, Any]) -> str:
    import ast
    body = [
        ast.Assign(targets=[ast.Name(id=attr, ctx=ast.Store())],
                   value=ast.Dict(keys=[ast.Constant(value=k) for k in d],
                                  values=[ast.Constant(value=v) for v in d.values()]))
        for attr, d in merged.items()
    ]
    module = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(module)
    return ast.unparse(module)


def selling_point(product_id: str) -> Tuple[str, str, str]:
    """按产品ID取 (产品名称, 生成话术, 标签ID)，缺失时返回空元组而非抛异常。"""
    with _lock:
        item = product_selling_point_match_dict.get(product_id)
    if not item:
        return ('', '', '')
    name, selling_text, label_ids = (list(item) + ['', ''])[:3]
    return (name or '', selling_text or '', label_ids or '')


def has_selling_point(product_id: str) -> bool:
    with _lock:
        return product_id in product_selling_point_match_dict


def label_name(label_id: str, default: str) -> str:
    with _lock:
        return label_name_mapping.get(label_id, default)


def cut_in_entry(product_id: str):
    with _lock:
        return cut_in_script_dict.get(product_id)


def stat() -> Dict[str, Any]:
    with _lock:
        return {
            'version': _version,
            'product_count': len(product_selling_point_match_dict),
            'cut_in_count': len(cut_in_script_dict),
            'label_count': len(label_name_mapping),
        }


def list_dim_tables() -> List[Dict[str, str]]:
    out = []
    for name, script in DIM_TABLES.items():
        path = MAIN_PATH / 'excel2consts' / name
        out.append({
            'name': name,
            'script': script,
            'path': str(path),
            'exists': path.exists(),
            'mtime': _mtime_str(path),
        })
    return out


def _mtime_str(path: Path) -> str:
    if not path.exists():
        return ''
    import datetime
    return datetime.datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec='seconds')
