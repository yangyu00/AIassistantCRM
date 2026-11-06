# -*- coding: utf-8 -*-
"""维表 -> consts.py 中各常量的名称与约定（单一事实来源）。"""


class ConstSpec:
    def __init__(self, name, description):
        self.name = name
        self.description = description


PRODUCT_SELLING_POINT_MATCH = ConstSpec('product_selling_point_match_dict', '{业务ID: (产品名称, 卖点, 标签ID)}')
CUT_IN_SCRIPT = ConstSpec('cut_in_script_dict', '{业务ID: {"兜底话术": ..., 标签组ID: ...}}')
LABEL_NAME_MAPPING = ConstSpec('label_name_mapping', '{标签ID: 营销助手显示名}')

# 维表中原有的列，保留以便后续按列引用，避免 magic string
LEGACY_COLUMNS = ('业务ID', '产品名称', '生成话术', '标签ID', '兜底话术', '匹配结果2')


def cut_in_column_keys(df):
    """维表里 标签0/标签1/... 的列号，供对账与排查使用。"""
    return [c for c in df.columns if isinstance(c, str) and c.startswith('标签')]
