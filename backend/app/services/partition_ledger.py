"""分区质检台账领域服务：承载多资产质检批次逐表回写的检查结果。

台账按「批次 + 资产 + 窗口 + 分区 + 规则」唯一，每条都带本资产自己的
阈值、实际测点和来源信息；补检记录会标明来源表与来源窗口，便于报表追溯。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class PartitionLedgerService(BaseModuleService):
    MODULE = "partition_ledger"
    ENTITY = "分区质检台账"
    OPERATION_TERM = "入账"
    LIST_FIELDS = [
        "台账编号", "批次编号", "资产组", "质量窗口", "资产编号", "资产名称",
        "分区", "规则编号", "检查类型", "阈值下限", "实际测点", "判定",
        "来源资产", "来源窗口", "补检", "检查时间",
    ]
    REQUIRED_FIELDS = ["批次编号", "资产编号", "规则编号"]
    STATUS_ORDER = ["已入账", "已复核", "已归档"]
    ACTION_RULES = {"复核台账": "已复核", "归档台账": "已归档"}
    NEGATIVE_ACTIONS = []

    def ledger_for(self, batch_code: str, asset_code: str = "") -> list[dict[str, Any]]:
        """按批次（可选资产）查询台账，供质量报告和页面直接消费。"""
        rows = [
            row for row in store.rows(self.MODULE)
            if row.get("批次编号") == batch_code
        ]
        if asset_code:
            rows = [row for row in rows if row.get("资产编号") == asset_code]
        return rows
