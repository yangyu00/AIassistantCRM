# -*- coding: utf-8 -*-
"""
通用业务入口：把「业务配置」变成「可直接跑的 sftp_common 配置」。

用法（替代原来的 sftp_service.py）：
    from sftp_jobs.service import build_job_config
    from sftp_common.runner import main
    CONFIG = build_job_config('F09554')
    if __name__ == '__main__':
        main(CONFIG)
"""
from sftp_common.config import DEFAULT_CONFIG

from . import table_router
from .config import JOBS
from .legacy_parser import LegacyLineParser
from .runtime_paths import local_dir, db_dir, db_path, log_dir
from .wide_parser import WidePairParser


def build_job_config(job_id: str) -> dict:
    import os
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(JOBS[job_id])
    # SFTP 密码只从环境变量读, 不写死在代码里
    if os.environ.get('SFTP_PASSWORD'):
        cfg['SFTP_PASSWORD'] = os.environ['SFTP_PASSWORD']

    # ---- 路径：统一落在 runtime/<job_id>/ 下 ----
    cfg['LOCAL_DIR'] = local_dir(job_id)
    cfg['DB_DIR'] = db_dir(job_id)
    cfg['DB_FILE_NAME'] = cfg.get('db_file') or f"{cfg['table_prefix']}.db"
    cfg['DB_PATH'] = db_path(job_id, cfg['DB_FILE_NAME'])
    cfg['LOG_DIR_NAME'] = ''
    cfg['LOG_DIR'] = log_dir(job_id)

    # ---- 兼容 sftp_common 原有的字段名 ----
    prefixes = cfg['prefixes']
    cfg['FILE_PREFIXES'] = prefixes[0]          # 单文件下载 / 日志命名用主前缀
    cfg['TABLE_NAME_PREFIX'] = cfg['table_prefix']
    cfg['DB_SCHEMA'] = [tuple(c) for c in cfg['schema']]
    cfg['PRIMARY_KEY'] = tuple(cfg['primary_key']) if cfg.get('primary_key') else None
    cfg['INDEX_COLUMNS'] = tuple(cfg['index_columns'])
    cfg['EXPIRE_TIME_COLUMN'] = 'op_time'
    # map 模式：写入仍按 product_no crc32 分表；查询按 product_id 查老单表
    cfg['NUM_TABLES'] = int(cfg.get('num_tables', DEFAULT_CONFIG['NUM_TABLES']))
    if cfg['router'] == 'map':
        cfg['QUERY_TABLE_MAP'] = dict(cfg['product_table_map'])
        # 注意：F09554 的 schema 没有 product_id 列，写入仍按 product_no 的 crc32
        # 落分表；老单表由 table_router.table_names() 登记，见该模块说明。
    cfg['DATA_EXPIRE_DAYS'], cfg['LOCAL_FILE_EXPIRE_DAYS'] = cfg['expire_days']
    cfg['INSERT_OR_REPLACE'] = cfg['primary_key'] is not None
    cfg.setdefault('MIN_COLUMNS', len(cfg['schema']))
    if 'batch_size' in cfg:
        cfg['BATCH_SIZE'] = int(cfg['batch_size'])

    # ---- 解析器 ----
    kind = cfg['parser']
    if kind == 'legacy':
        cfg['_parser_factory'] = lambda c, names: LegacyLineParser(c, names)
    elif kind == 'wide':
        cfg['HEADER_COLUMNS'] = int(cfg.get('wide_header_cols', 3))
        cfg['PAIR_COLUMNS'] = tuple(cfg.get('wide_pair_cols', ('product_id', 'strategy_script')))
        cfg['_parser_factory'] = lambda c, names: WidePairParser(c, names)
    elif kind == 'line':
        from sftp_common.parsers import F09591Parser
        cfg['_parser_factory'] = lambda c, names: F09591Parser(c, names)
    else:
        raise ValueError(f"未知 parser 类型: {kind}")
    return cfg


def jobs():
    """给 main.py / runner 用：[(job_id, config), ...]"""
    return [(job_id, build_job_config(job_id)) for job_id in JOBS]
