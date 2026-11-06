# -*- coding: utf-8 -*-
"""查看某张表里 op_time 最近的 10 条记录（只读）。

用法:
    python scripts/show_top_10.py <db_path> [table_name] [limit]

表名只允许 [A-Za-z0-9_]，与 sftp_common.db.TABLE_NAME_RE 口径一致，避免 SQL 注入。
"""
import os
import re
import sqlite3
import sys

TABLE_NAME_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def show_top_10(db_path: str, table: str = 'data_F09554_0', limit: int = 10) -> list:
    if not TABLE_NAME_RE.match(table):
        raise ValueError(f"非法表名: {table}")
    limit = max(1, min(int(limit), 1000))
    if not os.path.exists(db_path):
        raise FileNotFoundError(db_path)
    conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    try:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        if not cols:
            print(f"表 {table} 不存在")
            return []
        rows = conn.execute(
            f"SELECT * FROM {table} ORDER BY op_time DESC LIMIT {limit}").fetchall()
        for r in rows:
            print(dict(zip(cols, r)))
        return rows
    finally:
        conn.close()


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    db = sys.argv[1]
    tbl = sys.argv[2] if len(sys.argv) > 2 else 'data_F09554_0'
    lim = int(sys.argv[3]) if len(sys.argv) > 3 else 10
    show_top_10(db, tbl, lim)
