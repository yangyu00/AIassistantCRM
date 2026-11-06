# -*- coding: utf-8 -*-
"""标签转换维表 → consts 分片（label_name_mapping）

维表列：标签ID | 营销助手显示名字
只写自己的分片文件，不覆盖卖点维表的字典。
"""
import sys
from collections import Counter
from pathlib import Path

import pandas as pd
from yapf.yapflib.yapf_api import FormatCode

sys.path.append(str(Path(__file__).resolve().parent.parent))

from excel2consts import basic_ops as _ops  # noqa: E402


def find_duplicates(lst):
    counter = Counter(lst)
    return [k for k, v in counter.items() if v > 1]


def rewrite_label_name_mapping(excel_path, node_dict_name, file_path):
    df = pd.read_excel(excel_path, dtype=str, keep_default_na=False)

    keys = [str(k).strip() for k in df['标签ID']]
    values = [str(v).strip() for v in df['营销助手显示名字']]

    dup = find_duplicates([k for k in keys if k])
    if dup:
        print(f'[标签转换维表] 警告：标签ID 重复 {len(dup)} 个，保留最后一行: {dup[:5]}')

    my_dict = {k: v for k, v in zip(keys, values) if k}

    source = _ops.render_module({node_dict_name: my_dict})
    file_path.write_text(FormatCode(source)[0], encoding='utf-8')
    return len(my_dict)


if __name__ == '__main__':
    project_path = Path(__file__).resolve().parent.parent
    excel_path_ = project_path / 'excel2consts' / '标签转换维表.xlsx'
    file_path_ = project_path / 'src' / f'{_ops.PART_PREFIX}label_name.py'  # 只写分片，由 dim_table 合并
    n = rewrite_label_name_mapping(excel_path_, 'label_name_mapping', file_path_)
    print(f'[标签转换维表] 标签 {n} 条 → {file_path_.name}')
