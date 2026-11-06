# -*- coding: utf-8 -*-
"""
共用 AVL 流式读取器。

两个业务的文件都有「裸换行」问题：一条逻辑记录的最后一列里可能夹着真实换行符，
所以不能按行直接当一条记录。这里按字节块读取，自己按 \\n 切行：
  - 行首匹配日期正则 → 新逻辑记录的开始
  - 否则 → 上一条记录的续行
  - 无论文件是 \\n 还是 \\r\\n，行尾的 \\r 都会被剥掉
  - 行尾的反斜杠续行符（\\）统一去掉，空行跳过
"""
import re
from typing import Iterator, Optional

DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}')


class AvlLineReader:
    """按字节块流式切行的迭代器；不依赖文本模式的通用换行，裸换行逻辑完全可控"""

    def __init__(self, path: str, chunk_size: int = 1024 * 1024,
                 date_re: Optional[re.Pattern] = None):
        self._fh = open(path, 'rb')
        self._chunk_size = chunk_size
        self._date_re = date_re or DATE_RE
        self._buf = b''
        self._eof = False

    def close(self):
        try:
            self._fh.close()
        except Exception:
            pass

    def __enter__(self) -> 'AvlLineReader':
        return self

    def __exit__(self, *exc):
        self.close()

    def __iter__(self) -> Iterator[str]:
        try:
            while True:
                if not self._eof:
                    chunk = self._fh.read(self._chunk_size)
                    if chunk:
                        self._buf += chunk
                    else:
                        self._eof = True
                if not self._buf:
                    break
                idx = self._buf.find(b'\n')
                if idx < 0:
                    if not self._eof:
                        continue  # 半行，等下一块
                    raw, self._buf = self._buf, b''
                else:
                    raw = self._buf[:idx]
                    self._buf = self._buf[idx + 1:]
                yield _clean_line(raw)
        finally:
            self.close()

    def is_new_record(self, line: str) -> bool:
        return bool(self._date_re.match(line))


def _clean_line(raw: bytes) -> str:
    line = raw.decode('utf-8', errors='ignore').rstrip('\r')
    return line.rstrip('\\')
