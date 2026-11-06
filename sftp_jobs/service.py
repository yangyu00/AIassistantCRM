# -*- coding: utf-8 -*-
"""
通用业务入口：把「业务配置」变成「可直接跑的 sftp_common 配置」。

每个 job 对应一个独立的 SQLite 库（DB_PATH = runtime/<job_id>/sqlite_db/<prefix>.db），
分表路由统一为 crc32（sftp_common.db.TableBatcher 默认行为），不再需要 map 路由与猴子补丁。

用法（替代原来的 sftp_service.py）：
    from sftp_jobs.service import build_job_config
    from sftp_common.runner import main
    CONFIG = build_job_config('F09554')
    if __name__ == '__main__':
        main(CONFIG)
"""
import logging

from sftp_common.config import DEFAULT_CONFIG
from sftp_common.parsers import F09591Parser

from .config import JOBS
from .legacy_parser import LegacyLineParser
from .product_injector import ProductIdInjector
from .runtime_paths import local_dir, db_dir, db_path, log_dir, db_file_of
from .wide_parser import WidePairParser

log = logging.getLogger(__name__)

# parser 类型 → 解析器类
_PARSERS = {
    'legacy': LegacyLineParser,
    'wide': WidePairParser,
    'line': F09591Parser,
}


def build_job_config(job_id: str) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(JOBS[job_id])

    # ---- 路径：每个库独立落在 runtime/<job_id>/ 下 ----
    db_file = db_file_of(cfg)
    cfg['LOCAL_DIR'] = local_dir(job_id)
    cfg['DB_DIR'] = db_dir(job_id)
    cfg['DB_FILE_NAME'] = db_file
    cfg['DB_PATH'] = db_path(job_id, db_file)
    cfg['LOG_DIR_NAME'] = ''
    cfg['LOG_DIR'] = log_dir(job_id)

    # ---- 兼容 sftp_common 原有的字段名 ----
    prefixes = cfg['prefixes']
    if len(prefixes) != 1:
        raise ValueError(f"一个业务只能对应一个文件前缀，当前: {prefixes}")
    cfg['FILE_PREFIXES'] = prefixes[0]          # 单文件下载 / 日志命名用主前缀
    cfg['TABLE_NAME_PREFIX'] = cfg['table_prefix']
    cfg['DB_SCHEMA'] = [tuple(c) for c in cfg['schema']]
    cfg['PRIMARY_KEY'] = tuple(cfg['primary_key']) if cfg.get('primary_key') else None
    cfg['INDEX_COLUMNS'] = tuple(cfg['index_columns'])
    cfg['EXPIRE_TIME_COLUMN'] = 'op_time'
    cfg['NUM_TABLES'] = int(cfg.get('num_tables', DEFAULT_CONFIG['NUM_TABLES']))
    cfg['DATA_EXPIRE_DAYS'], cfg['LOCAL_FILE_EXPIRE_DAYS'] = cfg['expire_days']
    cfg['INSERT_OR_REPLACE'] = cfg['primary_key'] is not None
    cfg.setdefault('MIN_COLUMNS', len(cfg['schema']))
    if 'batch_size' in cfg:
        cfg['BATCH_SIZE'] = int(cfg['batch_size'])

    # ---- 解析器 ----
    kind = cfg['parser']
    cls = _PARSERS.get(kind)
    if cls is None:
        raise ValueError(f"未知 parser 类型: {kind}")
    if kind == 'line':
        # 未显式配置时，沿用 sftp_common 默认：续行拼回 strategy_name
        cfg.setdefault('CONTINUATION_COLUMN', DEFAULT_CONFIG.get('CONTINUATION_COLUMN'))
        cfg.setdefault('REQUIRED_COLUMNS', DEFAULT_CONFIG.get('REQUIRED_COLUMNS', ()))
    if kind == 'wide':
        cfg['HEADER_COLUMNS'] = int(cfg.get('wide_header_cols', 3))
        cfg['PAIR_COLUMNS'] = tuple(cfg.get('wide_pair_cols', ('product_id', 'strategy_script')))
        if cfg.get('continuation_col'):
            cfg['CONTINUATION_COLUMN'] = cfg['continuation_col']
    # 解析器统一由 sftp_common.parsers.get_parser 按 FILE_PREFIXES 选择，
    # 不再注入 _parser_factory（原注入的 WidePairParser 未在 parsers 中注册，必然 AttributeError）

    # ---- product_id 注入：配置了 product_id 的业务，入库时自动补列 ----
    if cfg.get('product_id') and cfg.get('product_id_column'):
        injector = ProductIdInjector(cfg, cfg['product_id_column'], cfg['product_id'])
        cfg['_wrap_batcher'] = injector.wrap
    return cfg


def jobs():
    """给 main.py / runner 用：[(job_id, config), ...]"""
    return [(job_id, build_job_config(job_id)) for job_id in JOBS]
