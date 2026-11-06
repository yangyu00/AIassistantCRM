# -*- coding: utf-8 -*-
from . import table_router
from sftp_common.db import TableBatcher

_original_add = TableBatcher.add


def _patched_add(self, row, key):
    cfg = self._cfg
    if cfg.get('router') != 'map':
        return _original_add(self, row, key)
    t_name = table_router.resolve_table(cfg, self, key, row)
    self.add_to(t_name, row)
    return t_name


def install():
    TableBatcher.add = _patched_add


def uninstall():
    TableBatcher.add = _original_add
