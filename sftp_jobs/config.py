# -*- coding: utf-8 -*-
"""
业务注册表：一个业务 = 一条配置 = 一个独立的 SQLite 库。

字段说明（前 8 项为必填，其余有默认值）：
  prefixes         该业务的 AVL 文件名前缀（单元素列表，与主前缀一致）
  table_prefix     分表名前缀，最终表名为 {table_prefix}_{0..N-1}
  schema           表结构 [(列名, 类型), ...]，顺序必须与 AVL 列顺序一致
  primary_key      主键元组；None = 普通 INSERT（追加），有值 = INSERT OR REPLACE 去重
  index_columns    索引列
  expire_days      (数据保留天数, 本地 AVL 文件保留天数)
  parser           解析器类型：'line'（一行一条）| 'wide'（宽表展开）| 'legacy'（老 f09554 容错行式）

  product_id       该业务对应的产品ID（写入时落入 product_id 列，查询时二次校验）
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
  db_file         库文件名，默认 {table_prefix}.db
  query_columns    查询返回列名，默认 = schema 列名
"""
import os

from config import config

# ============ 运行期根目录 ============
# 所有业务的文件 / 库 / 日志统一放在这个目录下，按 业务ID 分子目录：
#   <runtime_dir>/F09554/{files,sqlite_db,logs}/
# 用环境变量 SFTP_RUNTIME_DIR 指定；不指定则用本文件所在目录的上一级 / runtime
# 来自 .env 的 SFTP_RUNTIME_DIR（系统环境变量优先）；未设置则用项目根下的 runtime/
RUNTIME_DIR = config.get('runtime_dir') or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'runtime')

# ============ 号卡 / 套餐 / 宽带 共用表结构（三库同构，只改差异项） ============
_CARD_SCHEMA = [
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
    ('product_id', 'TEXT'),  # 新增：写入时由配置填入，查询时二次校验，避免跨产品串记录
]


def _card_job(prefix: str, product_id: str) -> dict:
    """号卡类业务（F09554/55/56）配置模板：库、表前缀、产品ID 各不相同，其余一致"""
    return {
        'prefixes': [prefix],
        'table_prefix': f'data_{prefix}',
        'schema': list(_CARD_SCHEMA),
        'primary_key': None,
        'index_columns': ('product_no', 'op_time'),
        'expire_days': (30, 7),
        'parser': 'legacy',
        'router': 'crc32',
        'route_column': 'product_no',
        'product_id': product_id,
        'product_id_column': 'product_id',
        'int_columns': ('city_id',),
        'int_default': 0,
        'batch_size': 10000,
    }


F09554_CONFIG = _card_job('F09554', '202608041615335173')  # 号卡
F09555_CONFIG = _card_job('F09555', '202608041615335172')  # 套餐
F09556_CONFIG = _card_job('F09556', '202608041615335174')  # 宽带

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
    # [FIX] 原为 4：schema 有 5 列，4 列的残缺行会被当正常数据入库，
    # city_id 顶到 strategy_name 的位置、续行内容拼进 city_id，策略内容丢失。
    # 改成 5 后列数不足的行会被判为异常行（宽表/续行场景由 service 显式覆盖）
    'min_columns': 5,
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
    # 原为 (2, 2)：文件晚到/周末不出文件时，按 op_time < 今天-2 天会被清掉，
    # 查询侧拿到「无话术记录」。放宽到 7 天，兼顾兜底话术可用性
    'expire_days': (7, 7),
    'parser': 'wide',
    'router': 'crc32',
    'wide_header_cols': 3,
    'wide_pair_cols': ('product_id', 'strategy_script'),
}

# ============ 注册表：job_id -> 配置 ============
JOBS = {
    'F09554': F09554_CONFIG,
    'F09555': F09555_CONFIG,
    'F09556': F09556_CONFIG,
    'F09591': F09591_CONFIG,
    'F09609': F09609_CONFIG,
}

# ============ 产品ID → 业务 路由表（main.py 用，新增产品只改这里） ============
PRODUCT_JOB_MAP = {
    cfg['product_id']: job_id
    for job_id, cfg in JOBS.items()
    if 'product_id' in cfg
}


def job_ids():
    return list(JOBS.keys())


def table_prefix(job_id: str) -> str:
    return JOBS[job_id]['table_prefix']


def job_of_product(product_id: str):
    """产品ID → job_id；未注册返回 None"""
    return PRODUCT_JOB_MAP.get(product_id)
