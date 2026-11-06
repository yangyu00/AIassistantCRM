from services.product_info_raw import ProductInfoRaw
from src.security import html_text


class ProductInfoRewrite(ProductInfoRaw):
    def __init__(self, product_id, custom_label_info, record=None):
        super().__init__(product_id, custom_label_info)
        # 未注册的产品ID（原实现返回 None）不能静默变成 None.name 崩溃，给个可读默认值
        if self.product_name is None:
            self.product_name = "未知产品"
        self.record = record if isinstance(record, dict) else None
        self.product_name = self.__get_product_name() or self.product_name
        self.output_script = self.__get_output_script()

    def __get_product_name(self):
        if self.product_id == "202608041615335173":
            return "号卡业务回捞商机"

        if self.product_id == "202608041615335172":
            return "套餐业务回捞商机"

        if self.product_id == "202608041615335174":
            return "宽带业务回捞商机"

    def __get_output_script(self):
        # 从独立数据库查询模块获取最近一条记录
        output_script = ''
        if not isinstance(self.record, dict):
            self.record = None
        if self.record:
            # 库表内容统一转义后再包标签
            g = lambda k: html_text(self.record.get(k) or '无')
            output_script += '<strong>【通话总结】</strong>' + g('content_abstract') + '\n'
            output_script += '<strong>【用户需求】</strong>' + '<strong><span style="color:blue;">' + g('type_xl') + '：</span></strong>' + g('cust_demand') + '\n'
            output_script += '<strong>【已推荐政策】</strong>' + g('rec_strategy') + '\n'
            output_script += ('<strong>【拒绝原因】</strong>\n'
                              + '<strong><span style="color:blue;">拒绝原因：</span></strong>' + g('reject_reason') + '\n'
                              + '<strong><span style="color:blue;">具体表述：</span></strong>' + g('reason_note') + '\n')

        output_script += (self.auxiliary_portrait + '\n') if self.auxiliary_portrait else ""

        return output_script

    def __str__(self):
        return (f'Product ID: {self.product_id}\n'
                f'Product Name: {self.product_name}\n'
                f'queried_label_ids: {self.queried_label_ids}\n'
                f'queried_label_nvs: {self.queried_label_nvs}\n'
                f'auxiliary_portrait: {self.auxiliary_portrait}\n'
                f'output_script: {self.output_script}')
