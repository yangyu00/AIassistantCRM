# -*- coding: utf-8 -*-
from sftp_common.parser_base import AvlParser


class LegacyLineParser(AvlParser):
    def begin_record(self, line: str, batches=None):
        schema = self.cfg['DB_SCHEMA']
        n = len(schema)
        min_cols = int(self.cfg.get('MIN_COLUMNS', n))
        parts = line.split('\t')
        if len(parts) < min_cols:
            self.warn_bad(f"字段不足 {min_cols} 列: {line[:50]}...")
            return None
        if len(parts) < n:
            parts = parts + [''] * (n - len(parts))
        elif len(parts) > n:
            parts = parts[:n]
        parts = list(parts)
        default = self.cfg.get('int_default', 0)
        for name in self.cfg.get('int_columns') or ():
            idx = batches.column_index(name)
            val = str(parts[idx]).strip()
            try:
                parts[idx] = int(val) if val else default
            except ValueError:
                parts[idx] = default
        for col in self.cfg.get('required_columns') or ():
            if not parts[batches.column_index(col)]:
                self.warn_bad(f"关键字段 {col} 为空: {line[:50]}...")
                return None
        return tuple(parts)

    def finish_record(self, buf, batches):
        batches.add(buf, buf[batches.column_index(self.cfg['route_column'])])
