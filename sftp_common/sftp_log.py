# -*- coding: utf-8 -*-
"""
统一的日志出口：整个项目只这一种调用方式，两个业务脚本都 `from sftp_common.sftp_log import log`。

调用约定（只有一个形式，带异常时统一用关键字 exc=）：
    log.info(msg) / log.warning(msg) / log.error(msg, exc=e)

底层优先用 sftp_workdir_base.sftp_logger（和原脚本一样写 logs/info.log、error.log，
并轮转），取不到就退回标准库 logging 写 stderr，保证离线调试时关键信息不丢。

各级别对应的输出：
    info    常规关键信息：开始/完成、连接、下载、入库条数、清理条数、退出码
    warning 需要关注但不中断：未取到当天文件、数据库锁重试
    error   异常：带错误类型和完整堆栈
    debug   细粒度进度（每表写入条数等），不进 info.log
"""
import logging
import os
import sys
import traceback

_BASE_LOG = None
_LOOKED_UP = False

# 共用模块内部的级别名 → 底层 logger 的方法名（按优先级依次找）
_LEVEL_CHAIN = {
    'info': ('info',),
    'warning': ('warning', 'noisy', 'info'),
    'error': ('error',),
    'debug': ('debug', 'noisy'),
}


def _backend():
    global _BASE_LOG, _LOOKED_UP
    if not _LOOKED_UP:
        _LOOKED_UP = True
        try:
            from sftp_common import sftp_logger as _b
            _BASE_LOG = _b
        except Exception:
            _BASE_LOG = None
    return _BASE_LOG


_STD = logging.getLogger("sftp_service")
if not _STD.handlers:
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    _STD.addHandler(_h)
_STD.setLevel(logging.INFO)


def _format(msg, exc):
    if exc is None:
        return msg
    if isinstance(exc, BaseException):
        return f"{msg} | {type(exc).__name__}: {exc}\n{traceback.format_exc()}"
    return f"{msg} | {exc}"


class _Logger:
    def setup(self, log_dir, echo_console=False, max_bytes=10 * 1024 * 1024,
              info_file="info.log", error_file="error.log"):
        backend = _backend()
        if backend is None:
            return False
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        backend.setup(log_dir, echo_console=echo_console, max_bytes=max_bytes,
                      info_file=info_file, error_file=error_file)
        return True

    def info(self, msg, exc=None):
        self._emit('info', msg, exc)

    def warning(self, msg, exc=None):
        self._emit('warning', msg, exc)

    def error(self, msg, exc=None):
        self._emit('error', msg, exc)

    def debug(self, msg, exc=None):
        self._emit('debug', msg, exc)

    # 兼容旧写法：原 f09609 用 log.noisy(...)
    def noisy(self, msg, exc=None):
        self._emit('warning', msg, exc)

    def _emit(self, level, msg, exc=None):
        backend = _backend()
        if backend is not None:
            for name in _LEVEL_CHAIN[level]:
                func = getattr(backend, name, None)
                if func is None:
                    continue
                try:
                    func(_format(msg, exc))
                    return
                except Exception:
                    break  # 底层 logger 有问题 → 退回标准库，不能因为日志把业务搞挂
        getattr(_STD, level if level != 'debug' else 'info')(_format(msg, exc))


log = _Logger()
