"""质量规则领域服务：完整性、准确性、一致性和及时性规则定义与阻断策略。

Pipeline 3: Quality rule definition → monitoring → issue tracking
"""
from __future__ import annotations

import time as _time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class QualityRuleService(BaseModuleService):
    MODULE = "quality_rule"
    ENTITY = "质量检查"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["规则编号", "目标资产", "检查类型", "阈值下限", "阈值上限", "阻断级别", "采样比例", "最近检查"]
    REQUIRED_FIELDS = ["规则编号", "目标资产", "检查类型"]
    STATUS_ORDER = ["草稿", "监控中", "已阻断", "已暂停"]
    ACTION_RULES = {"启用监控": "监控中", "确认阻断": "已阻断", "暂停规则": "已暂停"}
    NEGATIVE_ACTIONS = ["确认阻断"]

    def define_rule(self, asset_code: str, check_type: str, threshold_low: str, threshold_high: str, severity: str) -> dict[str, Any]:
        """定义质量规则（Pipeline 3 入口）。"""
        rule_id = store.next_id(self.MODULE)
        rule = {
            "id": rule_id,
            "规则编号": f"QUAL-{rule_id:04d}",
            "目标资产": asset_code,
            "检查类型": check_type,
            "阈值下限": threshold_low,
            "阈值上限": threshold_high,
            "阻断级别": severity,
            "采样比例": "100",
            "最近检查": "",
            "status": "监控中",
            "pending": True,
            "abnormal": False,
            "created_at": _time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        store.rows(self.MODULE).append(rule)
        return rule

    @staticmethod
    def evaluate_value(rule: dict[str, Any], current_value: float) -> dict[str, Any]:
        """按规则自身的阈值判定一个测点，纯函数、不写库。

        多资产质检批次直接复用它：每条台账必须带上该资产自己的规则阈值，
        不允许把邻表的阈值或测点套到当前资产上。
        """
        try:
            low = float(rule.get("阈值下限", 0))
            high = float(rule.get("阈值上限", 100))
        except (ValueError, TypeError):
            return {"ok": False, "error": "阈值配置异常"}

        check_type = rule.get("检查类型", "")
        breached = False
        breach_detail = ""

        if check_type in ("完整性", "completeness"):
            breached = current_value < low
            breach_detail = f"当前值{current_value}低于完整性阈值{low}"
        elif check_type in ("准确性", "accuracy"):
            breached = current_value < low
            breach_detail = f"当前值{current_value}低于准确性阈值{low}"
        elif check_type in ("一致性", "consistency"):
            breached = current_value > high
            breach_detail = f"当前值{current_value}超过一致性上限{high}"
        else:
            breached = current_value < low or current_value > high
            breach_detail = f"当前值{current_value}不在[{low}, {high}]范围内"

        return {
            "ok": True,
            "current_value": current_value,
            "threshold_low": low,
            "threshold_high": high,
            "breached": breached,
            "breach_detail": breach_detail,
        }

    def execute_check(self, rule_id: int, current_value: float) -> dict[str, Any]:
        """执行质量检查，如果触发阈值则自动生成治理问题（Pipeline 3 核心方法）。"""
        rule = self.get_entry(rule_id)
        if not rule:
            return {"ok": False, "error": "规则不存在"}

        evaluated = self.evaluate_value(rule, current_value)
        if not evaluated.get("ok"):
            return {"ok": False, "error": evaluated.get("error", "阈值配置异常")}
        low = evaluated["threshold_low"]
        high = evaluated["threshold_high"]
        breached = evaluated["breached"]
        breach_detail = evaluated["breach_detail"]

        # 更新检查时间
        rule["最近检查"] = _time.strftime("%Y-%m-%d %H:%M:%S")

        result = {
            "ok": True,
            "rule_id": rule_id,
            "current_value": current_value,
            "threshold_low": low,
            "threshold_high": high,
            "breached": breached,
            "breach_detail": breach_detail,
            "check_time": rule["最近检查"],
        }

        if breached:
            rule["status"] = "已阻断"
            rule["abnormal"] = True

            # Pipeline 3: 触发问题追踪
            issue = self._create_quality_issue(rule, current_value, breach_detail)
            result["issue_created"] = True
            result["issue"] = issue

            store.log_change(
                self.MODULE, rule_id, "质量检查阻断",
                before="监控中", after="已阻断",
                source="pipeline_3", scope="quality_rule->issue_track"
            )
            self.emit_event("quality_breached", {
                "rule_id": rule_id,
                "asset_code": rule.get("目标资产"),
                "issue_id": issue["id"] if issue else None,
            })

        return result

    def _create_quality_issue(self, rule: dict[str, Any], current_value: float, detail: str) -> dict[str, Any]:
        """生成治理问题并写入 issue_track 表。"""
        issue_id = store.next_id("issue_track")
        severity_map = {"阻断": "严重", "警告": "需关注", "提示": "一般"}
        issue = {
            "id": issue_id,
            "问题编号": f"ISSU-{issue_id:04d}",
            "关联资产": rule.get("目标资产", ""),
            "问题类型": f"质量{rule.get('检查类型', '检查')}异常",
            "严重级别": severity_map.get(rule.get("阻断级别", "提示"), "一般"),
            "指派处理": "数据质量管理员",
            "当前状态": "待处理",
            "修复方案": f"当前值{current_value}不满足质量规则{rule.get('规则编号', '')}",
            "截止日期": _time.strftime("%Y-%m-%d", _time.localtime(_time.time() + 86400 * 7)),
            "status": "待确认",
            "pending": True,
            "abnormal": False,
            "created_at": _time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        store.rows("issue_track").append(issue)
        return issue

    def batch_check_assets(self, asset_code: str, metrics: dict[str, float]) -> list[dict[str, Any]]:
        """对资产批量执行所有关联质量规则检查。"""
        results = []
        rules = store.find_by_field(self.MODULE, "目标资产", asset_code)
        for rule in rules:
            metric_key = rule.get("检查类型", "")
            value = metrics.get(metric_key, 0)
            result = self.execute_check(rule["id"], value)
            results.append(result)
        return results

    def get_rules_by_asset(self, asset_code: str) -> list[dict[str, Any]]:
        """获取资产的所有质量规则。"""
        return store.find_by_field(self.MODULE, "目标资产", asset_code)