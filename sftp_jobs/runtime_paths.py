# -*- coding: utf-8 -*-
"""运行期目录：每个业务一个独立目录，库文件互不混用。
   库路径统一走 db_path()，禁止在别处拼 sqlite 路径。"""
import os

from .config import RUNTIME_DIR


def job_dir(job_id: str) -> str:
    return os.path.join(RUNTIME_DIR, job_id)


def local_dir(job_id: str) -> str:
    return os.path.join(job_dir(job_id), 'files')


def db_dir(job_id: str) -> str:
    return os.path.join(job_dir(job_id), 'sqlite_db')


def log_dir(job_id: str) -> str:
    return os.path.join(job_dir(job_id), 'logs')


def db_file_of(cfg: dict) -> str:
    return cfg.get('db_file') or f"{cfg['table_prefix']}.db"


def db_path(job_id: str, db_file: str) -> str:
    return os.path.join(db_dir(job_id), db_file)
