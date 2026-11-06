# -*- coding: utf-8 -*-
"""
共用主流程：解析参数 → 下载 → 解析入库 → 过期清理 → 退出码
  - 本地文件已存在且不强制重载：下载和入库一起跳过（SKIP_IMPORT_IF_EXISTS）
  - 远程文件不存在 / 下载失败：不当作程序出错（退出码 0），仍然执行过期清理
    （TREAT_DOWNLOAD_FAILURE_AS_FATAL=True 可切回「失败即退出、不清理」的严格模式）
  - 清理时：先关 SFTP 连接，再在 finally 里清理本地文件
"""
import argparse
import gc
import os
import sys
import time
from datetime import datetime

from . import sftp_client
from .sftp_log import log
from .db import build_table_name_list, init_schema, open_db, cleanup_expired_data, cleanup_local_expired_files
from .parsers import get_parser

try:
    _HAS_EXTERNAL_LOGGER = True
except Exception:
    _HAS_EXTERNAL_LOGGER = False


def setup_logging(cfg: dict):
    log_dir = os.path.join(cfg['BASE_DIR'], cfg['LOG_DIR_NAME']) if cfg['LOG_DIR_NAME'] else ""
    log.setup(log_dir, echo_console=cfg['LOG_ECHO_CONSOLE'], max_bytes=cfg['LOG_MAX_BYTES'],
              info_file=cfg['LOG_FILE_INFO'], error_file=cfg['LOG_FILE_ERROR'])


def parse_args():
    parser = argparse.ArgumentParser(description="SFTP 文件下载与入库工具")
    parser.add_argument('--date', type=str,
                        help='指定处理日期 (YYYYMMDD 或 YYYY-MM-DD)，不传则默认当天')
    parser.add_argument('--force-reload', action='store_true',
                        help='本地文件存在时不重新下载，但强制重新入库')
    parser.add_argument('--no-download', action='store_true',
                        help='不连 SFTP，只入库已存在的本地文件并清理（调试/补跑用）')
    parser.add_argument('--no-cleanup', action='store_true', help='不执行过期清理')
    return parser.parse_args()


def resolve_run_args(args, cfg: dict):
    """
    清理口径：不传 --date（跑当天）默认执行清理；传了 --date（补历史）默认不清理，
    避免误删；两者都可用 --no-cleanup 显式关闭。
    """
    if args.date:
        try:
            target_date = (datetime.strptime(args.date, '%Y-%m-%d') if '-' in args.date
                           else datetime.strptime(args.date, '%Y%m%d'))
        except ValueError:
            log.error(f"日期格式错误，请使用 YYYYMMDD 格式: {args.date}")
            sys.exit(1)
        perform_cleanup = not args.no_cleanup
    else:
        target_date = datetime.now()
        perform_cleanup = not args.no_cleanup
    return target_date, perform_cleanup, args.no_download


def run_process(cfg: dict, target_date: datetime, perform_cleanup: bool,
                force_reload: bool, no_download: bool = False) -> dict:
    os.makedirs(cfg['LOCAL_DIR'], exist_ok=True)
    os.makedirs(cfg['DB_DIR'], exist_ok=True)

    date_str = target_date.strftime('%Y%m%d')
    log.info(f"===== 开始处理日期: {date_str} =====")
    log.info(f"运行模式: 清理={'开' if perform_cleanup else '关'}, "
             f"强制重载={'是' if force_reload else '否'}")

    remote_filename = cfg['REMOTE_FILE_TEMPLATE'].format(prefix=cfg['FILE_PREFIXES'], date=date_str)
    remote_path = f"{cfg['SFTP_REMOTE_PATH']}/{remote_filename}"
    local_path = os.path.join(cfg['LOCAL_DIR'], remote_filename)
    table_name_list = build_table_name_list(cfg)

    stat = {'date': date_str, 'downloaded': False, 'parsed': 0, 'written': 0,
            'bad': 0, 'rescued': 0, 'skipped': False, 'failed': False}

    file_exists = os.path.exists(local_path)
    # 本地文件已存在且未强制重载：按 f09609 口径，下载和入库都跳过（上面已提前返回）
    if file_exists and not force_reload and cfg.get('SKIP_IMPORT_IF_EXISTS', True):
        # f09609 口径：下载和入库都跳过；过期清理照常执行
        log.info(f"本地文件已存在，跳过下载与入库: {remote_filename}")
        stat['skipped'] = True
        if perform_cleanup:
            _do_cleanup(cfg, table_name_list)
        else:
            log.info("跳过数据库清理 (非默认模式)")
            log.info("跳过本地文件清理 (非默认模式)")
        return stat
    elif no_download and not file_exists:
        log.error(f"离线模式且本地文件不存在，无事可做: {remote_filename}")
        stat['failed'] = True
        if perform_cleanup:
            _do_cleanup(cfg, table_name_list)
        return stat

    ssh = sftp = None
    try:
        if not no_download:
            if file_exists and force_reload:
                log.info(f"强制重载：本地文件已存在，仍重新下载覆盖: {remote_filename}")
            ssh, sftp = sftp_client.connect(cfg)

        # 连接成功后（或离线模式下直接）先确保库结构存在：即使远程文件不存在，空库结构也完整
        _ensure_schema(cfg, table_name_list)

        if not no_download:
            ok = sftp_client.download_file(sftp, remote_path, local_path, cfg)
            if not ok:
                # f09609 口径：远程不存在 / 下载失败都不算程序出错，清理照常执行
                stat['download_failed'] = True
                if cfg.get('TREAT_DOWNLOAD_FAILURE_AS_FATAL'):
                    stat['failed'] = True
                if perform_cleanup:
                    _do_cleanup(cfg, table_name_list)
                return stat
            stat['downloaded'] = True
        else:
            log.info(f"离线模式：不下载，直接入库本地文件: {remote_filename}")

        # 网络操作结束后再入库，避免解析耗时占着 SFTP 连接
        parser = get_parser(cfg, table_name_list)
        r = parser.parse_file(local_path, cfg['DB_PATH'])
        stat['parsed'] = r['parsed']
        stat['written'] = r['written']
        stat['bad'] = r['bad']
        stat['rescued'] = r['rescued']
        stat['failed'] = r.get('failed', False)

        if perform_cleanup:
            cleanup_expired_data(cfg, table_name_list)
        elif not stat['failed']:
            log.info("跳过数据库清理 (非默认模式)")
        if stat.get('download_failed'):
            log.warning("===== 本次未取到当天文件，请检查远程文件是否已生成 / 网络是否可用 =====")

    except Exception as e:
        stat['failed'] = True
        log.error(f"SFTP 处理失败: {e}", exc=e)
    finally:
        # 先关 SFTP/SSH，再清理本地文件，避免连接异常时把刚下好的文件删掉
        sftp_client.close(ssh, sftp)
        # 正常 / 失败 / 未取到文件 三种情况都清本地过期文件（与原两版一致），仅 --no-cleanup 时跳过
        if perform_cleanup:
            cleanup_local_expired_files(cfg)
        else:
            log.info("跳过本地文件清理 (非默认模式)")
        gc.collect()
    return stat


def _ensure_schema(cfg: dict, table_name_list):
    conn = open_db(cfg['DB_PATH'], cfg)
    try:
        init_schema(conn, cfg, table_name_list)
        # 业务钩子: map 模式补建老单表 data_F09554/55/56, 否则写入落不进查询表
        if cfg.get('router') == 'map':
            from sftp_jobs.table_router import ensure_legacy_tables
            ensure_legacy_tables(conn, cfg)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _do_cleanup(cfg: dict, table_name_list):
    cleanup_expired_data(cfg, table_name_list)
    cleanup_local_expired_files(cfg)


def main(cfg: dict):
    setup_logging(cfg)
    args = parse_args()
    target_date, perform_cleanup, no_download = resolve_run_args(args, cfg)

    log.info(f"进程启动 PID={os.getpid()} 参数={' '.join(sys.argv[1:]) or '(无)'}")

    run_started_at = time.time()
    exit_code = 0
    try:
        stat = run_process(cfg, target_date, perform_cleanup, args.force_reload, no_download)
        exit_code = 1 if stat.get('failed') else 0
        log.info(f"本次运行结束：日期 {stat.get('date')}，"
                 f"下载={'是' if stat.get('downloaded') else '否'}，"
                 f"正常处理 {stat.get('parsed', 0)} 条，落盘 {stat.get('written', 0)} 条，"
                 f"跳过异常行 {stat.get('bad', 0)} 条"
                 + (f"，抢救落盘 {stat['rescued']} 条" if stat.get('rescued') else "")
                 + ("，未取到当天文件" if stat.get('download_failed') else "")
                 + (f"，失败={'是' if stat.get('failed') else '否'}"
                    if not stat.get('skipped') else "，本地文件已存在跳过"))
    except SystemExit:
        raise
    except BaseException as e:
        log.error(f"进程异常终止（未捕获异常）: {type(e).__name__}", exc=e)
        exit_code = 1
    finally:
        log.info(f"进程退出，退出码 {exit_code}，总耗时 {time.time() - run_started_at:.1f}s")
        sys.exit(exit_code)
