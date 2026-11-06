# -*- coding: utf-8 -*-
import os
import sqlite3
import threading
import zlib

from .config import JOBS, job_of_product
from .runtime_paths import db_path, db_file_of
from sftp_common.sftp_log import log

_local = threading.local()
_col_cache: dict = {}


def _mask(v) -> str:
    """[FIX] 日志脱敏：手机号只保留后 4 位，避免手机号随异常信息落进日志"""
    s = str(v or '')
    return s[-4:].rjust(len(s), '*') if len(s) >= 7 else '***'


def _columns_of(conn, t_name: str, cache_key: str = ''):
    key = (cache_key, t_name)
    cols = _col_cache.get(key)
    if cols is None:
        rows = conn.execute(f"PRAGMA table_info({t_name})").fetchall()
        cols = [r[1] for r in rows]
        _col_cache[key] = cols
    return cols


def _conn(job_id: str, path: str, timeout_ms: int) -> sqlite3.Connection:
    """每线程每 job 复用一条只读连接"""
    key = f'{job_id}:{path}'
    c = getattr(_local, 'conns', None)
    if c is None:
        c = _local.conns = {}
    conn = c.get(key)
    if conn is None:
        conn = sqlite3.connect(path, timeout=max(timeout_ms, 1) / 1000.0, isolation_level=None)
        conn.execute(f"PRAGMA busy_timeout = {int(timeout_ms)}")
        conn.execute("PRAGMA query_only = ON")
        c[key] = conn
    return conn


def _resolve(job_id: str, telnum, product_id=None):
    cfg = JOBS.get(job_id)
    if cfg is None:
        raise KeyError(f"未注册的业务: {job_id}")
    path = db_path(job_id, db_file_of(cfg))
    num = int(cfg.get('num_tables', 128))
    idx = zlib.crc32(str(telnum if telnum else 'NULL').encode('utf-8')) % num
    return cfg, path, f"{cfg['table_prefix']}_{idx}"


async def get_latest_record(job_id: str, telnum, product_id=None) -> dict:
    cfg, path, t_name = _resolve(job_id, telnum, product_id)
    timeout_ms = int(cfg.get('DB_BUSY_TIMEOUT_MS', 30000))
    col = cfg.get('product_id_column')
    try:
        conn = _conn(job_id, path, timeout_ms)
        cols = _columns_of(conn, t_name, f'{job_id}:{path}')
        if not cols:                                   # 表还没建（当天文件未到）
            return {}
        sql = f"SELECT * FROM {t_name} WHERE product_no = ?"
        args = [telnum]
        if col and col in cols and product_id is not None:
            sql += f" AND {col} = ?"
            args.append(product_id)
        sql += " ORDER BY op_time DESC LIMIT 1"
        row = conn.execute(sql, tuple(args)).fetchone()
        return dict(zip(cols, row)) if row else {}
    except Exception as e:
        # [FIX] 原来用 print，关掉控制台输出就完全看不见这条链路的报错；
        # 且异常文本可能带手机号，这里只记类型+脱敏后的号尾
        log.error(f"查询业务库失败: job={job_id}, productId={product_id}, "
                  f"tel=*******{_mask(telnum)[-4:]}, err={type(e).__name__}: {e}", exc=e)
        return {}


async def get_latest_record_by_product(product_id: str, telnum) -> dict:
    """按产品ID自动选库（main.py 用）"""
    job_id = job_of_product(product_id)
    if job_id is None:
        return {}
    return await get_latest_record(job_id, telnum, product_id)
