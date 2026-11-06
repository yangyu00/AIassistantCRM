# -*- coding: utf-8 -*-
"""运行期目录：所有业务的文件 / 库 / 日志都落在 RUNTIME_DIR/<job_id>/ 下，不写进代码目录。"""
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


def db_path(job_id: str, db_file: str) -> str:
    return os.path.join(db_dir(job_id), db_file)
