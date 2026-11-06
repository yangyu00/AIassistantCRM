import asyncio
import os
from contextlib import asynccontextmanager
from typing import List

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel, Field, field_validator

from IOP.iop_client import get_custom_info
from config import config
from services.product_info_raw import ProductInfoRaw as ProductInfoRaw
from services.rewrite_by_excel.rewrite import ProductInfoRewrite as ProductInfoByExcel
from services.product_registry import resolve as resolve_product_spec
from services.product_registry import is_registered as is_product_registered

from sftp_jobs import start_all
from sftp_jobs.queries import get_latest_record

from src import dim_table
from src.exceptions import telnumError, productIdError, register_errors_handlers
from src.log_util import my_log

excel_mtimes: dict = {}
sftp_threads: list = []
refresh_task = None


def _mtime(name: str):
    p = dim_table.MAIN_PATH / 'excel2consts' / name
    try:
        return p.stat().st_mtime
    except OSError:
        return None


async def _run_compiler(script: str) -> tuple:
    """跑一个维表编译脚本。脚本只写自己的分片，不碰 src/consts.py。"""
    path = dim_table.MAIN_PATH / script
    proc = await asyncio.create_subprocess_exec(
        os.sys.executable, str(path), cwd=str(dim_table.MAIN_PATH),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    text = (out.decode(errors='ignore') + err.decode(errors='ignore')).strip()
    return script, proc.returncode == 0, text[:1000]


async def _refresh_dim_tables(force: bool = False) -> dict:
    """维表 Excel 有变化就重新编译，全部成功后合并落盘并热加载。

    这是原实现的 bug 所在：卖点维表与标签转换维表都往 src/consts.py 写，
    后跑的脚本先删再写，把先跑那个的字典整段冲掉（现象：只剩
    label_name_mapping，product_selling_point_match_dict 消失，所有产品掉进
    ProductInfoRaw，返回 code=2「无产品名称」）。现在各脚本只产出自己的分片，
    全部编译成功再由 dim_table 合并成一份原子替换；任何一张失败都整体保留旧版。
    """
    now = {n: _mtime(n) for n in dim_table.DIM_TABLES}
    missing = [n for n, m in now.items() if m is None]
    if missing:
        my_log.warning(f'维表文件缺失，本轮跳过: {missing}')

    changed = [n for n, m in now.items() if m is not None and (force or m != excel_mtimes.get(n))]
    if not changed:
        return {'changed': [], **dim_table.stat()}

    my_log.info(f'检测到维表变更: {changed}，开始编译')
    results = await asyncio.gather(*(_run_compiler(dim_table.DIM_TABLES[n]) for n in changed))
    for script, ok, text in results:
        if not ok:
            my_log.error(f'{script} 编译失败，本轮维表整体保持上一版本: {text}')
            return {'changed': changed, 'error': f'{script} 编译失败', 'detail': text, **dim_table.stat()}
        if text:
            my_log.info(f'{script}: {text}')

    ok, err = dim_table.merge_part_files()
    if not ok:
        my_log.error(f'维表合并未通过，继续使用上一版本: {err}')
        return {'changed': changed, 'error': err, **dim_table.stat()}

    # 落盘成功才记 mtime，失败下一轮自动重试
    for n in changed:
        excel_mtimes[n] = _mtime(n)
    return {'changed': changed, **dim_table.stat()}


async def _dim_table_loop():
    """启动先与磁盘对齐一次，之后每 DIM_TABLE_CHECK_SECONDS 秒检查一次。"""
    while True:
        try:
            await _refresh_dim_tables(force=True)
        except Exception as exc:
            my_log.error(f'维表刷新任务异常: {exc}')
        try:
            await asyncio.sleep(DIM_TABLE_CHECK_SECONDS)
        except asyncio.CancelledError:
            raise

@asynccontextmanager
async def lifespan(application: FastAPI):
    global refresh_task, sftp_threads

    # 1) 先加载磁盘上已有的维表，保证第一个请求进来时字典不是空的
    dim_table.load_current()

    # 2) 维表热更新循环：启动即把 Excel 编译结果与磁盘对齐一次
    refresh_task = asyncio.create_task(_dim_table_loop(), name='dim-table-refresh')

    # 3) IOP 共享连接池（原来藏在请求里的懒启动提到这里）
    from IOP.iop_client import startup as iop_startup, shutdown as iop_shutdown
    await iop_startup()

    # 4) SFTP 入库子进程（保持原有"启动即拉起"的行为，线程句柄留着以便退出时收拢）
    sftp_threads = start_all()

    my_log.info(f'服务启动完成，维表: {dim_table.stat()}')
    yield

    # ---- shutdown ----
    if refresh_task is not None:
        refresh_task.cancel()
        try:
            await refresh_task
        except asyncio.CancelledError:
            pass
    # 只清理超过 1 小时的残留分片（正常一次编译+合并是秒级的，1 小时前的遗留才算异常退出残留）。
    import time as _time
    _stale_before = _time.time() - 3600
    for f in dim_table.MAIN_PATH.joinpath('src').glob(dim_table.PART_PREFIX + '*.py'):
        try:
            if f.stat().st_mtime < _stale_before:
                f.unlink()
                my_log.info(f'已清理残留维表分片: {f.name}')
        except OSError:
            pass
    for t in sftp_threads:
        if t.is_alive():
            my_log.info(f'等待 SFTP 子进程结束: {t.name}')
            t.join(timeout=SFTP_STOP_TIMEOUT)
    await iop_shutdown()
    my_log.info('服务已停止')


app = FastAPI(lifespan=lifespan)
register_errors_handlers(app)
global_config = config

MAIN_PATH = dim_table.MAIN_PATH
DIM_TABLE_CHECK_SECONDS = int(global_config['dim_table']['check_seconds'])
SFTP_STOP_TIMEOUT = int(global_config['sftp_stop_timeout'])


class ProductIds(BaseModel):
    id: str = Field("", description="产品ID", example="******")

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str):
        if not value:
            raise productIdError(value)
        return value


class Args(BaseModel):
    sceneId: str = Field("", description="场景ID", example="******")
    telnum: str = Field("", description="手机号", example="15020868271")
    productId: List[ProductIds] = Field([], description="产品ID列表",
                                        example=[{"id": '******'},
                                                 {"id": '******'}])

    @field_validator("telnum")
    @classmethod
    def validate_telnum(cls, value: str):
        if not value or not value.isdigit() or len(value) != 11:
            raise telnumError(value)
        return value

    @field_validator("productId")
    @classmethod
    def validate_productId(cls, value: List[ProductIds]):
        if not value:
            raise productIdError(value)
        return value


@app.post(str(global_config['marketAssistant']['url']))
async def marketAssistant(args: Args):
    # 1. 根据 手机号 获取 用户标签
    try:
        custom_label_info = await get_custom_info(args.telnum)
    except Exception as e:
        my_log.error(e)
        custom_label_info = {}

    # 2. 建立产品对象列表
    product_obj_list = []
    for one_product_obj in args.productId:
        # 解析优先级（维表是最后一道兜底）：
        #   1) 明确注册的产品     → 走注册表里的具名 job
        #   2) 未注册但有兜底 job → 先查兜底库，**查到记录才用**，查不到继续往下
        #   3) 卖点维表里有该产品 → 走维表话术（ProductInfoByExcel）
        #   4) 都没有             → ProductInfoRaw（product_name 为 None → code=2/3）
        job_id, spec_cls = resolve_product_spec(one_product_obj.id)
        if job_id is not None:
            record = await get_latest_record(job_id, args.telnum, one_product_obj.id)
            if is_product_registered(one_product_obj.id) or record:
                product_obj_list.append(spec_cls(one_product_obj.id, custom_label_info, record))
                continue
            my_log.info(f'产品 {one_product_obj.id} 在兜底业务 {job_id} 中无记录，改走卖点维表')

        # 未进入任何业务库（或兜底库无记录）→ 查卖点维表（dim_table 快照，热更新即时生效）
        if dim_table.has_selling_point(one_product_obj.id):
            product_obj_list.append(ProductInfoByExcel(one_product_obj.id, custom_label_info))
            continue

        product_obj_list.append(ProductInfoRaw(one_product_obj.id, custom_label_info))

    # 3. 输出话术
    allScripts = []
    for product_obj in product_obj_list:
        my_log.info(product_obj)
        if product_obj.product_name is not None:
            allScripts.append(
                {"productId": product_obj.product_id, "productName": product_obj.product_name,
                 "script": product_obj.output_script, "code": "0"})
        else:
            # 细分：
            #   code=2 维表里有该产品，但产品名为空
            #   code=3 任何业务库和维表中都不存在该产品ID（疑似ID错误/未配置）
            unknown = not dim_table.has_selling_point(product_obj.product_id)
            my_log.error(f'{"产品ID未配置: " if unknown else "无产品名称: "}{product_obj.product_id}')
            my_log.error(product_obj)
            allScripts.append(
                {"productId": product_obj.product_id, "productName": "", "script": "",
                 "code": "3" if unknown else "2"})

    result = {"allScripts": allScripts}
    return {"res_code": 0, "res_desc": "", "result": result}


@app.get("/api/dimTable/status")
async def dim_table_status():
    """维表自检：看当前生效版本、各表条目数、文件时间，排查"维表改了没生效"用。"""
    return {"res_code": 0, "res_desc": "", "result": {
        "dim_table": dim_table.stat(),
        "files": dim_table.list_dim_tables(),
        "refresh_seconds": DIM_TABLE_CHECK_SECONDS,
    }}


@app.post("/api/dimTable/reload")
async def dim_table_reload():
    """手动触发一次维表重新编译+加载，不必等定时任务或重启进程。"""
    result = await _refresh_dim_tables(force=True)
    return {"res_code": 0 if 'error' not in result else 1, "res_desc": "", "result": result}


if __name__ == "__main__":
    uvicorn.run(app="main:app", **global_config['fastapi'].as_dict())
