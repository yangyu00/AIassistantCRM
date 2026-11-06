# -*- coding: utf-8 -*-
"""
共用 SFTP 下载：连接、心跳、可靠下载、过期清理。

可靠性要点：
  - 先 stat 拿远程大小，下完做大小校验，避免「连接断了但落了半截文件」被当成成功
  - 先写 .part 临时文件，校验通过才原子改名，杜绝半截文件被下次运行当成完整文件跳过下载
  - 下载期间周期发 SSH 忽略包，避免空闲连接被中间设备掐断
  - 通道读超时 + 失败重试；任何一次尝试留下的 .part 都会清掉
"""
import os
import socket
import threading
import time
from typing import Optional

import paramiko

from .sftp_log import log


class _KeepAlive:
    """下载期间周期性发送 SSH 忽略包，避免空闲连接被中间设备掐断"""

    def __init__(self, transport, interval: int):
        self._transport = transport
        self._interval = max(int(interval), 0)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def __enter__(self):
        if self._interval <= 0:
            return self
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def _loop(self):
        while not self._stop.wait(self._interval):
            try:
                self._transport.send_ignore()
            except Exception:
                return

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)


def silent_remove(path: str):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def connect(cfg: dict):
    """建立 SSH + SFTP 连接；调用方负责在 finally 里 close"""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs = {}
    if _connect_supports_auth_timeout():
        kwargs['banner_timeout'] = int(cfg['SSH_CONNECT_TIMEOUT'])
        kwargs['auth_timeout'] = int(cfg['SSH_CONNECT_TIMEOUT'])
    ssh.connect(cfg['SFTP_HOST'], int(cfg['SFTP_PORT']),
                cfg['SFTP_USERNAME'], cfg['SFTP_PASSWORD'],
                timeout=int(cfg['SSH_CONNECT_TIMEOUT']), **kwargs)
    return ssh, ssh.open_sftp()


def close(ssh, sftp):
    if sftp is not None:
        try:
            sftp.close()
        except Exception:
            pass
    if ssh is not None:
        try:
            ssh.close()
        except Exception:
            pass


def remote_file_exists(sftp, remote_path: str) -> bool:
    try:
        sftp.stat(remote_path)
        return True
    except FileNotFoundError:
        return False
    except Exception as e:
        log.error(f"获取远程文件信息失败: {os.path.basename(remote_path)}: {e}", exc=e)
        return True  # 不确定时按"存在"处理，交给下载逻辑报错，避免误判成"远程没有"


def _download_once(sftp, remote_path: str, local_path: str, cfg: dict) -> bool:
    tmp_path = f"{local_path}.part"
    try:
        remote_size = int(sftp.stat(remote_path).st_size)
    except FileNotFoundError:
        log.info(f"远程文件不存在，跳过: {os.path.basename(remote_path)}")
        return False

    started_at = time.time()
    channel = sftp.get_channel()
    old_timeout = channel.gettimeout()
    channel.settimeout(int(cfg['DOWNLOAD_CHANNEL_TIMEOUT']))
    try:
        with _KeepAlive(channel.get_transport(), int(cfg['DOWNLOAD_KEEPALIVE_INTERVAL'])):
            sftp.get(remote_path, tmp_path)
    finally:
        channel.settimeout(old_timeout)

    local_size = os.path.getsize(tmp_path)
    if remote_size >= 0 and local_size != remote_size:
        log.error(f"下载大小不一致: {os.path.basename(remote_path)} "
                  f"远程 {remote_size} 字节 / 本地 {local_size} 字节，已丢弃不完整文件")
        silent_remove(tmp_path)
        return False

    if os.path.exists(local_path):
        os.remove(local_path)
    os.replace(tmp_path, local_path)
    log.info(f"下载完成 {os.path.basename(local_path)} ({local_size} 字节, "
             f"耗时 {time.time() - started_at:.1f}s)")
    return True


def download_file(sftp, remote_path: str, local_path: str, cfg: dict) -> bool:
    """
    可靠下载（带重试）。返回 True 表示本地已有可用文件。
    区分「远程不存在」与「下载失败」：前者不重试直接返回 False。
    """
    retries = max(int(cfg.get('DOWNLOAD_RETRY_TIMES', 1)), 1)
    base = float(cfg.get('DOWNLOAD_RETRY_BACKOFF_BASE', 5))
    for attempt in range(retries):
        if not remote_file_exists(sftp, remote_path):
            return False
        try:
            return _download_once(sftp, remote_path, local_path, cfg)
        except (socket.timeout, TimeoutError) as e:  # paramiko 各版本超时异常类型不同
            _on_download_error(e, attempt, retries, base, local_path, "超时")
        except Exception as e:
            _on_download_error(e, attempt, retries, base, local_path, "失败")
    return False


def _on_download_error(exc, attempt: int, retries: int, base: float, local_path: str, what: str):
    silent_remove(f"{local_path}.part")
    if attempt >= retries - 1:
        log.error(f"下载{what}，已重试 {retries} 次仍失败: {os.path.basename(local_path)}: {exc}",
                  exc=exc)
        return
    sleep_for = base * (2 ** attempt)
    log.noisy(f"下载{what}，{sleep_for:.0f}s 后第 {attempt + 1}/{retries - 1} 次重试 "
              f"({os.path.basename(local_path)}): {exc}")
    time.sleep(sleep_for)


_CONNECT_KWARGS_CACHE = None


def _connect_supports_auth_timeout() -> bool:
    """
    banner_timeout 自 paramiko 1.15 支持，auth_timeout 自 2.2 才有；
    老环境（1.x）传 auth_timeout 会直接 TypeError，这里探测一次并缓存。
    """
    global _CONNECT_KWARGS_CACHE
    if _CONNECT_KWARGS_CACHE is None:
        import inspect
        try:
            params = inspect.signature(paramiko.SSHClient.connect).parameters
            _CONNECT_KWARGS_CACHE = 'auth_timeout' in params
        except Exception:
            _CONNECT_KWARGS_CACHE = False
        if not _CONNECT_KWARGS_CACHE:
            log.noisy("当前 paramiko 版本不支持 auth_timeout，仅使用 timeout/banner_timeout")
    return _CONNECT_KWARGS_CACHE
