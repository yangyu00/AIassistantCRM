import datetime

import aiohttp

from IOP.get_token import TOKENS, token2global
from IOP.iop_client import get_custom_info, startup
from config import config
from src.log_util import my_log


async def get_custom_info_in_global(tel):
    await startup()
    return await get_custom_info(tel)


async def _legacy_get_custom_info_in_global(tel):
    if TOKENS.access_token is None or datetime.datetime.now() > TOKENS.deadline:
        await token2global()
        for _ in range(3):
            if TOKENS.access_token is None or datetime.datetime.now() > TOKENS.deadline:
                await token2global()
            else:
                break

    if TOKENS.access_token is None or datetime.datetime.now() > TOKENS.deadline:
        my_log.error("No access token")
        return None

    url = ("http://{host}:{port}/openapi/httpService/UserService"
           "?access_token={access_token}&method=qryCustInfoByChannel&format=json").format(
        host=config['IOP']['CustInfo']['host'],
        port=config['IOP']['CustInfo']['port'],
        access_token=TOKENS.access_token)

    headers = {'Content-Type': 'application/json; charset=gbk', 'Accept-Encoding': 'gzip, deflate'}

    post_data = {
        "VERSION": f"{config['IOP']['CustInfo']['version']}",
        "TELNUM": tel,
        "TYPE": f"{config['IOP']['CustInfo']['type']}",
    }

    async with aiohttp.ClientSession() as client:
        async with client.post(url, json=post_data, headers=headers) as response:
            try:
                res_data = await response.json(content_type=None)
                status_code = response.status
            except Exception as e:
                my_log.error(f"Can not get labels, response: {response}, error: {e}")
                return {}

    custom_label_dict = {}
    if status_code == 200 and res_data["res_code"] == "0":
        for custom_label in res_data["result"]["BUSIINFO"]:
            custom_label_dict.update({custom_label["ITEMCODE"]: [custom_label["ITEMNAME"], custom_label["ITEMVALUE"]]})

    return custom_label_dict
