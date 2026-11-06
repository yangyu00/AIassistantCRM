# -*- coding: utf-8 -*-
"""全局配置：从 .env 文件 + 环境变量读取（简洁版）。

优先级（高 → 低）：
  1. 系统环境变量（Docker -e / K8s Secret / export），适合放密码等敏感项
  2. 项目根目录下的 .env 文件（复制 .env.example 后填写）
  3. 本文件各配置类里的默认值

用法：
    from config import config

    config.IOP.TOKEN.host            # 属性式访问（推荐）
    config['IOP']['TOKEN']['host']   # 字典式访问（兼容旧写法，大小写不敏感）
    config.fastapi.port
"""
import json
import os
from dataclasses import dataclass, asdict, fields
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # 没装 python-dotenv 时也能跑，只是不自动读 .env
    def load_dotenv(*args, **kwargs):
        return False

# config.py 在项目根目录下，父目录即项目根
BASE_DIR = Path(__file__).resolve().parent
# override=False：系统环境变量优先于 .env 文件
load_dotenv(BASE_DIR / ".env", override=False)


def _str(key, default=""):
    value = os.getenv(key)
    return default if value is None or value == "" else value


def _int(key, default):
    return int(_str(key, str(default)))


def _bool(key, default=False):
    return _str(key, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _list(key, default=""):
    return [x.strip() for x in _str(key, default).replace(";", ",").split(",") if x.strip()]


class _ConfigBase:
    """配置基类：frozen dataclass（只读）+ 兼容 config['xxx'] 字典式访问（大小写不敏感）。"""

    @staticmethod
    def _lookup(obj, key):
        key = {"token": "TOKEN", "custinfo": "CustInfo"}.get(str(key).lower(), key)
        for f in fields(obj):
            if f.name == key or f.name.lower() == str(key).lower():
                return getattr(obj, f.name)
        raise KeyError(key)

    def __getitem__(self, key):
        return self._lookup(self, key)

    def as_dict(self, mask=False):
        return asdict(self)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key):
        try:
            self[key]
            return True
        except KeyError:
            return False


@dataclass(frozen=True)
class FastAPIConfig(_ConfigBase):
    """服务配置"""
    host: str = _str("APP_HOST", "0.0.0.0")
    port: int = _int("APP_PORT", 8989)
    reload: bool = _bool("APP_RELOAD", False)


@dataclass(frozen=True)
class MarketAssistantConfig(_ConfigBase):
    """接口鉴权配置"""
    url: str = _str("API_URL", "/api/speechQryService")
    users: list = None
    api_tokens: list = None  # 留空 = 不校验（仅限内网调试）

    def __post_init__(self):
        object.__setattr__(self, "users", _list("API_USERS", "CRM") if self.users is None else self.users)
        object.__setattr__(self, "api_tokens", _list("API_TOKENS") if self.api_tokens is None else self.api_tokens)


@dataclass(frozen=True)
class IOPGroup(_ConfigBase):
    """IOP TOKEN 接口配置"""
    host: str = _str("IOP_TOKEN_HOST", "134.80.220.55")
    port: str = _str("IOP_TOKEN_PORT", "80")
    id: str = "202408131154527921000059"
    secret: str = _str("IOP_CLIENT_SECRET")


@dataclass(frozen=True)
class CustInfoGroup(_ConfigBase):
    """IOP CustInfo 接口配置"""
    host: str = _str("IOP_CUSTINFO_HOST", "134.80.220.55")
    port: str = _str("IOP_CUSTINFO_PORT", "80")
    version: str = _str("IOP_CUSTINFO_VERSION", "1.0.1")
    type: str = _str("IOP_CUSTINFO_TYPE", "M")


@dataclass(frozen=True)
class IOPConfig(_ConfigBase):
    """IOP 接口配置"""
    TOKEN: IOPGroup = None
    CustInfo: CustInfoGroup = None
    max_concurrency: int = _int("IOP_MAX_CONCURRENCY", 20)

    def __post_init__(self):
        object.__setattr__(self, "TOKEN", self.TOKEN or IOPGroup(
            _str("IOP_TOKEN_HOST", "134.80.220.55"), _str("IOP_TOKEN_PORT", "80"),
            "202408131154527921000059", _str("IOP_CLIENT_SECRET")))
        object.__setattr__(self, "CustInfo", self.CustInfo or CustInfoGroup(
            _str("IOP_CUSTINFO_HOST", "134.80.220.55"), _str("IOP_CUSTINFO_PORT", "80")))


@dataclass(frozen=True)
class SFTPConfig(_ConfigBase):
    """SFTP 配置"""
    host: str = _str("SFTP_HOST", "134.80.199.252")
    port: int = _int("SFTP_PORT", 22)
    username: str = _str("SFTP_USERNAME", "wangsi")
    password: str = _str("SFTP_PASSWORD")
    runtime_dir: str = _str("SFTP_RUNTIME_DIR")  # 留空 = 项目下 runtime/


@dataclass(frozen=True)
class DimTableConfig(_ConfigBase):
    """维表配置"""
    check_seconds: int = _int("DIM_TABLE_CHECK_SECONDS", 300)


@dataclass(frozen=True)
class Config(_ConfigBase):
    log_dir: str = _str("LOG_DIR")  # 日志目录，留空 = 项目下 log/
    fastapi: FastAPIConfig = FastAPIConfig()
    marketAssistant: MarketAssistantConfig = MarketAssistantConfig()
    IOP: IOPConfig = IOPConfig()
    sftp: SFTPConfig = SFTPConfig()
    dim_table: DimTableConfig = DimTableConfig()
    sftp_stop_timeout: int = _int("SFTP_STOP_TIMEOUT", 30)

    def as_dict(self, mask=True):
        data = asdict(self)
        if not mask:
            return data
        # 打印时给敏感信息打码（data 是 asdict 后的普通 dict）
        secrets = []
        if self.IOP.TOKEN.secret:
            secrets.append(("IOP", "TOKEN", "secret"))
        if self.sftp.password:
            secrets.append(("sftp", None, "password"))
        for section, group, key in secrets:
            node = data[section][group] if group else data[section]
            node[key] = "******"
        if self.marketAssistant.api_tokens:
            data["marketAssistant"]["api_tokens"] = ["******"]
        return data


config = Config()

if __name__ == "__main__":
    # python config.py → 打印当前生效配置（密码打码），排查“配置没生效”时用
    print(f".env 文件: {BASE_DIR / '.env'}  (存在: {(BASE_DIR / '.env').is_file()})")
    print(json.dumps(config.as_dict(), ensure_ascii=False, indent=2))
