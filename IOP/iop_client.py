# -*- coding: utf-8 -*-
"""IOP 异步客户端：共享 aiohttp 连接池 + token 缓存 + 超时 + 限并发。

- 所有请求复用 startup 时创建的 ClientSession，不再每次请求新建；
- token 进程内缓存，提前 6 分钟过期，获取时加锁，避免并发重复取；
- 标签查询 2s 超时，失败/超时降级为空标签，不拖垮整个接口。
"""
import asyncio
import datetime
import time

import aiohttp

from config import ImmutableYamlConfig
from src.log_util import my_log
from src.security import mask_sensitive

global_config = ImmutableYamlConfig()

TOKEN_URL = 'http://{host}:{port}/openapi/restOauth2Server/access_token'.format(
    host=global_config['IOP']['TOKEN']['host'], port=global_config['IOP']['TOKEN']['port'])
CUSTINFO_URL = 'http://{host}:{port}/openapi/httpService/UserService'.format(
    host=global_config['IOP']['CustInfo']['host'], port=global_config['IOP']['CustInfo']['port'])

CUSTINFO_TIMEOUT = aiohttp.ClientTimeout(total=2)
TOKEN_TIMEOUT = aiohttp.ClientTimeout(total=3)
CUSTINFO_HEADERS = {'Content-Type': 'application/json; charset=gbk',
                    'Accept-Encoding': 'gzip, deflate'}

_session: aiohttp.ClientSession = None
_gate: asyncio.Semaphore = None

# token 缓存
_token_value = None
_token_deadline = 0.0
_token_lock = asyncio.Lock()


async def startup():
    global _session, _gate
    if _session is None or _session.closed:
        connector = aiohttp.TCPConnector(limit=50, limit_per_host=20)
        _session = aiohttp.ClientSession(connector=connector)
        _gate = asyncio.Semaphore(int(global_config['IOP'].get('max_concurrency', 20)))


async def shutdown():
    global _session
    if _session is not None:
        await _session.close()
        _session = None


async def _fetch_token() -> str:
    data = {
        'grant_type': 'client_credentials',
        'client_id': global_config['IOP']['TOKEN']['id'],
        'client_secret': global_config['IOP']['TOKEN']['secret'],
    }
    async with _session.post(TOKEN_URL, data=data, timeout=TOKEN_TIMEOUT) as resp:
        res = await resp.json(content_type=None)
    if 'access_token' in res:
        return res['access_token'], int(res.get('expires_in', 600))
    my_log.error(f"获取 IOP token 失败: {res}")
    return None, 0


async def _get_token() -> str:
    global _token_value, _token_deadline
    now = time.monotonic()
    if _token_value and now < _token_deadline:
        return _token_value
    async with _token_lock:
        now = time.monotonic()
        if _token_value and now < _token_deadline:
            return _token_value
        for _ in range(3):
            token, expires_in = await _fetch_token()
            if token:
                _token_value = token
                # 提前 360s 过期，留出余量
                _token_deadline = time.monotonic() + max(expires_in - 360, 30)
                return token
            await asyncio.sleep(1)
    return None


async def get_custom_info(tel: str) -> dict:
    """按手机号查客户标签；超时或失败返回 {}（降级为无标签）。"""
    await startup()
    token = await _get_token()
    if not token:
        my_log.error("No access token")
        return {}

    url = f"{CUSTINFO_URL}?access_token={token}&method=qryCustInfoByChannel&format=json"
    post_data = {
        "VERSION": str(global_config['IOP']['CustInfo']['version']),
        "TELNUM": tel,
        "TYPE": str(global_config['IOP']['CustInfo']['type']),
    }
    async with _gate:
        try:
            async with _session.post(url, json=post_data, headers=CUSTINFO_HEADERS,
                                     timeout=CUSTINFO_TIMEOUT) as resp:
                status = resp.status
                try:
                    res = await resp.json(content_type=None)
                except Exception as e:
                    my_log.error(f"标签查询响应解析失败 status={status}, error={e}")
                    return {}
        except asyncio.TimeoutError:
            my_log.warning(f"标签查询超时(2s)，降级为空标签: {mask_sensitive(tel)}")
            return {}
        except Exception as e:
            my_log.error(f"标签查询异常: {mask_sensitive(tel)}: {e}")
            return {}

    if status != 200:
        my_log.error(f"标签查询 HTTP {status}: {mask_sensitive(tel)}")
        return {}
    if res.get("res_code") != "0":
        my_log.error(f"标签查询 res_code={res.get('res_code')}, res_msg={res.get('res_msg')}")
        return {}

    result = res.get("result") or {}
    labels = {}
    for item in result.get("BUSIINFO") or []:
        labels[item["ITEMCODE"]] = [item.get("ITEMNAME"), item.get("ITEMVALUE")]
    return labels
