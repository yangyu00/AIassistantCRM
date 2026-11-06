# -*- coding: utf-8 -*-
"""子进程入口：python -m sftp_jobs.run_one F09554 [--date ...] [--force-reload]"""
import sys

from .service import build_job_config


def main():
    if len(sys.argv) < 2:
        print('用法: python -m sftp_jobs.run_one <JOB_ID> [--date YYYYMMDD] [--force-reload] [--no-cleanup]')
        return 2
    job_id = sys.argv[1].upper()
    cfg = build_job_config(job_id)
    # 安装 map 路由: F09554 按 product_id 写入老单表, 与查询口径一致
    from .router_patch import install
    install()
    # runner.main 内部读 sys.argv，这里把 job_id 摘掉，保留后面的参数
    sys.argv = [sys.argv[0]] + sys.argv[2:]
    from sftp_common.runner import main as run_main
    run_main(cfg)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
