# -*- coding: utf-8 -*-
"""全局配置：统一从 .env 文件 + 环境变量读取。

优先级（高 → 低）：
  1. 系统环境变量（Docker -e / K8s Secret / export），适合放密码等敏感项
  2. 项目根目录下的 .env 文件（复制 .env.example 后填写）
  3. 本文件里 DEFAULT_ENV 的兜底默认值

用法：
    from config import config

    config['IOP']['TOKEN']['host']
    config.get('log_dir')                       # 字典式访问，支持默认值
    config.fastapi                              # 属性式访问
    config.IOP.TOKEN.secret                     # 任意层级属性式访问

配置对象只读：任何层级的写操作（cfg['x']=1 / cfg.x=1 / del）都会抛 RuntimeError，
避免运行期被意外改配置（替代原来的 ImmutableYamlConfig）。
项目根目录定位：从本文件所在目录向上查找含 .env 的目录，找不到则用本文件所在目录。
"""
import os
from collections.abc import Mapping
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # 没装 python-dotenv 时也能跑，只是不自动读 .env 文件
    def load_dotenv(*args, **kwargs):
        return False


def find_project_root() -> Path:
    """向上查找 .env 所在目录作为项目根；没有 .env 则返回本文件所在目录。"""
    cur = Path(__file__).resolve().parent
    for p in (cur, *cur.parents):
        if (p / '.env').is_file():
            return p
    return cur


PROJECT_ROOT = find_project_root()
# override=False：系统环境变量优先于 .env 文件
load_dotenv(PROJECT_ROOT / '.env', override=False)


# ---------------- .env 中各项的兜底默认值（与 .env.example 保持一致） ----------------
DEFAULT_ENV = {
    'LOG_DIR': '',
    'APP_HOST': '0.0.0.0',
    'APP_PORT': '8989',
    'APP_RELOAD': 'false',
    'API_URL': '/api/speechQryService',
    'API_USERS': 'CRM',
    'API_TOKENS': '',
    'IOP_TOKEN_HOST': '134.80.220.55',
    'IOP_TOKEN_PORT': '80',
    'IOP_CLIENT_ID': '202408131154527921000059',
    'IOP_CLIENT_SECRET': '',
    'IOP_CUSTINFO_HOST': '134.80.220.55',
    'IOP_CUSTINFO_PORT': '80',
    'IOP_CUSTINFO_VERSION': '1.0.1',
    'IOP_CUSTINFO_TYPE': 'M',
    'IOP_MAX_CONCURRENCY': '20',
    'SFTP_HOST': '134.80.199.252',
    'SFTP_PORT': '22',
    'SFTP_USERNAME': 'wangsi',
    'SFTP_PASSWORD': '',
    'DIM_TABLE_CHECK_SECONDS': '300',
    'SFTP_STOP_TIMEOUT': '30',
}


def _env(key: str):
    """先查环境变量，再查默认值。"""
    value = os.environ.get(key)
    if value is None or value == '':
        value = DEFAULT_ENV.get(key, '')
    return value


def _as_bool(value) -> bool:
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def _as_list(value):
    if not value:
        return []
    return [x.strip() for x in str(value).replace(';', ',').split(',') if x.strip()]


class _ReadOnly(Mapping):
    """递归只读配置：支持 ['key'] 与 .key 两种访问方式，任何写操作都报错。"""

    __slots__ = ('_data',)

    def __init__(self, data):
        # 不在这里递归包装，避免 __setattr__ 拦截 _data 赋值
        object.__setattr__(self, '_data', data)

    @staticmethod
    def wrap(obj):
        if isinstance(obj, Mapping):
            return _ReadOnly({k: _ReadOnly.wrap(v) for k, v in obj.items()})
        if isinstance(obj, (list, tuple)):
            return tuple(_ReadOnly.wrap(v) for v in obj)
        return obj

    # ---- 读 ----
    def __getitem__(self, key):
        value = self._data[key]
        if isinstance(value, _ReadOnly) and value._data is None:
            return None
        return value

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        try:
            return self[name]
        except KeyError:
            raise AttributeError(f"配置项不存在: {name}")

    def get(self, key, default=None):
        value = self._data.get(key, default)
        return default if value is None else value

    def as_dict(self):
        """转成普通 dict（需要传给 **kwargs 时用，如 uvicorn.run）。"""

        def unwrap(v):
            if isinstance(v, _ReadOnly):
                return {k: unwrap(x) for k, x in v._data.items()}
            if isinstance(v, tuple):
                return [unwrap(x) for x in v]
            return v

        return unwrap(self)

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)

    def __contains__(self, key):
        return key in self._data

    def __repr__(self):
        return f'<Config {self._data!r}>'

    # ---- 禁止修改 ----
    _MSG = '配置只读，禁止运行时修改！请修改 .env 后重启服务。'

    def __setitem__(self, key, value):
        raise RuntimeError(self._MSG)

    def __delitem__(self, key):
        raise RuntimeError(self._MSG)

    def __setattr__(self, name, value):
        raise RuntimeError(self._MSG)

    def __delattr__(self, name):
        raise RuntimeError(self._MSG)

    def clear(self):
        raise RuntimeError(self._MSG)

    def pop(self, *args, **kwargs):
        raise RuntimeError(self._MSG)

    def update(self, *args, **kwargs):
        raise RuntimeError(self._MSG)


def build_config() -> _ReadOnly:
    """从环境变量 / .env 组装完整配置树（结构与原 config.yaml 一一对应）。"""
    raw = {
        'log_dir': _env('LOG_DIR'),
        'fastapi': {
            'host': _env('APP_HOST'),
            'port': int(_env('APP_PORT')),
            'reload': _as_bool(_env('APP_RELOAD')),
        },
        'marketAssistant': {
            'url': _env('API_URL'),
            'users': _as_list(_env('API_USERS')),
            # 逗号分隔的 Bearer token；留空 = 不校验（仅限内网调试）
            'api_tokens': _as_list(_env('API_TOKENS')),
        },
        'IOP': {
            'TOKEN': {
                'host': _env('IOP_TOKEN_HOST'),
                'port': _env('IOP_TOKEN_PORT'),
                'id': _env('IOP_CLIENT_ID'),
                'secret': _env('IOP_CLIENT_SECRET'),
            },
            'CustInfo': {
                'host': _env('IOP_CUSTINFO_HOST'),
                'port': _env('IOP_CUSTINFO_PORT'),
                'version': _env('IOP_CUSTINFO_VERSION'),
                'type': _env('IOP_CUSTINFO_TYPE'),
            },
            'max_concurrency': int(_env('IOP_MAX_CONCURRENCY') or 20),
        },
        'sftp': {
            'host': _env('SFTP_HOST'),
            'port': int(_env('SFTP_PORT') or 22),
            'username': _env('SFTP_USERNAME'),
            'password': _env('SFTP_PASSWORD'),
            'runtime_dir': _env('SFTP_RUNTIME_DIR'),  # SFTP 文件/库/日志根目录，空 = 项目下 runtime/
        },
        'dim_table': {
            'check_seconds': int(_env('DIM_TABLE_CHECK_SECONDS') or 300),
        },
        # 兼容旧配置项：原 YAML 未设置，代码里默认 30
        'sftp_stop_timeout': int(_env('SFTP_STOP_TIMEOUT') or 30),
    }
    return _ReadOnly.wrap(raw)


# 单例：全局唯一配置对象
config = build_config()

# 向后兼容别名：旧代码写的是 ImmutableYamlConfig()，保留同名入口，迁移期不用改调用方
ImmutableYamlConfig = lambda *args, **kwargs: config  # noqa: E731


if __name__ == '__main__':
    # python config.py → 打印当前生效配置（密码打码），排查“配置没生效”时用
    def _show(obj, indent=0):
        for k, v in obj.items():
            if isinstance(v, _ReadOnly):
                print(' ' * indent + f'{k}:')
                _show(v, indent + 2)
            else:
                text = str(v)
                if k in ('secret', 'password', 'api_tokens') and text:
                    text = '******'
                print(' ' * indent + f'{k}: {text}')

    print(f'.env 文件: {PROJECT_ROOT / ".env"}  (存在: {(PROJECT_ROOT / ".env").is_file()})')
    _show(config)
