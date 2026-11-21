"""多资产质检批次领域服务：把一组资产当作可补检单元，按到齐情况逐表调度。

业务约束（与数仓日常交付一致）：
- 一个批次绑定「资产组 + 质量窗口」，成员固定为事实表和维表，整组才可比较；
- 批量提交按「资产 + 分区」去重，同一分区重复导入只保留一份质量依据；
- 未到齐的资产不生成台账、不借用邻表的阈值或测点，整组结论标记为不可比；
- 调度治理消费质检批次，逐表逐分区运行该资产自己的有效规则，结果回写分区台账；
- 补检必须显式记录取自哪张表、哪个窗口，并继续使用被补资产自己的规则阈值。
"""
from __future__ import annotations

import time as _time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store

ARRIVED = "已到齐"
NOT_ARRIVED = "未到齐"
INGEST_FAILED = "接入失败"

BATCH_STATUS_DRAFT = "待调度"
BATCH_STATUS_PARTIAL = "部分完成"
BATCH_STATUS_DONE = "已完成"
BATCH_STATUS_DISABLED = "已停用"


def _now() -> str:
    return _time.strftime("%Y-%m-%d %H:%M:%S")


def _pick(values: dict[str, Any], *keys: str, default: Any = "") -> Any:
    """按多个中英文别名取值，兼容接口和脚本两种调用习惯。"""
    for key in keys:
        if key in values and values[key] not in (None, ""):
            return values[key]
    return default


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class QualityBatchService(BaseModuleService):
    MODULE = "quality_batch"
    ENTITY = "质检批次"
    OPERATION_TERM = "调度"
    LIST_FIELDS = ["批次编号", "资产组", "质量窗口", "到齐情况", "质检状态", "组结论", "上次调度", "说明"]
    REQUIRED_FIELDS = ["批次编号", "资产组", "质量窗口"]
    STATUS_ORDER = [BATCH_STATUS_DRAFT, BATCH_STATUS_PARTIAL, BATCH_STATUS_DONE, BATCH_STATUS_DISABLED]
    ACTION_RULES = {"停用批次": BATCH_STATUS_DISABLED, "重新调度": BATCH_STATUS_DRAFT}
    NEGATIVE_ACTIONS = ["停用批次"]

    # ── 批次登记 ──────────────────────────────────────────────

    def register_group(
        self,
        group_code: str,
        window: str,
        assets: list[dict[str, Any]],
        group_name: str = "",
        description: str = "",
    ) -> dict[str, Any]:
        """把一组资产登记成可补检单元；同组同窗口重复登记时合并成员。

        成员支持 ``资产编号/asset_code``、``资产类型/asset_type`` 等别名。
        已有成员的证据按分区保留，重复登记不会清空已到齐的证据。
        """
        if not group_code or not window:
            return {"ok": False, "error": "资产组和质量窗口不能为空"}
        if not assets:
            return {"ok": False, "error": "至少登记一个资产成员"}

        batch = self.find_batch(group_code, window)
        if batch is None:
            batch_id = store.next_id(self.MODULE)
            batch = {
                "id": batch_id,
                "批次编号": f"QBAT-{batch_id:04d}",
                "资产组": group_code,
                "资产组名称": group_name or group_code,
                "质量窗口": window,
                "到齐情况": "0/0",
                "质检状态": BATCH_STATUS_DRAFT,
                "组结论": "待补齐",
                "可比结论": "不可比",
                "成员": [],
                "补检记录": [],
                "说明": description,
                "上次调度": "",
                "status": BATCH_STATUS_DRAFT,
                "pending": True,
                "abnormal": False,
                "created_at": _now(),
            }
            store.rows(self.MODULE).append(batch)

        for asset in assets:
            code = str(_pick(asset, "资产编号", "asset_code", "code"))
            if not code:
                return {"ok": False, "error": "资产成员缺少资产编号"}

            member = self._member(batch, code)
            if member is None:
                member = {
                    "资产编号": code,
                    "资产名称": str(_pick(asset, "资产名称", "asset_name", "name", default=code)),
                    "资产类型": str(_pick(asset, "资产类型", "asset_type", "type", default="数据表")),
                    "到齐状态": NOT_ARRIVED,
                    "接入状态": "待接入",
                    "证据": {},
                    "质检状态": "未到齐",
                    "失败原因": "",
                }
                batch["成员"].append(member)
            else:
                if _pick(asset, "资产名称", "asset_name", "name"):
                    member["资产名称"] = str(_pick(asset, "资产名称", "asset_name", "name"))
                if _pick(asset, "资产类型", "asset_type", "type"):
                    member["资产类型"] = str(_pick(asset, "资产类型", "asset_type", "type"))

            # 允许登记时直接带初始证据（例如已交付但尚未质检的批次）
            evidence_value = _pick(asset, "实际测点", "actual_value", "completeness", default=None)
            metrics = asset.get("metrics") or asset.get("测点")
            if metrics or evidence_value is not None:
                self._ingest_evidence(
                    batch, member,
                    partition=str(_pick(asset, "分区", "partition", default=window)),
                    metrics=metrics, value=evidence_value,
                )
            elif str(_pick(asset, "到齐状态", "arrived", default="")) == ARRIVED:
                member["到齐状态"] = ARRIVED
                member["接入状态"] = "已接入"
                member["质检状态"] = "待检查"

        self._refresh_arrival(batch)
        if description:
            batch["说明"] = description
        return {"ok": True, "batch": batch, "member_count": len(batch["成员"])}

    def find_batch(self, group_code: str, window: str) -> dict[str, Any] | None:
        for row in store.rows(self.MODULE):
            if row.get("资产组") == group_code and row.get("质量窗口") == window:
                return row
        return None

    def get_batch(self, batch_id: int | str) -> dict[str, Any] | None:
        batch = store.find(self.MODULE, int(batch_id)) if str(batch_id).isdigit() else None
        if batch is not None:
            return batch
        for row in store.rows(self.MODULE):
            if row.get("批次编号") == str(batch_id):
                return row
        return None

    # ── 逐表规则登记 ──────────────────────────────────────────

    def register_asset_rule(
        self,
        asset_code: str,
        window: str,
        threshold_low: float,
        threshold_high: float = 100,
        check_type: str = "完整性",
        severity: str = "阻断",
        rule_code: str = "",
    ) -> dict[str, Any]:
        """为单张表登记该窗口的有效规则，不同资产的阈值彼此独立。"""
        low = _to_float(threshold_low)
        high = _to_float(threshold_high)
        if not asset_code or low is None or high is None:
            return {"ok": False, "error": "资产编号和完整性上下限不能为空"}

        existing = None
        for rule in store.rows("quality_rule"):
            if (rule.get("目标资产") == asset_code
                    and rule.get("检查类型") == check_type
                    and str(rule.get("目标窗口", "")) in ("", window)
                    and rule.get("status") != "已暂停"):
                existing = rule
                break

        if existing is not None:
            existing.update({
                "阈值下限": str(low),
                "阈值上限": str(high),
                "阻断级别": severity,
                "目标窗口": window,
                "status": "监控中",
                "pending": True,
                "abnormal": False,
            })
            return {"ok": True, "rule": existing, "created": False}

        rule_id = store.next_id("quality_rule")
        rule = {
            "id": rule_id,
            "规则编号": rule_code or f"QUAL-{rule_id:04d}",
            "目标资产": asset_code,
            "检查类型": check_type,
            "阈值下限": str(low),
            "阈值上限": str(high),
            "阻断级别": severity,
            "采样比例": "100",
            "最近检查": "",
            "目标窗口": window,
            "status": "监控中",
            "pending": True,
            "abnormal": False,
            "created_at": _now(),
        }
        store.rows("quality_rule").append(rule)
        return {"ok": True, "rule": rule, "created": True}

    def effective_rules(self, asset_code: str, window: str) -> list[dict[str, Any]]:
        """返回某资产在该窗口的有效规则：已暂停的不参与，其他都参与。"""
        rules = []
        for rule in store.rows("quality_rule"):
            if rule.get("目标资产") != asset_code:
                continue
            if rule.get("status") == "已暂停":
                continue
            rule_window = str(rule.get("目标窗口", ""))
            if rule_window and rule_window != window:
                continue
            rules.append(rule)
        return rules

    # ── 批量提交与去重 ────────────────────────────────────────

    def submit(self, batch_id: int | str, submissions: list[dict[str, Any]]) -> dict[str, Any]:
        """批量提交分区数据；同一资产同一分区重复导入只保留一份质量依据。"""
        batch = self.get_batch(batch_id)
        if batch is None:
            return {"ok": False, "error": "质检批次不存在"}

        results: list[dict[str, Any]] = []
        deduplicated = 0
        failed = 0
        for submission in submissions:
            code = str(_pick(submission, "资产编号", "asset_code", "code"))
            member = self._member(batch, code)
            if member is None:
                results.append({"资产编号": code, "ok": False, "message": "该资产不在批次成员中"})
                continue

            partition = str(_pick(
                submission, "分区", "partition",
                default=self._default_partition(member) or batch["质量窗口"],
            ))
            ingest_status = str(_pick(
                submission, "接入状态", "ingest_status", "status", default="已接入"))

            if ingest_status == INGEST_FAILED:
                # 接入失败的分区没有可信质量依据：清掉该分区证据，避免
                # 后续补齐时被误判成「重复导入」而一直停在未到齐。
                (member.get("证据") or {}).pop(partition, None)
                member["到齐状态"] = INGEST_FAILED
                member["接入状态"] = INGEST_FAILED
                member["失败原因"] = str(_pick(submission, "失败原因", "reason", default="接入失败"))
                member["质检状态"] = "未到齐"
                failed += 1
                results.append({
                    "资产编号": code, "分区": partition, "ok": False,
                    "状态": INGEST_FAILED, "message": member["失败原因"],
                })
                continue

            value = _pick(submission, "实际测点", "actual_value", "completeness", default=None)
            metrics = submission.get("metrics") or submission.get("测点")
            if value is None and not metrics:
                results.append({
                    "资产编号": code, "分区": partition, "ok": False,
                    "message": "缺少实际测点，不能生成质量依据",
                })
                continue

            ingested = self._ingest_evidence(
                batch, member, partition=partition, metrics=metrics, value=value,
            )
            if ingested["deduplicated"]:
                deduplicated += 1
            # 到齐即按本表规则入账，补齐后不需要再等下一次整批调度。
            check = self._run_member_partitions(batch, member, only=partition)
            results.append({
                "资产编号": code,
                "分区": partition,
                "ok": True,
                "状态": ARRIVED,
                "去重": ingested["deduplicated"],
                "重复导入次数": ingested["重复导入次数"],
                "质检": check["status"],
                "message": "同一分区已有一份质量依据，本次仅归并" if ingested["deduplicated"]
                else "质检依据已登记",
            })

        self._refresh_arrival(batch)
        summary = self._finalize_group(batch)
        batch["说明"] = f"最近批量提交 {len(submissions)} 条，去重 {deduplicated} 条，失败 {failed} 条"
        return {
            "ok": True,
            "batch": batch,
            "results": results,
            "submitted": len(submissions),
            "deduplicated": deduplicated,
            "failed": failed,
            **summary,
        }

    def _ingest_evidence(
        self,
        batch: dict[str, Any],
        member: dict[str, Any],
        *,
        partition: str,
        metrics: Any = None,
        value: Any = None,
    ) -> dict[str, Any]:
        """写入成员某分区的证据；同分区只保留一份，重复导入累计次数。"""
        evidence_map = member.setdefault("证据", {})
        existing = evidence_map.get(partition)
        if existing is not None:
            existing["重复导入次数"] = int(existing.get("重复导入次数", 0)) + 1
            existing["最后提交"] = _now()
            return {
                "deduplicated": True,
                "重复导入次数": existing["重复导入次数"],
                "evidence": existing,
            }

        if metrics is None:
            metrics = {}
        if not isinstance(metrics, dict):
            metrics = {"完整性": metrics}
        if value is not None and "完整性" not in metrics:
            metrics["完整性"] = value

        evidence = {
            "分区": partition,
            "测点": {str(k): (_to_float(v) if _to_float(v) is not None else v)
                     for k, v in metrics.items()},
            "首次提交": _now(),
            "最后提交": _now(),
            "重复导入次数": 0,
            "补检": False,
            "来源资产": "",
            "来源窗口": "",
            "来源分区": "",
        }
        evidence_map[partition] = evidence
        member["到齐状态"] = ARRIVED
        member["接入状态"] = "已接入"
        member["失败原因"] = ""
        if member.get("质检状态") in (NOT_ARRIVED, "未到齐"):
            member["质检状态"] = "待检查"
        return {"deduplicated": False, "重复导入次数": 0, "evidence": evidence}

    # ── 调度质检 ──────────────────────────────────────────────

    def schedule(self, batch_id: int | str, job_id: int | None = None) -> dict[str, Any]:
        """调度一个批次：只检查已到齐的资产，逐表逐分区运行该资产的有效规则。

        未到齐的资产保持空台账，绝不借用其他资产的测点或阈值。
        """
        batch = self.get_batch(batch_id)
        if batch is None:
            return {"ok": False, "error": "质检批次不存在"}
        if batch.get("status") == BATCH_STATUS_DISABLED:
            return {"ok": False, "error": "批次已停用，不能调度"}

        self._refresh_arrival(batch)
        per_asset = [self._run_member_partitions(batch, member) for member in batch["成员"]]
        summary = self._finalize_group(batch)
        return {
            "ok": True,
            "batch": batch,
            "asset_results": per_asset,
            **summary,
        }

    def _run_member_partitions(
        self,
        batch: dict[str, Any],
        member: dict[str, Any],
        only: str | None = None,
    ) -> dict[str, Any]:
        """对单个成员逐分区、逐条运行有效规则，结果回写分区台账。"""
        code = member["资产编号"]
        if member.get("到齐状态") != ARRIVED:
            member["质检状态"] = "未到齐"
            return {
                "资产编号": code,
                "资产名称": member.get("资产名称", ""),
                "status": "未到齐", "状态": "未到齐",
                "skipped": True,
                "message": f"{member.get('资产名称', code)} 未到齐，不生成台账、不借用邻表指标",
                "ledger": [],
            }

        evidence_map = member.get("证据") or {}
        partitions = [only] if only else sorted(evidence_map)
        if not partitions or any(not evidence_map.get(p) for p in partitions):
            member["质检状态"] = "缺测点"
            return {
                "资产编号": code,
                "资产名称": member.get("资产名称", ""),
                "status": "缺测点", "状态": "缺测点",
                "skipped": True,
                "message": f"{member.get('资产名称', code)} 没有本表测点，不能用邻表测点代替",
                "ledger": [],
            }

        rules = self.effective_rules(code, batch["质量窗口"])
        if not rules:
            member["质检状态"] = "无有效规则"
            return {
                "资产编号": code,
                "资产名称": member.get("资产名称", ""),
                "status": "无有效规则", "状态": "无有效规则",
                "skipped": True,
                "message": f"{member.get('资产名称', code)} 未登记有效规则，不能沿用邻表阈值",
                "ledger": [],
            }

        from app.services import QUALITY_SERVICE

        rows: list[dict[str, Any]] = []
        breached = False
        for partition in partitions:
            evidence = evidence_map[partition]
            for rule in rules:
                check_type = str(rule.get("检查类型", ""))
                value = _to_float(evidence["测点"].get(check_type))
                if value is None:
                    value = _to_float(evidence["测点"].get("完整性"))
                if value is None:
                    rows.append({
                        "资产编号": code,
                        "分区": partition,
                        "规则编号": rule.get("规则编号"),
                        "检查类型": check_type,
                        "判定": "缺测点",
                        "message": f"本表 {partition} 证据缺少 {check_type} 测点",
                    })
                    continue

                evaluated = QUALITY_SERVICE.evaluate_value(rule, value)
                if not evaluated.get("ok"):
                    rows.append({
                        "资产编号": code,
                        "分区": partition,
                        "规则编号": rule.get("规则编号"),
                        "判定": "规则异常",
                        "message": evaluated.get("error", "阈值配置异常"),
                    })
                    continue

                # 走完整检查链路：更新规则最近检查时间，并按需生成治理问题。
                QUALITY_SERVICE.execute_check(rule["id"], value)
                ledger = self._upsert_ledger(batch, member, evidence, rule, evaluated)
                rows.append(ledger)
                breached = breached or bool(evaluated["breached"])

        member["质检状态"] = "阻断" if breached else "通过"
        return {
            "资产编号": code,
            "资产名称": member.get("资产名称", ""),
            "status": member["质检状态"], "状态": member["质检状态"],
            "skipped": False,
            "ledger": rows,
        }

    def _upsert_ledger(
        self,
        batch: dict[str, Any],
        member: dict[str, Any],
        evidence: dict[str, Any],
        rule: dict[str, Any],
        evaluated: dict[str, Any],
    ) -> dict[str, Any]:
        """按「批次 + 资产 + 窗口 + 分区 + 规则」幂等写入分区质检台账。"""
        partition = str(evidence.get("分区") or "")
        batch_code = batch["批次编号"]
        asset_code = member["资产编号"]

        existing = None
        for row in store.rows("partition_ledger"):
            if (row.get("批次编号") == batch_code
                    and row.get("资产编号") == asset_code
                    and row.get("质量窗口") == batch["质量窗口"]
                    and row.get("分区") == partition
                    and row.get("规则编号") == rule.get("规则编号")):
                existing = row
                break

        source_asset = evidence.get("来源资产") or asset_code
        source_window = evidence.get("来源窗口") or batch["质量窗口"]
        source_partition = evidence.get("来源分区") or partition

        payload = {
            "批次编号": batch_code,
            "资产组": batch["资产组"],
            "质量窗口": batch["质量窗口"],
            "资产编号": asset_code,
            "资产名称": member.get("资产名称", ""),
            "资产类型": member.get("资产类型", ""),
            "分区": partition,
            "规则编号": rule.get("规则编号", ""),
            "检查类型": rule.get("检查类型", ""),
            "阈值下限": str(evaluated["threshold_low"]),
            "阈值上限": str(evaluated["threshold_high"]),
            "实际测点": str(evaluated["current_value"]),
            "判定": "阻断" if evaluated["breached"] else "通过",
            "阻断级别": rule.get("阻断级别", ""),
            "检查说明": evaluated.get("breach_detail", ""),
            "来源资产": source_asset,
            "来源窗口": source_window,
            "来源分区": source_partition,
            "补检": "是" if evidence.get("补检") else "否",
            "检查时间": _now(),
            "status": "已入账",
            "pending": bool(evaluated["breached"]),
            "abnormal": bool(evaluated["breached"]),
        }

        if existing is not None:
            existing.update(payload)
            existing["检查次数"] = int(existing.get("检查次数", 1)) + 1
            return existing

        ledger_id = store.next_id("partition_ledger")
        ledger = {
            "id": ledger_id,
            "台账编号": f"PLED-{ledger_id:04d}",
            **payload,
            "检查次数": 1,
            "created_at": _now(),
        }
        store.rows("partition_ledger").append(ledger)
        return ledger

    # ── 补检 ──────────────────────────────────────────────────

    def supplement(
        self,
        batch_id: int | str,
        asset_code: str,
        *,
        partition: str = "",
        source_asset: str = "",
        source_window: str = "",
        source_partition: str = "",
        actual_value: Any = None,
        metrics: dict[str, Any] | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """补入未到齐资产的分区，并写明补检取自哪张表、哪个窗口。

        补检使用被补资产自己登记的有效规则；如果本表没有规则，直接拒绝，
        不会退回实收表已经通过的阈值和测点。
        """
        batch = self.get_batch(batch_id)
        if batch is None:
            return {"ok": False, "error": "质检批次不存在"}
        member = self._member(batch, asset_code)
        if member is None:
            return {"ok": False, "error": f"{asset_code} 不在批次成员中"}

        source_asset = source_asset or asset_code
        source_window = source_window or batch["质量窗口"]
        source_partition = source_partition or partition or batch["质量窗口"]
        partition = partition or self._default_partition(member) or batch["质量窗口"]

        if actual_value is None and not metrics:
            return {"ok": False, "error": "补检必须提供本表的实际测点"}
        if not self.effective_rules(asset_code, batch["质量窗口"]):
            return {
                "ok": False,
                "error": f"{member.get('资产名称', asset_code)} 未登记有效规则，不能沿用实收表已通过的阈值",
            }

        # 同一分区补检覆盖测点；来源字段随该分区证据单独保留。
        evidence_map = member.setdefault("证据", {})
        evidence_map.pop(partition, None)
        ingested = self._ingest_evidence(
            batch, member, partition=partition, metrics=metrics, value=actual_value)
        evidence = ingested["evidence"]
        evidence["补检"] = True
        evidence["来源资产"] = source_asset
        evidence["来源窗口"] = source_window
        evidence["来源分区"] = source_partition

        record = {
            "资产编号": asset_code,
            "资产名称": member.get("资产名称", ""),
            "分区": partition,
            "来源资产": source_asset,
            "来源窗口": source_window,
            "来源分区": source_partition,
            "补检时间": _now(),
            "说明": note,
        }
        batch.setdefault("补检记录", []).append(record)
        self._refresh_arrival(batch)

        # 只重跑被补资产被补的分区，其他资产沿用已经入账的结果。
        asset_result = self._run_member_partitions(batch, member, only=partition)
        summary = self._finalize_group(batch)
        if batch["可比结论"] == "可比":
            batch["说明"] = (
                f"补检完成：{member.get('资产名称', asset_code)} 取自"
                f"{source_asset}/{source_window}/{source_partition}，{batch['组结论']}")

        return {
            "ok": True,
            "batch": batch,
            "supplement": record,
            "asset_result": asset_result,
            "ledger": [row for row in summary["ledger"] if row.get("资产编号") == asset_code],
            **{k: v for k, v in summary.items() if k != "ledger"},
        }

    # ── 质量报告与未结项 ──────────────────────────────────────

    def report(self, batch_id: int | str) -> dict[str, Any]:
        """三表关联的整组质量报告：每张表的最新台账 + 组结论 + 未结调度项。"""
        batch = self.get_batch(batch_id)
        if batch is None:
            return {"ok": False, "error": "质检批次不存在"}

        ledger_rows = self._ledger_rows(batch)
        assets: list[dict[str, Any]] = []
        report_rows: list[dict[str, Any]] = []
        for member in batch["成员"]:
            code = member["资产编号"]
            rows = [row for row in ledger_rows if row.get("资产编号") == code]
            evidence_map = member.get("证据") or {}
            partitions = sorted(evidence_map)
            if rows:
                judgements = sorted({str(row.get("判定")) for row in rows})
                report_rows.extend(rows)
            else:
                judgements = ["未到齐" if member.get("到齐状态") != ARRIVED else "未检查"]
                report_rows.append({
                    "批次编号": batch["批次编号"],
                    "资产编号": code,
                    "资产名称": member.get("资产名称", ""),
                    "资产类型": member.get("资产类型", ""),
                    "分区": partitions[0] if partitions else "",
                    "来源资产": "",
                    "来源窗口": "",
                    "补检": "否",
                    "阈值下限": "",
                    "实际测点": "",
                    "判定": judgements[0],
                    "检查说明": ("未到齐资产不生成台账"
                                 if member.get("到齐状态") != ARRIVED else "尚未调度"),
                })

            supplemented = [evidence_map[p] for p in partitions if evidence_map[p].get("补检")]
            assets.append({
                "资产编号": code,
                "资产名称": member.get("资产名称", ""),
                "资产类型": member.get("资产类型", ""),
                "到齐状态": member.get("到齐状态", ""),
                "质检状态": member.get("质检状态", ""),
                "分区": partitions,
                "补检": bool(supplemented),
                "来源资产": supplemented[0].get("来源资产", "") if supplemented else "",
                "来源窗口": supplemented[0].get("来源窗口", "") if supplemented else "",
                "来源分区": supplemented[0].get("来源分区", "") if supplemented else "",
                "台账": rows,
                "实际测点": rows[0].get("实际测点") if rows else None,
                "阈值下限": rows[0].get("阈值下限") if rows else None,
                "判定": "阻断" if "阻断" in judgements else judgements[0],
            })

        return {
            "ok": True,
            "batch_id": batch["id"],
            "批次编号": batch["批次编号"],
            "资产组": batch["资产组"],
            "质量窗口": batch["质量窗口"],
            "到齐情况": batch["到齐情况"],
            "组结论": batch.get("组结论", ""),
            "可比结论": batch.get("可比结论", "不可比"),
            "comparable": batch.get("可比结论") == "可比",
            "未到齐资产": [m["资产编号"] for m in batch["成员"] if m.get("到齐状态") != ARRIVED],
            "assets": assets,
            "rows": report_rows,
            "未结调度项": self.unresolved_items(batch["批次编号"]),
        }

    def unresolved_items(self, batch_code: str = "") -> list[dict[str, Any]]:
        """未结调度项：已挂到批次且尚未收口（未完成/未停用）的调度作业。"""
        items = []
        for job in store.rows("schedule_job"):
            if batch_code and job.get("批次编号") != batch_code:
                continue
            if not batch_code and not job.get("批次编号"):
                continue
            if job.get("status") in ("已完成", "已停用"):
                continue
            items.append(job)
        return items

    # ── 内部辅助 ──────────────────────────────────────────────

    def _member(self, batch: dict[str, Any], asset_code: str) -> dict[str, Any] | None:
        for member in batch.get("成员", []):
            if member.get("资产编号") == asset_code:
                return member
        return None

    @staticmethod
    def _default_partition(member: dict[str, Any]) -> str:
        evidence_map = member.get("证据") or {}
        if evidence_map:
            return sorted(evidence_map)[0]
        return str(member.get("分区") or "")

    def _refresh_arrival(self, batch: dict[str, Any]) -> None:
        members = batch.get("成员", [])
        arrived = sum(1 for m in members if m.get("到齐状态") == ARRIVED)
        batch["到齐情况"] = f"{arrived}/{len(members)}"
        batch["pending"] = arrived < len(members) or bool(batch.get("group_pending"))

    def _ledger_rows(self, batch: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            row for row in store.rows("partition_ledger")
            if row.get("批次编号") == batch["批次编号"]
        ]

    def _finalize_group(self, batch: dict[str, Any]) -> dict[str, Any]:
        """按当前到齐与台账状态收口整组结论，并同步调度作业。"""
        ledger_rows = self._ledger_rows(batch)
        breached = [row for row in ledger_rows if row.get("判定") == "阻断"]
        missing = [m for m in batch["成员"] if m.get("到齐状态") != ARRIVED]

        if missing:
            batch["质检状态"] = BATCH_STATUS_PARTIAL
            batch["组结论"] = "待补齐"
            batch["可比结论"] = "不可比"
            reason = "未到齐：" + "、".join(
                f"{m.get('资产名称') or m['资产编号']}({m['资产编号']})" for m in missing)
        elif breached:
            batch["质检状态"] = BATCH_STATUS_DONE
            batch["组结论"] = "整组可比-存在阻断"
            batch["可比结论"] = "可比"
            reason = "存在质量阻断：" + "、".join(
                sorted({str(row.get("资产名称") or row.get("资产编号")) for row in breached}))
        else:
            batch["质检状态"] = BATCH_STATUS_DONE
            batch["组结论"] = "整组可比-通过"
            batch["可比结论"] = "可比"
            reason = ""

        batch["status"] = batch["质检状态"]
        batch["abnormal"] = bool(breached)
        batch["pending"] = bool(missing) or bool(breached)
        batch["group_pending"] = batch["pending"]
        batch["上次调度"] = _now()
        batch["未到齐资产"] = [m["资产编号"] for m in missing]
        batch["说明"] = reason or f"整组 {len(batch['成员'])} 张表已完成质检"
        job = self._sync_schedule_job(batch, reason=reason)
        return {
            "ledger": ledger_rows,
            "group_conclusion": batch["组结论"],
            "comparable": batch["可比结论"] == "可比",
            "missing_assets": [m["资产编号"] for m in missing],
            "job": job,
        }

    def _sync_schedule_job(self, batch: dict[str, Any], reason: str = "") -> dict[str, Any]:
        """把批次状态同步成调度治理里的一个作业，供报表使用方查未结项。"""
        job = None
        for row in store.rows("schedule_job"):
            if row.get("批次编号") == batch["批次编号"]:
                job = row
                break

        unresolved = batch.get("可比结论") != "可比" or bool(batch.get("abnormal"))
        target_status = "执行中" if unresolved else "已完成"
        dependencies = f"消费质检批次{batch['批次编号']}，到齐{batch['到齐情况']}"
        fields = {
            "依赖检查": dependencies,
            "批次编号": batch["批次编号"],
            "资产组": batch["资产组"],
            "质量窗口": batch["质量窗口"],
            "待办说明": reason,
            "status": target_status,
            "pending": unresolved,
            "abnormal": bool(batch.get("abnormal")),
            "执行时间": batch.get("上次调度", ""),
        }

        if job is None:
            job_id = store.next_id("schedule_job")
            job = {
                "id": job_id,
                "作业编号": f"SCHE-{job_id:04d}",
                "治理类型": "质量检查",
                "目标资产": batch["资产组"],
                "Cron表达式": "手动触发",
                "超时上限": "30min",
                "失败通知": "data-quality@example.com",
                "下次执行": "-",
                "created_at": _now(),
                **fields,
            }
            store.rows("schedule_job").append(job)
        else:
            job.update(fields)
        return job
