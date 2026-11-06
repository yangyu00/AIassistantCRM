# -*- coding: utf-8 -*-
"""
业务解析器：两个文件真正的业务差异都在这里。

F09609 —— 宽表展开：
  前 3 列 = 日期 / user_id / product_no，第 4 列起两两一组 = (product_id, strategy_script)，
  一条物理记录展开成最多 50 条；按 product_no 分表；主键 (product_no, product_id) 去重。
  策略内容里的裸换行：缓冲整条逻辑记录的所有物理行，用 "\n" 还原后整体按 tab 切分。

F09591 —— 一行一条：
  5 列 = 日期 / user_id / product_no / city_id / strategy_name，
  少于 4 列视为严重错误行；第 5 列吸收剩余全部内容（含制表符），续行拼回第 5 列。
"""
from typing import List, Optional

from .parser_base import AvlParser


class _WidePairMixin:
    """宽表展开共用逻辑：前 N 列为记录头，之后两两一组 (product_id, script) 展开成多条。
    续行按物理行原样保留，收尾时用 "\n" 还原；sftp_jobs.WidePairParser 复用同一套逻辑。"""

    def begin_record(self, line: str, batches=None):
        # 宽表用「物理行列表」当缓冲，收尾时再拼成一条逻辑记录
        return [line]

    def append_continuation(self, buf: list, line: str, batches=None):
        buf.append(line)

    def _expand(self, buf: list, batches):
        logical = "\n".join(buf)
        parts = logical.split('\t')
        header = int(self.cfg.get('wide_header_cols', self.cfg.get('HEADER_COLUMNS', 3)))
        if len(parts) < header:
            self.warn_bad(f"列数不足，跳过: {logical[:50]}...")
            return
        rest = parts[header:]
        if len(rest) % 2 != 0:
            # 成对校验：奇数个说明某处缺列/多列，展开会错位，记一条告警便于排查
            self.warn_bad(f"宽表列数不成对（{len(rest)} 个），末尾组被丢弃: {logical[:50]}...")
        id_col, script_col = self.cfg.get('wide_pair_cols', self.cfg.get('PAIR_COLUMNS', ('product_id', 'strategy_script')))
        idx_id = batches.column_index(id_col)
        idx_script = batches.column_index(script_col)
        route_col = self.cfg.get('route_column', 'product_no')
        route_idx = batches.column_index(route_col) if route_col else None
        if route_idx is not None and route_idx >= header:
            # 路由列不在记录头里（配置有误）→ 退化为按 product_id 路由，避免 IndexError
            route_idx = None
        t_name = None
        count = 0
        for i in range(0, len(rest) - 1, 2):
            product_id = rest[i]
            if not product_id.strip():
                continue
            row = [None] * len(self.cfg['DB_SCHEMA'])
            if route_idx is not None:
                row[route_idx] = parts[route_idx]
            row[idx_id] = product_id
            row[idx_script] = rest[i + 1]
            t_name = batches.add(tuple(row), row[route_idx] if route_idx is not None else product_id)
            count += 1
        if count and t_name is not None:
            self.touch(t_name)


class F09609Parser(_WidePairMixin, AvlParser):
    """宽表 → 多条（F09609 专用，路由键 = product_no）"""

    def finish_record(self, buf: list, batches):
        self._expand(buf, batches)

    def finish_record(self, buf: list, batches):
        logical = "\n".join(buf)
        parts = logical.split('\t')
        if len(parts) < self.cfg['HEADER_COLUMNS']:
            self.warn_bad(f"列数不足，跳过: {logical[:50]}...")
            return
        op_time, user_id, product_no = parts[0], parts[1], parts[2]
        rest = parts[self.cfg['HEADER_COLUMNS']:]
        id_col, script_col = self.cfg['PAIR_COLUMNS']
        idx_id = batches.column_index(id_col)
        idx_script = batches.column_index(script_col)
        t_name = batches.table_names()[batches.table_index(product_no)]
        count = 0
        for i in range(0, len(rest) - 1, 2):
            product_id = rest[i]
            if not product_id.strip():
                continue
            row = [None] * len(self.cfg['DB_SCHEMA'])
            row[0], row[1], row[2] = op_time, user_id, product_no
            row[idx_id] = product_id
            row[idx_script] = rest[i + 1] if i + 1 < len(rest) else ""
            batches.add_to(t_name, tuple(row))
            count += 1
        if count:
            self.touch(t_name)


class F09591Parser(AvlParser):
    """一行一条，最后一列吸收剩余内容并支持续行"""

    #: row 中「吸收剩余内容」的那一列下标；续行拼接用，避免丢 Tab / 重复拼接
    TAIL_INDEX = 4

    def begin_record(self, line: str, batches=None):
        parts = line.split('\t')
        min_cols = int(self.cfg['MIN_COLUMNS'])
        if len(parts) < min_cols:
            self.warn_bad(f"字段不足 {min_cols} 列: {line[:50]}...")
            return None
        op_time, user_id, product_no, city_id = parts[0], parts[1], parts[2], parts[3]
        strategy_name = '\t'.join(parts[4:]) if len(parts) > 4 else ""
        row = [op_time, user_id, product_no, city_id, strategy_name]
        for col in self.cfg['REQUIRED_COLUMNS']:
            if not row[batches.column_index(col)]:
                self.warn_bad(f"关键字段 {col} 为空: {line[:50]}...")
                return None
        return row

    def append_continuation(self, buf: list, line: str, batches=None):
        """续行原样拼回尾列（还原换行符）。
        尾列内部用 Tab 拼出来的原结构要保留，所以这里必须按「原始行文本」拼接，
        不能依赖基类按列拼（基类会把 Tab 压掉）。"""
        idx = self.TAIL_INDEX
        buf[idx] = (buf[idx] or '') + '\n' + line

    def finish_record(self, buf: list, batches):
        t_name = batches.add(tuple(buf), buf[batches.column_index('product_no')])
        self.touch(t_name)


PARSERS = {
    'F09609': F09609Parser,
    'F09591': F09591Parser,
}


def get_parser(cfg: dict, table_name_list: List[str]) -> AvlParser:
    prefix = cfg['FILE_PREFIXES']
    cls = PARSERS.get(prefix)
    if cls is None:
        raise ValueError(f"未注册的业务解析器: {prefix}")
    return cls(cfg, table_name_list)
