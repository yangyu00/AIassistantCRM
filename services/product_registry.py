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
# 注意：当前配置中 F09609 的 ids 为空集合，因此 resolve 对未注册产品也会返回它
FALLBACK_JOB = 'F09609'

# 产品ID → (job_id, 话术类) 的映射表
# 只收集“明确注册过产品ID”的映射
PRODUCT_SPECS = {
    pid: (job_id, v['cls'])
    for job_id, v in SPECIAL_PRODUCTS.items()
    for pid in (v['ids'] or ())
}


def resolve(product_id: str):
    """根据产品ID解析 (job_id, 话术类)，未匹配返回 (None, None)。

    [FIX] 匹配顺序：
        1) 精确注册的产品ID      → 对应的具名 job
        2) 未注册 + 有兜底 job   → (FALLBACK_JOB, cls)
        3) 未注册 + 无兜底 job   → (None, None)
    兜底 job 只是第一顺位尝试；它**查不到记录**时 main.py 会继续落到
    卖点维表，维表是最后一道兜底。
    """
    hit = PRODUCT_SPECS.get(product_id)
    if hit is not None:
        return hit

    fallback = SPECIAL_PRODUCTS.get(FALLBACK_JOB)
    if fallback is not None and not fallback.get('ids'):
        return FALLBACK_JOB, fallback['cls']

    return None, None


def is_registered(product_id: str) -> bool:
    """[FIX] 产品ID是否「明确登记」在 SPECIAL_PRODUCTS 中
    （包括被兜底 job 点名登记的），区别于「被空 ids 兜底 job 无条件接住」。"""
    if product_id in PRODUCT_SPECS:
        return True
    return any(product_id in (v.get('ids') or ()) for v in SPECIAL_PRODUCTS.values())
