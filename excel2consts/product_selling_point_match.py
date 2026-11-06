# -*- coding: utf-8 -*-
"""卖点维表 → consts 分片（product_selling_point_match_dict + cut_in_script_dict）

维表列（见截图）：
  业务ID | 产品名称 | 生成话术 | 标签ID | 兜底话术 | 匹配结果2 | 标签0 | 标签1 ...
- 业务ID 为空的行直接跳过（原来是补成 line{i+2} 这种假 key，会污染维表）
- 标签ID 列：单元格内按换行分隔多个标签ID，顺序即优先级（靠前优先）
- 匹配结果2 列：按换行列出「命中分组」，与 标签0/标签1/... 按位置一一对应；
  第 g 个分组用「标签{g}」列的话术，该列为空时写空串（与原行为一致）
- 兜底话术 / 标签N 话术格式：
      切入话术：xxx
      拒绝挽留话术：yyy
  只写了一段时另一段为空串，业务侧按长度取，不会 IndexError

本脚本只写自己的分片文件，绝不碰 src/consts.py，也就不会覆盖标签转换维表。
"""
import re
import sys
from pathlib import Path

import pandas as pd
from yapf.yapflib.yapf_api import FormatCode

sys.path.append(str(Path(__file__).resolve().parent.parent))

from excel2consts import basic_ops as _ops  # noqa: E402

CUT_IN_MARK = '切入话术'
RETAIN_MARK = '拒绝挽留话术'


def _clean(cell) -> str:
    return str(cell).replace('**', '').strip() if cell is not None else ''


def cut_in_str2tuple(fusion_scripts: str):
    """把一格话术拆成 [切入话术, 挽留话术]；整体为空时返回 ''。"""
    text = _clean(fusion_scripts)
    if not text:
        return ''

    if not text.startswith(CUT_IN_MARK):  # 没写「切入话术」前缀时整格当切入话术
        return [f'【切入话术】\n{_ops.format_script_block(text)}', '']

    body = text[len(CUT_IN_MARK):].lstrip('：:')
    cut_in, _, tail = body.partition(RETAIN_MARK)
    cut_in, retain = _ops.format_script_block(cut_in), _ops.format_script_block(tail.lstrip('：:'))

    scripts = [f'【切入话术】\n{cut_in}' if cut_in else '',
               f'【挽留话术】\n{retain}' if retain else '']
    return scripts if (scripts[0] or scripts[1]) else ''


def split_ids(cell) -> list:
    """标签ID / 匹配结果2 单元格 → 按换行拆出的清单（保持维表行序）。"""
    text = _clean(cell)
    if not text:
        return []
    return [x.strip() for x in re.split(r'[\n\r]+', text) if x.strip()]


def rewrite_product_selling_point_match_dict(excel_path, file_path, *node_dict_names):
    df = pd.read_excel(excel_path, dtype=str, keep_default_na=False)

    match_dict: dict = {}
    cut_in_dict: dict = {}
    label_cols = {c for c in df.columns if re.fullmatch(r'标签\d+', str(c))}

    for _, row in df.iterrows():
        key = _clean(row.get('业务ID'))
        if not key:
            continue

        match_dict[key] = (_clean(row.get('产品名称')),
                           _clean(row.get('生成话术')),
                           '\n'.join(split_ids(row.get('标签ID'))))  # 换行分隔，与 ProductInfoRaw 兼容

        inner: dict = {}
        fallback = cut_in_str2tuple(row.get('兜底话术'))
        if fallback:
            inner['兜底话术'] = fallback

        for g, one_id in enumerate(split_ids(row.get('匹配结果2'))):
            col = f'标签{g}'
            if col not in label_cols:
                continue
            scripts = cut_in_str2tuple(row.get(col))
            inner[one_id] = scripts if scripts else ''
        cut_in_dict[key] = inner

    source = _ops.render_module({node_dict_names[0]: match_dict,
                                 node_dict_names[1]: cut_in_dict})
    # [FIX] 原子写分片，避免半成品文件被下次启动的 merge 读到
    _ops.write_part_atomic(file_path, FormatCode(source)[0])
    return len(match_dict), len(cut_in_dict)


if __name__ == '__main__':
    project_path = Path(__file__).resolve().parent.parent
    excel_path_ = project_path / 'excel2consts' / '卖点维表.xlsx'
    file_path_ = project_path / 'src' / f'{_ops.PART_PREFIX}selling_point.py'  # 只写分片，由 dim_table 合并
    n, m = rewrite_product_selling_point_match_dict(excel_path_, file_path_,
                                                    'product_selling_point_match_dict', 'cut_in_script_dict')
    print(f'[卖点维表] 产品 {n} 条，切入话术 {m} 组 → {file_path_.name}')
