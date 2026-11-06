import datetime
import importlib
import os
from contextlib import asynccontextmanager
import subprocess
import threading
from pathlib import Path
from typing import List

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from starlette.status import HTTP_401_UNAUTHORIZED

from IOP.iop_client import get_custom_info, startup as iop_startup, shutdown as iop_shutdown
from config import ImmutableYamlConfig
from services.product_info_raw import ProductInfoRaw as ProductInfoRaw
from services.rewrite_by_excel.rewrite import ProductInfoRewrite as ProductInfoByExcel
from services.rewrite_by_spec_f09554.rewrite import ProductInfoRewrite as ProductInfoBySpec9554
from services.rewrite_by_spec_f09591.rewrite import ProductInfoRewrite as ProductInfoBySpec9591
from services.rewrite_by_spec_f09609.rewrite import ProductInfoRewrite as ProductInfoBySpec9609

from sftp_jobs import run_job  # noqa: F401  (run_one 导入时安装 map 路由)
from sftp_jobs import queries as data_query_f09554
from sftp_jobs import queries as data_query_f09591
from sftp_jobs import queries as data_query_f09609
from sftp_jobs.config import JOBS

from src import consts
from src.call_stat import record_call
from src.security import mask_sensitive
from src.exceptions import telnumError, productIdError, register_errors_handlers
from src.log_util import my_log
from src.task_scheduler import repeat_task

@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动初始化 IOP 共享连接池，关闭时释放。"""
    await iop_startup()
    try:
        yield
    finally:
        await iop_shutdown()


app = FastAPI(lifespan=lifespan)
register_errors_handlers(app)
global_config = ImmutableYamlConfig()


def _api_tokens():
    raw = global_config.get('marketAssistant', {}).get('api_tokens', '') or ''
    return [t.strip() for t in raw.split(',') if t.strip()]


@app.middleware("http")
async def count_call_middleware(request, call_next):
    """调用量 +1（成功失败都计）；计数放线程池，避免 SQLite 写锁阻塞事件循环。
    api_tokens 非空时校验 Authorization: Bearer <token>。"""
    if request.url.path == str(str(global_config['marketAssistant']['url'])):
        tokens = _api_tokens()
        if tokens and request.headers.get('Authorization', '') not in {f'Bearer {t}' for t in tokens}:
            return JSONResponse({"res_code": 401, "res_desc": "未授权", "result": ""},
                                status_code=HTTP_401_UNAUTHORIZED)
        await run_in_threadpool(record_call)
    return await call_next(request)


excel_update_time = []
first_launch = True
sftp_running = None  # 当前在跑的 SFTP 轮次线程

MAIN_PATH = Path(__file__).resolve().parent


def _start_all_thread():
    """单个后台线程串行跑完所有业务：上一轮没跑完就跳过，不再每轮叠加线程。"""
    def _run_all():
        for job_id in list(JOBS):
            try:
                rc = run_job(job_id)
                if rc != 0:
                    my_log.error(f'SFTP 任务 {job_id} 退出码 {rc}，请检查')
            except Exception as e:
                my_log.error(f'SFTP 任务 {job_id} 异常: {e}')

    th = threading.Thread(target=_run_all, name='sftp-all', daemon=True)
    th.start()
    return th


@repeat_task(seconds=60 * 60)
def repeat_update_consts() -> None:
    # 任务1.定时检查卖点维表和标签转换维表是否更新，更新则重写src中的consts文件，
    #        并利用importlib.reload(consts)热更新（业务代码必须通过 consts.xxx 读取才生效）
    global excel_update_time, first_launch, sftp_running
    heart_file = {
        f'{MAIN_PATH}/excel2consts/卖点维表.xlsx': f'{MAIN_PATH}/excel2consts/product_selling_point_match.py',
        f'{MAIN_PATH}/excel2consts/标签转换维表.xlsx': f'{MAIN_PATH}/excel2consts/labelName1_to_labelName2.py',
    }

    for i, (one_file_path, convert_script) in enumerate(heart_file.items()):
        try:
            mtime = datetime.datetime.fromtimestamp(os.path.getmtime(one_file_path))
        except OSError as e:
            my_log.error(f'维表读取失败 {os.path.basename(one_file_path)}: {e}')
            continue

        if len(excel_update_time) < len(heart_file):
            excel_update_time.append(mtime)

        if mtime != excel_update_time[i] or first_launch:
            if len(excel_update_time) == len(heart_file):
                first_launch = False
            my_log.info(f'{os.path.basename(one_file_path)} 触发更新任务')
            result = subprocess.run([sys.executable, convert_script], shell=False,
                                    capture_output=True, text=True, check=False)
            if result.returncode != 0:
                # 转换失败：保留旧维表并报错，不再静默继续
                my_log.error(f'维表转换失败 rc={result.returncode}: {convert_script} '
                             f'{mask_sensitive(result.stderr[-500:])}')
                continue
            importlib.reload(consts)
            excel_update_time[i] = mtime

    # 任务2.定时异步运行 sftp_service 的任务函数
    if sftp_running is not None and not sftp_running.is_alive():
        sftp_running = None
    if sftp_running is None:
        sftp_running = _start_all_thread()
    else:
        my_log.info('上一轮 SFTP 任务仍在运行，本轮跳过')


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


@app.post(str(str(global_config['marketAssistant']['url'])))
async def marketAssistant(args: Args):
    # 1. 根据 手机号 获取 用户标签
    try:
        custom_label_info = await get_custom_info(args.telnum)
    except Exception as e:
        my_log.error(f'标签查询失败: {mask_sensitive(str(e))}')
        custom_label_info = {}

    # 2. 建立产品对象列表
    product_obj_list = []
    for one_product_obj in args.productId:
        # 1. 特殊业务
        # 号卡业务、套餐业务、宽带业务
        if one_product_obj.id in ["202608041615335173", "202608041615335172", "202608041615335174"]:
            record = await data_query_f09554.get_latest_record(args.telnum, 'F09554')
            product_obj_list.append(ProductInfoBySpec9554(one_product_obj.id, custom_label_info, record))
            continue

        # 2. 数据库中业务(控损专用)
        if one_product_obj.id in ["202609180955095402"]:
            record = await data_query_f09591.get_latest_record(args.telnum, 'F09591')
            if record:
                product_obj_list.append(ProductInfoBySpec9591(one_product_obj.id, custom_label_info, record))
                continue

        # 数据库中业务(客户诊断)
        record = await data_query_f09609.get_latest_record(args.telnum, 'F09609')
        if record:
            product_obj_list.append(ProductInfoBySpec9609(one_product_obj.id, custom_label_info, record))
            continue

        # 4. 维表中业务
        if one_product_obj.id in consts.product_selling_point_match_dict:
            product_obj_list.append(ProductInfoByExcel(one_product_obj.id, custom_label_info))
            continue

        product_obj_list.append(ProductInfoRaw(one_product_obj.id, custom_label_info))

    # 3. 输出话术
    allScripts = []
    for product_obj in product_obj_list:
        my_log.info(mask_sensitive(str(product_obj)))
        if product_obj.product_name is not None:
            allScripts.append(
                {"productId": product_obj.product_id, "productName": product_obj.product_name,
                 "script": product_obj.output_script, "code": "0"})
        else:
            my_log.error("无产品名称")
            my_log.error(mask_sensitive(str(product_obj)))
            allScripts.append(
                {"productId": product_obj.product_id, "productName": "", "script": "", "code": "2"})

    result = {"allScripts": allScripts}
    return {"res_code": 0, "res_desc": "", "result": result}


if __name__ == "__main__":
    uvicorn.run(app="main:app", **global_config['fastapi'])
