from src import dim_table
from src.security import html_text


class ProductInfoRaw(object):
    def __init__(self, product_id, custom_label_info):
        self.product_id = product_id
        self.custom_label_info = custom_label_info
        self.product_name = self.__get_product_name()
        self.queried_label_ids = self.__get_queried_label_ids()
        self.queried_label_nvs = self.__get_queried_label_nvs()
        self.auxiliary_portrait = self.__get_auxiliary_portrait()
        self.output_script = "无"

    def __get_product_name(self):
        """产品名称取自卖点维表；查不到返回 None，由接口层按 code=2 处理。"""
        name, _, _ = dim_table.selling_point(self.product_id)
        return name or None

    def __get_queried_label_ids(self):
        """维表第 3 列是按换行分隔的标签ID清单，可能是 str 也可能是 list。"""
        _, _, label_ids = dim_table.selling_point(self.product_id)
        if not label_ids:
            return []
        if isinstance(label_ids, (list, tuple, set)):
            return [str(x).strip() for x in label_ids if str(x).strip()]
        return [x.strip() for x in str(label_ids).split("\n") if x.strip()]

    def __get_queried_label_nvs(self):
        queried_label_nvs = []
        for one_label_id in self.queried_label_ids:
            if one_label_id not in self.custom_label_info:
                queried_label_nvs.append(None)
                continue
            queried_label_nvs.append((self.custom_label_info[one_label_id][0],
                                      self.custom_label_info[one_label_id][1]))

        return queried_label_nvs

    def __get_auxiliary_portrait(self):
        portrait_str = ""
        exist_label_name_list = []
        exist_ids = set()
        for i, label_nv in enumerate(self.queried_label_nvs):
            if label_nv is None:
                continue
            label_name, label_value = label_nv[0], label_nv[1]
            if not label_value:
                continue

            # 维表配了展示名就用维表的，否则沿用 IOP 返回的 ITEMNAME
            label_name = dim_table.label_name(self.queried_label_ids[i], label_name)

            # 近期上网掉线、卡顿记录的特殊处理
            if self.queried_label_ids[i] == "a1e357f57b474d51119bf003443bd088" and str(label_value) == "0.00":
                label_value = "无"

            # 涉及费用的标签转换规则
            if self.queried_label_ids[i] == "cb933e7eb32b67ec637c78c6f2be65a1":
                if str(label_value) == "0.00":
                    label_value = "0元"
                else:
                    label_value = str(label_value) + "元"

            # 抖音快手使用流量后加“M”单位
            if self.queried_label_ids[i] in ("44aad5383d8d8910e72bd8f74f1cd1df", "bbc3b62f605a0112d7bde076eb24fea5"):
                label_value = str(label_value) + "M"

            # 1,0 切换成 是, 否
            if "是否" in label_name and str(label_value) == "1":
                label_value = "是"
            elif "是否" in label_name and str(label_value) == "0":
                label_value = "否"
            if self.queried_label_ids[i] == "1c7b8fdc55e00c4300adcf1c0543f00f":
                label_value = ("是" if str(label_value) == "1" else "否")

            # 涉及是否的重复标签去重：IOP 与维表对同名标签可能给出不同写法，
            # 这里按「标签ID」判重，不再依赖中文展示名是否完全一致
            if self.queried_label_ids[i] in exist_ids:
                if label_value == "是":
                    # 把先前写进去的同名标签由「否」升级为「是」（原 replace 结果没赋值，等于没生效）
                    old = f'{html_text(label_name)}: <strong><span style="color:blue;">否</span></strong>; '
                    new = f'{html_text(label_name)}: <strong><span style="color:blue;">是</span></strong>; '
                    if old in portrait_str:
                        portrait_str = portrait_str.replace(old, new, 1)
                continue
            exist_ids.add(self.queried_label_ids[i])

            # 降档阈值的特殊处理
            if self.queried_label_ids[i] in ("0e6f7d4a8b32aa551d09174481857c39", "efe5295c6763e2eab16afbaa984a9ee4"):
                portrait_str += (f'<strong><span style="color:red;">{html_text(label_name)}</span></strong>'
                             f': {html_text(label_value)}; ')
                continue

            portrait_str += (f'{html_text(label_name)}: <strong><span style="color:blue;">'
                             f'{html_text(label_value)}</span></strong>; ')
            exist_label_name_list.append(label_name)

        if portrait_str:
            portrait_str = "【辅助画像】" + portrait_str
            portrait_str = portrait_str.rstrip('; ')

        return portrait_str

    def __str__(self):
        return (f'Product ID: {self.product_id}\n'
                f'Product Name: {self.product_name}\n'
                f'queried_label_ids: {self.queried_label_ids}\n'
                f'queried_label_nvs: {self.queried_label_nvs}\n'
                f'auxiliary_portrait: {self.auxiliary_portrait}')
