# -*- coding: utf-8 -*-
"""
统一起子进程：取代 3 份几乎一样的 sftp_workdir_fXXXXX/run.py。

在后台线程里调用 run_job('F09554')，阻塞当前线程直到子进程结束，不阻塞 FastAPI 主循环。
"""
import subprocess
import sys
import threading

from .config import JOBS
from .config import RUNTIME_DIR
from .service import build_job_config


def run_job(job_id: str, extra_args=None, env=None) -> int:
    """同步执行一个业务的入库子进程，返回退出码（0 为成功）"""
    if job_id not in JOBS:
        raise KeyError(f"未注册的业务: {job_id}，可选: {list(JOBS)}")
    cfg = build_job_config(job_id)
    # 让子进程里的 sftp_common 走同一套 runtime 路径
    import os
    run_env = dict(os.environ)
    run_env.setdefault('SFTP_RUNTIME_DIR', RUNTIME_DIR)
    if env:
        run_env.update(env)
    cmd = [sys.executable, '-m', 'sftp_jobs.run_one', job_id] + list(extra_args or [])
    result = subprocess.run(cmd, env=run_env)
    return result.returncode


def start_job_thread(job_id: str, extra_args=None, daemon: bool = True) -> threading.Thread:
    t = threading.Thread(target=run_job, args=(job_id, extra_args), daemon=daemon, name=f'sftp-{job_id}')
    t.start()
    return t


def start_all(daemon: bool = True):
    """启动全部已注册业务（替代 main.py 里三段重复的线程启动代码）"""
    return [start_job_thread(job_id, daemon=daemon) for job_id in JOBS]
