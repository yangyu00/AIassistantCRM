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


class F09609Parser(AvlParser):
    """宽表 → 多条（语义与第一版一致）"""

    def begin_record(self, line: str, batches=None):
        # 宽表用「物理行列表」当缓冲，收尾时再拼成一条逻辑记录
        return [line]

    def append_continuation(self, buf: list, line: str, batches=None):
        buf.append(line)

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

    def finish_record(self, buf: list, batches):
        t_name = batches.add(tuple(buf), buf[batches.column_index('product_no')])
        self.touch(t_name)


PARSERS = {
    'F09609': F09609Parser,
    'F09591': F09591Parser,
}


def get_parser(cfg: dict, table_name_list: List[str]) -> AvlParser:
    cls = PARSERS.get(cfg['FILE_PREFIXES'])
    if cls is None:
        raise ValueError(f"未注册的业务解析器: {cfg['FILE_PREFIXES']}")
    return cls(cfg, table_name_list)
