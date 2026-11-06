# -*- coding: utf-8 -*-
"""
宽表解析器：与 sftp_common.parsers.F09609Parser 逻辑一致（前 N 列为记录头，
之后两两一组展开成多条；裸换行用 "\\n" 还原）。

单独放一份是因为 sftp_common 的 PARSERS 字典按 FILE_PREFIXES 硬绑定类名，
一个前缀只能对应一个解析器；放进 sftp_jobs 后，业务解析器由 service.py 按
parser 类型注入，新增宽表业务不需要再改 sftp_common。
"""
from sftp_common.parser_base import AvlParser


class WidePairParser(AvlParser):
    def begin_record(self, line: str, batches=None):
        return [line]

    def append_continuation(self, buf: list, line: str, batches=None):
        buf.append(line)

    def finish_record(self, buf: list, batches):
        logical = '\n'.join(buf)
        parts = logical.split('\t')
        header = int(self.cfg.get('wide_header_cols', 3))
        if len(parts) < header:
            self.warn_bad(f"列数不足，跳过: {logical[:50]}...")
            return
        rest = parts[header:]
        id_col, script_col = self.cfg.get('wide_pair_cols', ('product_id', 'strategy_script'))
        idx_id = batches.column_index(id_col)
        idx_script = batches.column_index(script_col)
        route_idx = batches.column_index(self.cfg.get('route_column', 'product_no'))
        t_name = None
        count = 0
        for i in range(0, len(rest) - 1, 2):
            if not rest[i].strip():
                continue
            row = [None] * len(self.cfg['DB_SCHEMA'])
            row[route_idx] = parts[2] if header > 2 else ''
            row[idx_id] = rest[i]
            row[idx_script] = rest[i + 1]
            t_name = batches.add(tuple(row), row[route_idx])
            count += 1
        if count:
            self.touch(t_name)
