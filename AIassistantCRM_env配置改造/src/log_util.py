from loguru import logger
from pathlib import Path
import time
from config import config
from src.security import mask_sensitive

global_config = config
if global_config.get('log_dir'):
    log_dir_path = Path(global_config['log_dir'])
else:
    log_dir_path = Path(__file__).resolve().parent.parent / 'log'

log_dir_path.mkdir(parents=True, exist_ok=True)


class _MaskedStream:
    """流包装：只在 write 时脱敏手机号 / 身份证号，其余全部透传给 loguru 的 FileSink。

    loguru 0.7 的 add() 规则：
      - sink 是 str/Path  → 才接受 rotation / retention 等参数；
      - sink 是带 write 的对象 → 被当成“流”，多余参数直接 TypeError。
    所以这里先把 FileSink 建出来（拿到它内部的轮转文件流），再包一层脱敏。
    """

    def __init__(self, path):
        from loguru._file_sink import FileSink
        self._sink = FileSink(
            str(path), encoding='utf8', delay=True, mode='a', buffering=1,
        )

    def write(self, message):
        self._sink.write(mask_sensitive(str(message)))

    def __getattr__(self, name):
        # loguru 内部会调用 flush/stop/close 等方法，全部透传给 FileSink
        return getattr(self._sink, name)

    @property
    def name(self):
        return str(self._sink.path)


logger.remove()
for _lv in ("INFO", "ERROR"):
    logger.add(
        _MaskedStream(log_dir_path / f"{_lv}_{time.strftime('%Y-%m-%d')}.log"),
        level=_lv,
        filter=lambda record, _lv=_lv: record["level"].name == _lv,
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | (file:{file.name}:{line}) {message}",
        backtrace=True,
        diagnose=False,  # 不打印变量值，避免手机号进堆栈
    )

my_log = logger
