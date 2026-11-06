# -*- coding: utf-8 -*-

from services.rewrite_by_spec_f09554.rewrite import ProductInfoRewrite as ProductInfoBySpec9554
from services.rewrite_by_spec_f09591.rewrite import ProductInfoRewrite as ProductInfoBySpec9591
from services.rewrite_by_spec_f09609.rewrite import ProductInfoRewrite as ProductInfoBySpec9609

# 特殊产品配置
# key   : job_id
# value : {
#     'ids': 明确绑定的产品ID集合，
#            为空集合表示该 job 作为“兜底 job”，匹配任意未注册产品ID
#     'cls': 对应的话术处理类
# }

SPECIAL_PRODUCTS = {
    'F09554': {'ids': {'202608041615335173'}, 'cls': ProductInfoBySpec9554},  # 号卡
    'F09555': {'ids': {'202608041615335172'}, 'cls': ProductInfoBySpec9554},  # 套餐
    'F09556': {'ids': {'202608041615335174'}, 'cls': ProductInfoBySpec9554},  # 宽带
    'F09591': {'ids': {'202609180955095402'}, 'cls': ProductInfoBySpec9591},  # 控损专用
    'F09609': {'ids': set(), 'cls': ProductInfoBySpec9609},  # 客户诊断（兜底 job：匹配任意未注册产品ID）
}

# 兜底 job_id
# 注意：当前配置中 F09609 的 ids 为空集合，因此它会匹配所有未显式注册的产品ID
FALLBACK_JOB = 'F09609'

# 产品ID → (job_id, 话术类) 的映射表
# 只收集“明确注册过产品ID”的映射
PRODUCT_SPECS = {
    pid: (job_id, v['cls'])
    for job_id, v in SPECIAL_PRODUCTS.items()
    for pid in (v['ids'] or ())
}


def resolve(product_id: str):
    """
    根据产品ID解析对应的 (job_id, 话术类)

    ⚠️ 重要说明：
    在当前配置下，由于 FALLBACK_JOB='F09609' 且它的 ids 为空集合，
    本函数 **永远不会返回 (None, None)**。

    实际行为：
    - 命中 PRODUCT_SPECS：返回精确匹配的 (job_id, cls)
    - 未命中：直接返回兜底 job (FALLBACK_JOB, 兜底 cls)

    因此下面的 `return None, None` 是 **防御性代码 / 不可达分支**，
    仅用于未来配置调整或逻辑变更时兜底，不代表当前有“未匹配”语义。
    """

    hit = PRODUCT_SPECS.get(product_id)
    if hit is not None:
        return hit

    ids = SPECIAL_PRODUCTS.get(FALLBACK_JOB, {}).get('ids', set())

    if not ids:
        return FALLBACK_JOB, SPECIAL_PRODUCTS[FALLBACK_JOB]['cls']

    return None, None
