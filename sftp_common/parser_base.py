# -*- coding: utf-8 -*-
"""
共用解析骨架：把「读文件 → 识别逻辑记录 → 分批落盘 → 收尾/抢救」的流程抽出来。

业务只实现两件事：
  1. begin_record(line)      —— 遇到一条新记录的第一行时，建立记录缓冲；返回 None 表示跳过
  2. append_continuation(buf, line) / finish_record(buf) —— 续行怎么拼、收尾怎么展开入库

底层差异（哪个字段做分表键、宽表展开还是一行一条、INSERT 还是 INSERT OR REPLACE）
全部由 TableBatcher + 配置消化，子类不用关心 SQL。
"""
import os
import time
from abc import ABC, abstractmethod
from typing import List, Optional

from .sftp_log import log
from .db import TableBatcher, checkpoint_wal, init_schema, open_db
from .reader import AvlLineReader


class AvlParser(ABC):
    def __init__(self, cfg: dict, table_name_list: List[str]):
        self.cfg = cfg
        self.table_name_list = table_name_list
        self.bad_count = 0
        self.parsed_count = 0
        self.written_count = 0
        self.rescued_count = 0

    # ---------- 业务钩子 ----------
    @abstractmethod
    def begin_record(self, line: str) -> Optional[list]:
        """新记录首行 → 返回记录缓冲（list），返回 None 表示这条丢弃"""

    def append_continuation(self, buf: list, line: str, batches=None):
        """续行处理，默认拼到配置的那一列并还原换行符。
        注意：拼接的是「原始物理行文本」（制表符原样保留），
        这样 F09591 尾列里原本用 Tab 分隔的多段内容不会被压成一个字段。"""
        col = self.cfg.get('CONTINUATION_COLUMN')
        if col is not None and batches is not None:
            idx = batches.column_index(col)
            buf[idx] = (buf[idx] or '') + '\n' + line

    @abstractmethod
    def finish_record(self, buf: list, batches: TableBatcher):
        """一条逻辑记录收尾：展开成 N 条入库（可多次调用 batches.add / add_to）"""

    # ---------- 通用工具 ----------
    def warn_bad(self, msg: str):
        self.bad_count += 1
        if self.bad_count <= int(self.cfg['CORRUPT_ROW_LOG_LIMIT']):
            log.debug(f"跳过异常行（第 {self.bad_count} 条）: {msg}")

    # ---------- 主流程 ----------
    def parse_file(self, avl_path: str, db_path: str) -> dict:
        reader = None
        conn = None
        batches = None
        started_at = time.time()
        fname = os.path.basename(avl_path)
        try:
            os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
            conn = open_db(db_path, self.cfg)
            init_schema(conn, self.cfg, self.table_name_list)
            conn.commit()

            batches = TableBatcher(self.cfg, self.table_name_list, conn)
            # 业务层可选挂钩（如 product_id 自动补列）；原来只在 cfg 里放了
            # _wrap_batcher，从来没人调用，导致 product_id 列一直是空
            wrapper = self.cfg.get('_wrap_batcher')
            if wrapper is not None:
                batches = wrapper(batches)
            log.info(f"开始解析文件: {fname}")
            reader = AvlLineReader(avl_path)

            current = None
            for line in reader:
                if not line:
                    continue
                if reader.is_new_record(line):
                    if current is not None:
                        # 先刷盘再开新记录：万一落盘抛错，数据还留在 current 里可抢救
                        self._finish_and_flush(current, batches)
                        current = None
                    current = self.begin_record(line, batches)
                elif current is not None:
                    self.append_continuation(current, line, batches)
                # 文件开头就是续行时无主可依，忽略

            if current is not None:
                self._finish_and_flush(current, batches)
                current = None

            self.written_count = batches.flush_all("最终提交")
            checkpoint_wal(conn)

            log.info(f"文件 {fname} 解析完成：正常处理 {self.parsed_count} 条，"
                     f"落盘 {self.written_count} 条，跳过异常行 {self.bad_count} 条，"
                     f"耗时 {time.time() - started_at:.1f}s")
            return self._stat()

        except Exception as e:
            log.error(f"解析/入库失败: {fname}: {e}", exc=e)
            if conn is not None:
                try:
                    conn.rollback()  # 先释放锁，再抢救内存里的数据
                except Exception:
                    pass
                if batches is not None:
                    try:
                        pending = batches.pending()
                        if pending:
                            log.info(f"尝试抢救未落盘的 {pending} 条数据...")
                            rescued = batches.flush_all("抢救落盘")
                            self.rescued_count = rescued
                            log.info(f"抢救落盘完成，成功写入 {rescued} 条")
                    except Exception as e2:
                        log.error(f"抢救落盘也失败，未写入 {batches.pending()} 条: {e2}", exc=e2)
            return self._stat(failed=True)
        finally:
            if reader is not None:
                reader.close()
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def _finish_and_flush(self, buf, batches):
        self.parsed_count += 1
        self._touched_table = None
        self.finish_record(buf, batches)
        # 只检查本条动过的表是否满批，避免每条记录遍历 128 张表；
        # 一条记录可能落进多张表（F09609 宽表），全部检查一遍
        t_names = self._touched_by.keys() if self._touched_by else ()
        for t_name in t_names:
            batches.flush_if_full(t_name)

    def touch(self, t_name: str):
        """记录本条记录落进了哪些表（供满批刷盘使用）"""
        if not hasattr(self, '_touched_by'):
            self._touched_by = {}
        self._touched_by[t_name] = None

    def _stat(self, failed: bool = False) -> dict:
        return {'parsed': self.parsed_count, 'written': self.written_count,
                'bad': self.bad_count, 'rescued': self.rescued_count, 'failed': failed}
