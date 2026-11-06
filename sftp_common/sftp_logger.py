# -*- coding: utf-8 -*-
"""
两个 SFTP 入库脚本共用的分级日志（只依赖标准库，把它和脚本放在同一目录即可）：
  info.log  —— 关键节点：启动、连接、下载、入库条数、清理汇总、结束、退出
  error.log —— 错误说明 + 完整堆栈（含 main 兜底捕获的未处理异常）
逐表写入、批次、锁重试等刷屏信息默认只走终端，不落盘。
线程安全；日志自身出任何异常都不会影响业务；单文件超过 max_bytes 轮转一次（保留 .1）。
"""
import os
import sys
import threading
import traceback
from datetime import datetime

_LOCK = threading.RLock()
_DIR = None
_ECHO = True
_MAX_BYTES = 10 * 1024 * 1024
_INFO = 'info.log'
_ERR = 'error.log'
_STARTED = False


def setup(log_dir, echo_console=True, max_bytes=10 * 1024 * 1024, info_file='info.log', error_file='error.log'):
    """主程序启动时调用一次；未调用时按脚本同级 logs/ 惰性初始化"""
    global _DIR, _ECHO, _MAX_BYTES, _INFO, _ERR, _STARTED
    with _LOCK:
        _DIR = log_dir or None
        _ECHO = bool(echo_console)
        _MAX_BYTES = max(1024 * 1024, int(max_bytes))
        _INFO, _ERR = info_file, error_file
        _STARTED = True
        if _DIR:
            try:
                os.makedirs(_DIR, exist_ok=True)
            except Exception:
                _DIR = None


def _ensure():
    if _STARTED:
        return
    try:
        base = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
    except Exception:
        base = os.getcwd()
    setup(os.path.join(base, 'logs'))


def _target(name):
    if not _DIR:
        return None
    return os.path.join(_DIR, _INFO if name == 'info' else _ERR)


def _rotate(path):
    try:
        if os.path.exists(path) and os.path.getsize(path) >= _MAX_BYTES:
            bak = path + '.1'
            if os.path.exists(bak):
                os.remove(bak)
            os.replace(path, bak)
    except Exception:
        pass


def _append(name, text):
    path = _target(name)
    if not path:
        return
    try:
        with _LOCK:
            _rotate(path)
            with open(path, 'a', encoding='utf-8') as f:
                f.write(text)
    except Exception:
        pass


def _line(level, msg):
    return '%s [%s] %s\n' % (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), level, msg)


def _print(text):
    if not _ECHO:
        return
    try:
        print(text, flush=True)
    except Exception:
        pass


def info(msg):
    """关键节点 → info.log"""
    _ensure()
    text = _line('INFO', msg)
    _append('info', text)
    _print(text.rstrip('\n'))


def warning(msg):
    """需要留意但不中断流程的问题 → info.log"""
    _ensure()
    text = _line('WARNING', msg)
    _append('info', text)
    _print(text.rstrip('\n'))


def error(msg, exc=None):
    """错误 → error.log（含完整堆栈），并在 info.log 留一行指针"""
    _ensure()
    detail = msg if exc is None else '%s: %s' % (msg, exc)
    text = _line('ERROR', detail)
    _append('error', text)
    if exc is not None:
        _append('error', traceback.format_exc().rstrip('\n') + '\n')
    _print(text.rstrip('\n'))
    _append('info', _line('INFO', '发生错误，详见 error.log -> %s' % detail))
    return detail


def exception(msg):
    """在 except 块里直接调用，自动带上当前异常的堆栈"""
    return error(msg, sys.exc_info()[1])


def noisy(msg):
    """逐表/逐批次/锁重试等刷屏信息：不落盘，仅终端可见（LOG_ECHO_CONSOLE=False 时完全静默）"""
    _ensure()
    _print(_line('DEBUG', msg).rstrip('\n'))
