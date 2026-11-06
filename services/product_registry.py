# -*- coding: utf-8 -*-

from services.rewrite_by_spec_f09554.rewrite import ProductInfoRewrite as ProductInfoBySpec9554
from services.rewrite_by_spec_f09591.rewrite import ProductInfoRewrite as ProductInfoBySpec9591
from services.rewrite_by_spec_f09609.rewrite import ProductInfoRewrite as ProductInfoBySpec9609

SPECIAL_PRODUCTS = {
    'F09554': {'ids': {'202608041615335173'}, 'cls': ProductInfoBySpec9554},   # 号卡
    'F09555': {'ids': {'202608041615335172'}, 'cls': ProductInfoBySpec9554},   # 套餐
    'F09556': {'ids': {'202608041615335174'}, 'cls': ProductInfoBySpec9554},   # 宽带
    'F09591': {'ids': {'202609180955095402'}, 'cls': ProductInfoBySpec9591},   # 控损专用
    'F09609': {'ids': set(), 'cls': ProductInfoBySpec9609},                    # 客户诊断（按ID兜底）
}

# 兜底查询的库：原逻辑 record = await data_query_f09609.get_record(telnum, product_id)
FALLBACK_JOB = 'F09609'

# 产品ID → (job_id, 话术类)
PRODUCT_SPECS = {
    pid: (job_id, v['cls'])
    for job_id, v in SPECIAL_PRODUCTS.items()
    for pid in (v['ids'] or ())
}


def resolve(product_id: str):
    """产品ID → (job_id, 话术类)；未注册返回 (None, None)"""
    hit = PRODUCT_SPECS.get(product_id)
    if hit is not None:
        return hit
    ids = SPECIAL_PRODUCTS.get(FALLBACK_JOB, {}).get('ids', set())
    if not ids:  # 兜底库按库内任意ID匹配
        return FALLBACK_JOB, SPECIAL_PRODUCTS[FALLBACK_JOB]['cls']
    return None, None
