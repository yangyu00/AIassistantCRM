# -*- coding: utf-8 -*-
"""
业务注册表：一个业务 = 一条配置，不再一个业务一个目录。

字段说明（前 8 项为必填，其余有默认值）：
  prefixes         该业务的 AVL 文件名前缀，可多个
  table_prefix     分表名前缀，最终表名为 {table_prefix}_{0..N-1}
  schema           表结构 [(列名, 类型), ...]，顺序必须与 AVL 列顺序一致
  primary_key      主键元组；None = 普通 INSERT（追加），有值 = INSERT OR REPLACE 去重
  index_columns    索引列
  expire_days      (数据保留天数, 本地 AVL 文件保留天数)
  parser           解析器类型：'line'（一行一条）| 'wide'（宽表展开）| 'legacy'（老 f09554 容错行式）
  router           分表路由：'crc32'（按 route_column 的 crc32 取模）| 'map'（按 product_id 查表）

  route_column     路由字段，默认 product_no
  num_tables       分表数，默认取 DEFAULT_CONFIG['NUM_TABLES']（128）
  wide_header_cols wide 模式：记录头列数，默认 3
  wide_pair_cols   wide 模式：(id列, 内容列)，默认 ('product_id', 'strategy_script')
  min_columns      line/legacy 模式：少于该列数视为异常行，默认 len(schema)
  tail_absorb      line 模式：最后一列吸收剩余全部内容（含制表符），默认 False
  continuation_col 续行拼回的列名，默认 None
  required_columns 任一为空则整行丢弃的列，默认 ()
  int_columns      legacy 模式：需要转 int 的列，转换失败置 default
  int_default      int 转换失败时的默认值，默认 0
  product_table_map  router='map' 必填：{product_id: 表名}
  db_file         库文件名，默认 {table_prefix}.db
  query_columns    查询返回列名，默认 = schema 列名
"""
import os

# ============ 运行期根目录 ============
# 所有业务的文件 / 库 / 日志统一放在这个目录下，按 业务ID 分子目录：
#   <runtime_dir>/F09554/{files,sqlite_db,logs}/
# 用环境变量 SFTP_RUNTIME_DIR 指定；不指定则用本文件所在目录的上一级 / runtime
RUNTIME_DIR = os.environ.get('SFTP_RUNTIME_DIR') or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'runtime')

# ============ 各业务差异配置 ============
F09554_CONFIG = {
    'prefixes': ['F09554', 'F09555', 'F09556'],   # 号卡 / 套餐 / 宽带
    'table_prefix': 'data_F09554',
    'schema': [
        ('op_time', 'TEXT'),
        ('call_time', 'TEXT'),
        ('product_no', 'TEXT'),
        ('type_dl', 'VARCHAR(50)'),
        ('type_xl', 'VARCHAR(50)'),
        ('content_abstract', 'TEXT'),
        ('cust_demand', 'TEXT'),
        ('rec_strategy', 'TEXT'),
        ('reject_reason', 'TEXT'),
        ('reason_note', 'TEXT'),
        ('refer_strategy', 'TEXT'),
        ('city_id', 'INTEGER'),
    ],
    'primary_key': None,
    'index_columns': ('product_no', 'op_time'),
    'expire_days': (30, 7),
    'parser': 'legacy',
    'router': 'map',
    'route_column': 'product_no',
    'product_table_map': {
        '202608041615335173': 'data_F09554',
        '202608041615335172': 'data_F09555',
        '202608041615335174': 'data_F09556',
    },
    'int_columns': ('city_id',),
    'int_default': 0,
    # 与老服务一致：每 10000 条提交一次
    'batch_size': 10000,
}

F09591_CONFIG = {
    'prefixes': ['F09591'],
    'table_prefix': 'data_F09591',
    'schema': [
        ('op_time', 'TEXT'),
        ('user_id', 'TEXT'),
        ('product_no', 'TEXT'),
        ('city_id', 'TEXT'),
        ('strategy_name', 'TEXT'),
    ],
    'primary_key': None,
    'index_columns': ('product_no', 'op_time'),
    'expire_days': (50, 50),
    'parser': 'line',
    'router': 'crc32',
    'min_columns': 4,
    'tail_absorb': True,
    'required_columns': ('op_time', 'product_no'),
    'continuation_col': 'strategy_name',
}

F09609_CONFIG = {
    'prefixes': ['F09609'],
    'table_prefix': 'data_F09609',
    'schema': [
        ('op_time', 'TEXT'),
        ('user_id', 'TEXT'),
        ('product_no', 'TEXT NOT NULL'),
        ('product_id', 'TEXT NOT NULL'),
        ('strategy_script', 'TEXT'),
    ],
    'primary_key': ('product_no', 'product_id'),
    'index_columns': ('op_time',),
    'expire_days': (2, 2),
    'parser': 'wide',
    'router': 'crc32',
    'wide_header_cols': 3,
    'wide_pair_cols': ('product_id', 'strategy_script'),
}

# ============ 注册表：job_id -> 配置 ============
JOBS = {
    'F09554': F09554_CONFIG,
    'F09591': F09591_CONFIG,
    'F09609': F09609_CONFIG,
}


def job_ids():
    return list(JOBS.keys())


# 各业务缺省值：原代码依赖 sftp_common.DEFAULT_CONFIG 的全局键，这里按业务补齐，
# 避免 KeyError，也让每个业务可单独覆盖
_DEFAULTS = {
    'NUM_TABLES': 128,
    'BATCH_SIZE': 500,
    'DB_BUSY_TIMEOUT_MS': 30000,
    'INSERT_OR_REPLACE': True,
    'WAL_CHECKPOINT_PAGES': 1000,
    'EXPIRE_TIME_COLUMN': 'op_time',
    # 下面几项只在 SFTP 子进程拉数流程里用到，查询路径补默认值防 KeyError
    'SFTP_REMOTE_PATH': '/', 'REMOTE_FILE_TEMPLATE': '{prefix}*.txt',
    'LOCAL_DIR': 'runtime', 'DB_DIR': 'runtime', 'BASE_DIR': 'runtime',
    'DB_RETRY_TIMES': 3, 'DB_RETRY_BACKOFF_BASE': 0.5,
    'LOG_DIR_NAME': 'logs', 'LOG_FILE_INFO': 'info.log', 'LOG_FILE_ERROR': 'error.log',
    'LOG_MAX_BYTES': 10485760, 'LOG_ECHO_CONSOLE': False,
    'SKIP_IMPORT_IF_EXISTS': False, 'TREAT_DOWNLOAD_FAILURE_AS_FATAL': False,
}


def job_config(job_id: str) -> dict:
    cfg = dict(_DEFAULTS)
    cfg.update(JOBS[job_id])
    cfg.setdefault('num_tables', cfg['NUM_TABLES'])
    cfg['DB_SCHEMA'] = cfg['schema']
    cfg.setdefault('DB_FILE_NAME', f"{cfg['table_prefix']}.db")
    return cfg


def table_prefix(job_id: str) -> str:
    return JOBS[job_id]['table_prefix']
