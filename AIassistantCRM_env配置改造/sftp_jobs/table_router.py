# -*- coding: utf-8 -*-
"""
分表路由扩展点：
  crc32 —— 按 route_column 的 crc32 取模（sftp_common 默认行为）
  map   —— 在 crc32 分表之外，把 product_table_map 里的老单表
           data_F09554/55/56 也登记进表名清单

背景：F09554 的 schema 里没有 product_id 列（列是 op_time/call_time/
product_no/.../city_id），无法按产品 ID 逐行路由。原设计就是"写入仍按
product_no 的 crc32 落分表，同时把老单表加进清单"，这样：
  - 老库里已存在的 data_F09554/55/56 不会被漏掉；
  - 新数据落到 crc32 分表；
queries.get_latest_record 在 map 模式下只读老单表，因此本项目的
实际生效前提是：历史库文件里数据就在 data_F09554/55/56 中。
若线上库实际是 crc32 分表，需要把 queries 的 map 分支改成查分表
（可加一个开关），上线前请先按下面命令核对：

    sqlite3 <runtime>/F09554/sqlite_db/data_F09554.db \\
      "select name from sqlite_master where type='table' limit 20;"
"""
from sftp_common.db import table_index_of


def table_names(cfg, build_names):
    """map 模式的表名清单 = 默认 128 张分表 + 映射表里的老单表。"""
    names = build_names(cfg)
    if cfg.get('router') != 'map':
        return names
    for t in cfg['product_table_map'].values():
        if t not in names:
            names.append(t)
    return names


def resolve_table(cfg, batches, key, row):
    """写入仍按 product_no 的 crc32 落分表（与 sftp_common 写入层一致）。"""
    n = int(cfg.get('NUM_TABLES', cfg.get('num_tables', 128)))
    return batches.table_names()[table_index_of(key, n)]


def ensure_legacy_tables(conn, cfg):
    """map 模式补建老单表，须在 init_schema 之后、入库之前调用。"""
    if cfg.get('router') != 'map':
        return
    from sftp_common.db import init_schema
    init_schema(conn, cfg, list(dict.fromkeys(cfg['product_table_map'].values())))
