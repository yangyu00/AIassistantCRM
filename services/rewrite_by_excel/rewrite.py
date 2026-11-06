import re

from services.product_info_raw import ProductInfoRaw
from src import dim_table
from src.security import html_text

from src.log_util import my_log

def _tag_priority(product_id, search_id):
    """标签在该产品「标签ID」列里的行号 = 维表优先级，越靠前越优先。

    维表是把标签ID直接当字典键的（不是"标签0/标签1"），而 Python dict 只保证
    插入顺序，多标签同时命中时原来返回哪条完全取决于 Excel 列的读取顺序，
    业务上不可解释。这里回到维表原文按行号定序：上面那行优先。
    兜底话术恒为最大值，只在没有任何标签命中时使用。
    """
    if search_id == "兜底话术":
        return float('inf')
    _, _, label_ids = dim_table.selling_point(product_id)
    if not label_ids:
        return 0
    candidates = [x.strip() for x in re.split(r'[\n,，;；\r]+', str(label_ids)) if x.strip()]
    # 没在「标签ID」列里登记的命中项排到兜底话术之后：兜底仍优先于未登记标签，
    # 但永远不会让 sorted 因为 key 类型不一致（int vs float）抛异常
    return candidates.index(search_id) if search_id in candidates else len(candidates) + 1


class ProductInfoRewrite(ProductInfoRaw):
    def __init__(self, product_id, custom_label_info):
        super().__init__(product_id, custom_label_info)
        self.selling_point = self.__get_selling_point()
        self.cut_in_statement = self.__get_cut_in_statement()
        self.retain_statement = self.__get_retain_statement()
        self.output_script = self.__get_output_script()

    def __get_selling_point(self):
        _, old_text, _ = dim_table.selling_point(self.product_id)
        if not old_text:
            return ""
        # 将“1、资费：”后的字符标红，若“2、价格”未换行，增加换行符
        pattern = re.compile(r'\d、.*?：', re.DOTALL)
        extract = pattern.findall(old_text)
        if not extract:
            return "【卖点】\n" + old_text

        # 此时至少存在"1、xxx："
        start_index = old_text.find(extract[0]) + len(extract[0])

        # 此时只有"1、xxx："
        if len(extract) == 1:
            return "【卖点】\n" + '<strong>' + old_text[:start_index] + '<span style="color:red;">' + old_text[
                                                                                                    start_index:] + '</span></strong>'

        # 此时至少存在"2、xxx："
        end_index = old_text.find(extract[1])
        fusion_text = "【卖点】\n" + '<strong>' + old_text[:start_index] + '<span style="color:red;">' + old_text[
                                                                                                       start_index:end_index].strip() + '</span></strong>'
        ready_join_text = old_text[end_index:]
        split_chars_list = []
        for split_chars in extract[1:]:
            split_chars_list.append(split_chars)
        split_rule = f'{"|".join(re.escape(char) for char in split_chars_list)}'
        for i, g in enumerate(re.split(split_rule, ready_join_text)[1:]):
            fusion_text += "\n" + '<strong>' + split_chars_list[i] + '</strong>' + g.strip()

        return html_text(fusion_text)

    def _pick_statement(self, index: int):
        """切入话术 index=0 / 挽留话术 index=1。

        修了两个问题：
        1. 原实现写死 fusion_script_list[0] / [1]，维表某格只填了一条话术时
           会 IndexError，被 except 吞掉后连兜底话术都拿不到；现在按长度取。
        2. 多标签同时命中时不再依赖 dict 插入顺序，按维表行序取优先级最高的。
        """
        id_sequence_obj = dim_table.cut_in_entry(self.product_id)
        if not isinstance(id_sequence_obj, dict) or not id_sequence_obj:
            return ""

        try:
            ordered = sorted(id_sequence_obj.items(),
                             key=lambda kv: _tag_priority(self.product_id, kv[0]))
            for search_id, scripts in ordered:
                if search_id == "兜底话术" or not isinstance(scripts, (list, tuple)):
                    continue
                label = self.custom_label_info.get(search_id)
                if label is None or str(label[1]).strip() not in ("是", "1"):
                    continue
                if len(scripts) > index and scripts[index]:
                    return str(scripts[index])
        except Exception as e:
            my_log.error(f'{"切入" if index == 0 else "挽留"}话术读取错误：{e}')

        fallback = id_sequence_obj.get("兜底话术")
        if isinstance(fallback, (list, tuple)) and len(fallback) > index and fallback[index]:
            return str(fallback[index])
        return ""

    def __get_cut_in_statement(self):
        return self._pick_statement(0)

    def __get_retain_statement(self):
        return self._pick_statement(1)

    @staticmethod
    def __strong_text(text):
        """只加粗小节标题。先转义外部文本里的尖括号，再插入标题标签，
        避免维表话术里出现 < / > 时把结构撑坏。"""
        need_strong_text = ["【辅助画像】", "【切入话术】", "【卖点】", "【挽留话术】"]
        text = html_text(text)
        for title in need_strong_text:
            text = text.replace(title, f"<strong>{title}</strong>")
        return text

    def __get_output_script(self):
        # 维表中业务
        output_script = ""
        output_script += (self.auxiliary_portrait + '\n') if self.auxiliary_portrait else ""
        output_script += (self.cut_in_statement + '\n') if self.cut_in_statement else ""
        output_script += (self.selling_point + '\n') if self.selling_point else ""
        output_script += self.retain_statement if self.retain_statement else ""

        output_script = self.__strong_text(output_script)

        return output_script

    def __str__(self):
        return (f'Product ID: {self.product_id}\n'
                f'Product Name: {self.product_name}\n'
                f'queried_label_ids: {self.queried_label_ids}\n'
                f'queried_label_nvs: {self.queried_label_nvs}\n'
                f'selling_point: {self.selling_point}\n'
                f'auxiliary_portrait: {self.auxiliary_portrait}\n'
                f'cut_in_statement: {self.cut_in_statement}\n'
                f'retain_statement: {self.retain_statement}\n'
                f'output_script: {self.output_script}')
