# -*- coding: utf-8 -*-
"""
宽表解析器：与 sftp_common.parsers.F09609Parser 逻辑一致（前 N 列为记录头，
之后两两一组展开成多条；裸换行用 "\\n" 还原）。

单独放一份是因为 sftp_common 的 PARSERS 字典按 FILE_PREFIXES 硬绑定类名，
一个前缀只能对应一个解析器；放进 sftp_jobs 后，业务解析器由 service.py 按
parser 类型注入，新增宽表业务不需要再改 sftp_common。
"""
from sftp_common.parser_base import AvlParser
from sftp_common.parsers import _WidePairMixin


class WidePairParser(_WidePairMixin, AvlParser):
    """通用宽表解析器：逻辑与 F09609Parser 完全一致，供未来新增的宽表业务复用。"""

    def finish_record(self, buf: list, batches):
        self._expand(buf, batches)
