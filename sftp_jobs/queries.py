# -*- coding: utf-8 -*-
import os
import sqlite3
import zlib

from src.log_util import my_log
from .config import JOBS, job_config
from .runtime_paths import db_path


def _columns(cfg):
    return [c[0] for c in cfg['schema']]


async def get_latest_record(telnum, job_id: str, product_id=None):
    if job_id not in JOBS:
        raise KeyError(f"未注册的业务: {job_id}")
    cfg = job_config(job_id)

    db_file = cfg.get('db_file') or f"{cfg['table_prefix']}.db"
    path = db_path(job_id, db_file)
    if not os.path.exists(path):
        return {}

    if cfg['router'] == 'map':
        # 老 f09554 语义：product_id → 老单表 data_F09554/55/56
        # 老 f09554 入库时只按 product_no 分表，库里没有产品ID列，
        # 所以不管是哪个号卡/套餐/宽带产品，都查这三张老表，再取最新一条
        product_id = next(iter(cfg['product_table_map']), None)
        t_name = cfg['product_table_map'].get(product_id)
        if t_name is None:
            return {}
    else:
        key = telnum if telnum else 'NULL'
        idx = zlib.crc32(str(key).encode('utf-8')) % int(cfg.get('num_tables', 128))
        t_name = f"{cfg['table_prefix']}_{idx}"

    try:
        conn = sqlite3.connect(path, timeout=max(int(cfg.get('DB_BUSY_TIMEOUT_MS', 30000)) / 1000, 1))
        try:
            conn.execute('PRAGMA busy_timeout = %d' % int(cfg.get('DB_BUSY_TIMEOUT_MS', 30000)))
            cur = conn.cursor()
            cur.execute(
                f"SELECT * FROM {t_name} WHERE product_no = ? ORDER BY op_time DESC LIMIT 1",
                (telnum,))
            row = cur.fetchone()
        finally:
            conn.close()
        if row:
            return dict(zip(_columns(cfg), row))
        return {}
    except Exception as e:
        my_log.error(f"查询 {job_id} 失败: {e}")
        return {}
