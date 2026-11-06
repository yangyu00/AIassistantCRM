# -*- coding: utf-8 -*-
"""
共用 SQLite 层：连接、建表、锁重试、分批落盘、过期清理。

两版脚本这部分逻辑基本一致，差异全部由配置驱动：
  - 表结构 / 主键 / 索引          → DB_SCHEMA / PRIMARY_KEY / INDEX_COLUMNS
  - 插入去重                      → INSERT_OR_REPLACE
  - 过期列与保留天数              → EXPIRE_TIME_COLUMN / DATA_EXPIRE_DAYS
"""
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta
from contextlib import contextmanager
from typing import Iterator, List, Optional, Sequence

from .sftp_log import log

TABLE_NAME_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


# ================= 表名 =================
def build_table_name_list(cfg: dict) -> List[str]:
    """默认分表名清单；map 模式由 sftp_jobs.table_router 再补老单表。
    按 前缀_序号 生成并做安全校验，避免 f-string 拼 SQL 引入注入面"""
    names = []
    for i in range(int(cfg['NUM_TABLES'])):
        name = f"{cfg['TABLE_NAME_PREFIX']}_{i}"
        if not TABLE_NAME_RE.match(name):
            raise ValueError(f"非法表名: {name}")
        names.append(name)
    try:
        from sftp_jobs.table_router import table_names as _extend
    except ImportError:
        return names
    return _extend(cfg, lambda c: names)



def table_index_of(key: str, num_tables: int) -> int:
    """分表路由规则（两版一致）：按 product_no 的 crc32 取模"""
    import zlib
    return zlib.crc32((key or "NULL").encode('utf-8')) % num_tables


# ================= 连接 =================
@contextmanager
def connect_db(db_path: str, cfg: dict, timeout: Optional[float] = None) -> Iterator[sqlite3.Connection]:
    """
    统一连接入口：
      - timeout + busy_timeout 双保险，遇锁先等待而不是立刻抛错
      - isolation_level=None 自动提交，语句结束不长时间占锁
      - WAL 让读不阻塞写；退出 with 一定 close，避免连接泄漏留下长事务
    """
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    if timeout is not None:
        wait_ms = int(timeout * 1000)
    else:
        wait_ms = int(cfg['DB_BUSY_TIMEOUT_MS'])
    conn = sqlite3.connect(db_path, timeout=max(wait_ms, 1) / 1000.0, isolation_level=None)
    try:
        _apply_pragmas(conn, cfg, wait_ms)
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _apply_pragmas(conn, cfg: dict, wait_ms: int):
    conn.execute('PRAGMA busy_timeout = %d' % int(wait_ms))
    conn.execute('PRAGMA journal_mode = WAL')
    conn.execute('PRAGMA synchronous = NORMAL')
    conn.execute('PRAGMA foreign_keys = ON')


open_db = connect_db  # 兼容旧调用名


def checkpoint_wal(conn):
    """收尾做一次 PASSIVE 检查点，把 WAL 合并回主库（不抢独占锁，失败也无妨）"""
    try:
        conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    except Exception:
        pass


# ================= 锁重试 =================
def is_lock_error(exc: BaseException) -> bool:
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    msg = str(exc).lower()
    return any(k in msg for k in ('locked', 'busy', 'lock'))


def retry_on_lock(func, *args, **kwargs):
    """遇到 database is locked 时退避重试；每次重试前先 rollback 释放旧锁"""
    retries = max(int(kwargs.pop('_retries', None) or 5), 1)
    base = float(kwargs.pop('_backoff_base', None) or 0.2)
    last_exc = None
    for attempt in range(retries):
        try:
            return func(*args, **kwargs)
        except sqlite3.OperationalError as exc:
            last_exc = exc
            if not is_lock_error(exc):
                raise
            conn = kwargs.get('conn')
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
            if attempt == retries - 1:
                break
            sleep_for = base * (2 ** attempt)
            log.debug(f"数据库锁定，{sleep_for:.1f}s 后第 {attempt + 1}/{retries - 1} 次重试 "
                      f"({type(exc).__name__}: {exc})")
            time.sleep(sleep_for)
    log.error(f"数据库锁定，重试 {retries} 次后仍失败: {last_exc}", exc=last_exc)
    raise last_exc


# ================= 建表 =================
def init_schema(conn, cfg: dict, table_name_list: Sequence[str]):
    """预建表 + 索引。统一 IF NOT EXISTS，保证增量可重复运行"""
    columns_def = ", ".join(f"{col} {dtype}" for col, dtype in cfg['DB_SCHEMA'])
    pk = cfg.get('PRIMARY_KEY')
    pk_sql = f", PRIMARY KEY ({', '.join(pk)})" if pk else ""
    cursor = conn.cursor()
    for table_name in table_name_list:
        cursor.execute(f"CREATE TABLE IF NOT EXISTS {table_name} ({columns_def}{pk_sql})")
        for idx_cols in cfg.get('INDEX_COLUMNS', ()):
            cols = tuple(idx_cols) if isinstance(idx_cols, (list, tuple)) else (idx_cols,)
            cursor.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{table_name}_{'_'.join(cols)} "
                f"ON {table_name} ({', '.join(cols)})"
            )
    conn.commit()


# ================= 分批落盘 =================
class TableBatcher:
    """
    按分表维护批次缓冲，满了就写库。
      - BEGIN IMMEDIATE 抢写锁 → executemany → commit，成功后才清空 batch，
        任何一步失败都保留 batch 内容，交给上层重试 / 抢救，数据不丢
      - 只检查"本条刚动过"的那张表是否满批，不再每条记录遍历 128 张表
    """

    def __init__(self, cfg: dict, table_name_list: Sequence[str], conn):
        self._cfg = cfg
        self._conn = conn
        self._cursor = conn.cursor()
        self._batches: dict = {t: [] for t in table_name_list}
        self._table_names = list(self._batches.keys())
        self._columns_sql = ", ".join(col for col, _ in cfg['DB_SCHEMA'])
        self._placeholders = ", ".join("?" * len(cfg['DB_SCHEMA']))
        self._batch_size = int(cfg['BATCH_SIZE'])
        if cfg.get('INSERT_OR_REPLACE'):
            self._verb = "INSERT OR REPLACE"
        else:
            self._verb = "INSERT"

    # --- 分发 ---
    def add(self, row: tuple, key: str):
        t_name = self._table_names[table_index_of(key, int(self._cfg['NUM_TABLES']))]
        self._batches[t_name].append(row)
        return t_name

    def add_to(self, t_name: str, row: tuple):
        self._batches[t_name].append(row)

    def table_names(self) -> List[str]:
        return self._table_names

    def batch_of(self, t_name: str) -> list:
        return self._batches[t_name]

    def table_index(self, key: str) -> int:
        return table_index_of(key, int(self._cfg['NUM_TABLES']))

    def column_index(self, name: str) -> int:
        if not hasattr(self, '_col_idx'):
            self._col_idx = {c: i for i, (c, _) in enumerate(self._cfg['DB_SCHEMA'])}
        return self._col_idx[name]

    def pending(self) -> int:
        return sum(len(b) for b in self._batches.values())

    # --- 落盘 ---
    def _do_flush(self, t_name: str, batch: list, conn=None) -> int:
        if not batch:
            return 0
        self._cursor.execute("BEGIN IMMEDIATE")
        self._cursor.executemany(
            f"{self._verb} INTO {t_name} ({self._columns_sql}) VALUES ({self._placeholders})",
            batch,
        )
        self._conn.commit()
        written = len(batch)
        batch.clear()  # 只有走到这里，才说明真的落盘成功
        return written

    def flush(self, t_name: str, label: str = "已写入") -> int:
        batch = self._batches[t_name]
        if not batch:
            return 0
        expected = len(batch)
        try:
            written = retry_on_lock(self._do_flush, t_name, batch, conn=self._conn, _retries=int(self._cfg['DB_RETRY_TIMES']), _backoff_base=float(self._cfg['DB_RETRY_BACKOFF_BASE']))
        except Exception:
            # 自动提交模式下 _do_flush 手动 BEGIN IMMEDIATE，中途失败必须回滚，
            # 否则连接里残留打开的事务，下一批会报
            # "cannot start a transaction within a transaction"
            try:
                self._conn.rollback()
            except Exception:
                pass
            raise
        return written if written == expected else expected - len(batch)

    def flush_if_full(self, t_name: str) -> int:
        if len(self._batches[t_name]) >= self._batch_size:
            return self.flush(t_name, "已写入")
        return 0

    def flush_all(self, label: str = "最终提交") -> int:
        """提交所有剩余批次；某张表失败也继续处理其余表，失败数据留在内存里等抢救"""
        written_total = 0
        touched = [t for t in self._table_names if self._batches[t]]
        for t_name in self._table_names:
            if not self._batches[t_name]:
                continue
            try:
                written_total += self.flush(t_name, label)
            except Exception as e:
                log.error(f"表 {t_name} {label}失败，数据仍保留在内存中"
                          f"（{len(self._batches[t_name])} 条）: {e}", exc=e)
        if touched:
            log.info(f"最终提交完成：{len(touched)} 张表共 {written_total} 条")
        return written_total


# ================= 过期清理 =================
def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (table_name,)
    ).fetchone()
    return row is not None


def _delete_expired_of_table(table_name: str, expire_value: str, conn, time_column: str) -> int:
    """单表过期删除：一个短写事务内完成并立即提交，持锁时间最短"""
    cursor = conn.cursor()
    cursor.execute("BEGIN IMMEDIATE")
    cursor.execute(f"DELETE FROM {table_name} WHERE {time_column} < ?", (expire_value,))
    conn.commit()
    return cursor.rowcount if cursor.rowcount > 0 else 0


def cleanup_expired_data(cfg: dict, table_name_list: Sequence[str]):
    """
    删除过期数据：逐表短事务 + 锁重试；单表失败不影响其余表。
    不自动 VACUUM（需要独占锁，极易失败），改为空闲时手动维护。
    """
    db_path = cfg['DB_PATH']
    if not os.path.exists(db_path):
        return
    time_column = cfg['EXPIRE_TIME_COLUMN']
    # [FIX] 原来直接拿日期字符串和 op_time 比较：op_time 只写了日期（没有时分秒）
    # 的当天记录会满足 '当天' < '当天' 之外的边界而被误删。改成「严格早于
    # (今天-N天) 这一天」才删，即阈值取次日 00:00:00，当天数据一律保留。
    expire_day = (datetime.now() - timedelta(days=int(cfg['DATA_EXPIRE_DAYS']))).strftime('%Y-%m-%d')
    expire_value = expire_day + ' 00:00:00'
    total_deleted = 0
    failed_tables = 0
    try:
        with connect_db(db_path, cfg) as conn:
            for table_name in table_name_list:
                if not _table_exists(conn, table_name):
                    continue
                try:
                    deleted = retry_on_lock(_delete_expired_of_table, table_name, expire_value,
                                            conn=conn, time_column=time_column,
                                            _retries=int(cfg['DB_RETRY_TIMES']),
                                            _backoff_base=float(cfg['DB_RETRY_BACKOFF_BASE']))
                    total_deleted += deleted
                    if deleted > 0:
                        log.debug(f"表 {table_name} 已清理 {deleted} 条"
                                  f"（{time_column} < {expire_value}）")
                except Exception as e:
                    failed_tables += 1
                    log.error(f"表 {table_name} 清理过期数据失败: {e}", exc=e)
    except Exception as e:
        log.error(f"清理过期数据失败: {e}", exc=e)
        return
    log.info(f"数据库过期数据清理完成：共删除 {total_deleted} 条"
             f"（{time_column} < {expire_value}，保留 {expire_day} 及以后），失败表 {failed_tables} 个")


def cleanup_local_expired_files(cfg: dict):
    """删除本地目录中超过指定天数的 .AVL 文件（只汇总记一条 info，个别失败记 error）"""
    local_dir = cfg['LOCAL_DIR']
    if not os.path.exists(local_dir):
        return
    days = int(cfg['LOCAL_FILE_EXPIRE_DAYS'])
    expire_time = datetime.now() - timedelta(days=days)
    deleted_count = 0
    failed = 0
    for filename in os.listdir(local_dir):
        if not filename.upper().endswith('.AVL'):
            continue
        file_path = os.path.join(local_dir, filename)
        try:
            if datetime.fromtimestamp(os.path.getmtime(file_path)) < expire_time:
                os.remove(file_path)
                deleted_count += 1
        except OSError as e:
            failed += 1
            log.error(f"删除本地过期文件失败 {filename}: {e}")
    log.info(f"本地文件清理完成：删除 {deleted_count} 个超过 {days} 天的 .AVL 文件，失败 {failed} 个")
