# -*- coding: utf-8 -*-
"""
把配置里的 product_id 在入库前补进每一行（schema 最后一列）。
只在解析入库链路里挂钩一次，不改 sftp_common，也不用猴子补丁全局替换方法：
runner / parser_base 都在自己的流程里建 TableBatcher，这里包装该类实例即可。
"""
import logging

log = logging.getLogger(__name__)


class ProductIdInjector:
    def __init__(self, cfg: dict, column: str, product_id: str):
        self.cfg = cfg
        self.column = column
        self.product_id = product_id
        self.idx = [c[0] for c in cfg['DB_SCHEMA']].index(column)
        self.filled = 0

    def fill(self, row: tuple) -> tuple:
        if row[self.idx]:
            return row
        vals = list(row)
        vals[self.idx] = self.product_id
        self.filled += 1
        return tuple(vals)

    def wrap(self, batches):
        """包装一个 TableBatcher 实例：add / add_to 入库前补 product_id"""
        orig_add = batches.add
        orig_add_to = batches.add_to
        fill = self.fill

        def add(row, key):
            return orig_add(fill(row), key)

        def add_to(t_name, row):
            return orig_add_to(t_name, fill(row))

        batches.add = add
        batches.add_to = add_to
        return batches
