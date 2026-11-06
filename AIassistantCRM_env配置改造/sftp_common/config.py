import os

from config import config
# -*- coding: utf-8 -*-
"""
共用配置：两个业务的差异项都收敛到这里。

分三层：
  1. DEFAULT_CONFIG  —— 两个业务完全一样的内容（SFTP 账号、路径、分表数、锁重试、日志…）
  2. F09609_CONFIG / F09591_CONFIG —— 各业务真正不同的项（前缀、表结构、保留天数…）
  3. build_config()  —— 合并出最终配置，并统一下发脚本目录 / 本地目录 / DB 路径

业务脚本只需要写：
    from sftp_common.config import build_config, F09609_CONFIG
    CONFIG = build_config(F09609_CONFIG, __file__)
"""
import os

# ================= 通用默认配置（两业务一致） =================
DEFAULT_CONFIG = {
    # --- SFTP 连接（两台脚本连的是同一台服务器、同一个远程目录）---
    'SFTP_HOST': config['sftp']['host'],
    'SFTP_PORT': config['sftp']['port'],
    'SFTP_USERNAME': config['sftp']['username'],
    # 密码不入库，统一由环境变量 SFTP_PASSWORD 注入（见 sftp_jobs/service.py）
    'SFTP_PASSWORD': config['sftp']['password'],  # 来自 .env 的 SFTP_PASSWORD（系统环境变量优先）
    'SFTP_REMOTE_PATH': "/ftpdata2/data_reverse/wangsi/aifile",
    # 远程文件名模板：前缀 + 日期 + 000000.AVL
    'REMOTE_FILE_TEMPLATE': "{prefix}{date}000000.AVL",

    # --- 分表 ---
    'NUM_TABLES': 128,
    'BATCH_SIZE': 5000,

    # --- 路径 ---
    'LOCAL_DIR_NAME': "files",   # 本地文件下载目录名（相对脚本目录）
    'DB_DIR_NAME': "sqlite_db",  # 本地数据库存储目录名（相对脚本目录）
    'DB_FILE_NAME': "{prefix}.db",

    # --- 两版脚本行为统一后的口径（以 f09609 为准）---
    # 本地文件已存在且不指定 --force-reload：下载和入库一起跳过
    'SKIP_IMPORT_IF_EXISTS': True,
    # 远程文件不存在 / 下载失败：不当作程序出错（退出码 0），仍执行过期清理
    'TREAT_DOWNLOAD_FAILURE_AS_FATAL': False,
    # 兜底：远程时间解析失败时，按本地时间处理（原 f09591 更宽容的口径）
    'FALLBACK_TO_LOCAL_TIME': True,

    # --- 锁与重试 ---
    'DB_BUSY_TIMEOUT_MS': 30000,   # database is locked 时 SQLite 内部等待上限
    'DB_RETRY_TIMES': 5,           # 事务级重试次数
    'DB_RETRY_BACKOFF_BASE': 0.2,  # 退避基数（秒）：0.2 → 0.4 → 0.8 → 1.6
    'WAL_CHECKPOINT_PAGES': 2000,  # WAL 自动检查点阈值（页）

    # --- SFTP 下载 ---
    'DOWNLOAD_KEEPALIVE_INTERVAL': 10,   # 心跳间隔（秒），0 表示关闭
    'DOWNLOAD_CHANNEL_TIMEOUT': 120,     # 通道读超时（秒）
    'SSH_CONNECT_TIMEOUT': 30,           # TCP 连接 / banner / 认证超时（秒）
    'DOWNLOAD_RETRY_TIMES': 3,           # 下载失败重试次数（含首次）
    'DOWNLOAD_RETRY_BACKOFF_BASE': 5,    # 下载重试退避基数（秒）

    # --- 日志 ---
    'LOG_DIR_NAME': "logs",
    'LOG_FILE_INFO': "info.log",
    'LOG_FILE_ERROR': "error.log",
    'LOG_MAX_BYTES': 10 * 1024 * 1024,
    'LOG_ECHO_CONSOLE': False,

    # --- 解析 ---
    'CORRUPT_ROW_LOG_LIMIT': 20,  # 异常行最多打印多少条明细，避免刷屏
}


# ================= 业务差异配置 =================

# ---------- F09609：宽表，一条记录展开成多条 ----------
# 字段：日期 / user_id / product_no + 若干组 (product_id, strategy_script)
# 分表按 product_no 的 crc32 取模；入库用 INSERT OR REPLACE（主键去重）
F09609_CONFIG = {
    'FILE_PREFIXES': "F09609",
    'TABLE_NAME_PREFIX': "data_F09609",
    'DATA_EXPIRE_DAYS': 2,
    'LOCAL_FILE_EXPIRE_DAYS': 2,

    'DB_SCHEMA': [
        ("op_time", "TEXT"),
        ("user_id", "TEXT"),
        ("product_no", "TEXT NOT NULL"),
        ("product_id", "TEXT NOT NULL"),
        ("strategy_script", "TEXT"),
    ],
    'PRIMARY_KEY': ("product_no", "product_id"),
    'INDEX_COLUMNS': ("op_time",),
    # 清理过期数据用的时间列
    'EXPIRE_TIME_COLUMN': "op_time",
    # 宽表：前 N 列是记录头，之后两两一组
    'HEADER_COLUMNS': 3,
    'PAIR_COLUMNS': ("product_id", "strategy_script"),

    'INSERT_OR_REPLACE': True,
}

# ---------- F09591：一行一条记录，最后一列可能含裸换行 ----------
# 字段：日期 / user_id / product_no / city_id / strategy_name
# 续行拼回最后一列（还原 \n）；入库用普通 INSERT（追加模式）
F09591_CONFIG = {
    'FILE_PREFIXES': "F09591",
    'TABLE_NAME_PREFIX': "data_F09591",
    'DATA_EXPIRE_DAYS': 50,
    'LOCAL_FILE_EXPIRE_DAYS': 50,

    'DB_SCHEMA': [
        ("op_time", "TEXT"),
        ("user_id", "TEXT"),
        ("product_no", "TEXT"),
        ("city_id", "TEXT"),
        ("strategy_name", "TEXT"),
    ],
    'PRIMARY_KEY': None,
    # 复合索引与 F09609 不同，按业务配置
    'INDEX_COLUMNS': ("product_no", "op_time"),
    'EXPIRE_TIME_COLUMN': "op_time",

    # 行式：少于该列数视为严重错误行；最后一列吸收剩余全部内容（含制表符）
    'MIN_COLUMNS': 4,
    'TAIL_ABSORB': True,
    # 分表/校验依赖的关键字段，任一为空则整行丢弃
    'REQUIRED_COLUMNS': ("op_time", "product_no"),
    # 续行拼回的那一列
    'CONTINUATION_COLUMN': "strategy_name",

    'INSERT_OR_REPLACE': False,
}


def build_config(business_config: dict, script_path: str) -> dict:
    """默认值 + 业务差异 → 最终配置，并补充运行期派生路径"""
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(business_config)
    cfg['BASE_DIR'] = os.path.dirname(os.path.abspath(script_path))
    cfg['LOCAL_DIR'] = os.path.join(cfg['BASE_DIR'], cfg['LOCAL_DIR_NAME'])
    cfg['DB_DIR'] = os.path.join(cfg['BASE_DIR'], cfg['DB_DIR_NAME'])
    cfg['DB_FILE_NAME'] = cfg['DB_FILE_NAME'].format(prefix=cfg['FILE_PREFIXES'])
    cfg['DB_PATH'] = os.path.join(cfg['DB_DIR'], cfg['DB_FILE_NAME'])
    return cfg
