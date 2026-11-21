"""质检批次接口：批次登记、批量提交、调度、补检与整组质量报告。

批次业务动作不能只靠通用状态流转表达，单独开一组路由把领域能力暴露给
调度员和报表使用方；通用资源路由仍负责列表/导出/登记/动作。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import QUALITY_BATCH_SERVICE, PARTITION_LEDGER_SERVICE

router = APIRouter(prefix="/api/quality_batch", tags=["多资产质检批次"])


class BatchRegisterPayload(BaseModel):
    group_code: str
    window: str
    group_name: str = ""
    description: str = ""
    assets: list[dict[str, Any]] = Field(default_factory=list)


class BatchSubmitPayload(BaseModel):
    submissions: list[dict[str, Any]] = Field(default_factory=list)


class RulePayload(BaseModel):
    asset_code: str
    window: str
    threshold_low: float
    threshold_high: float = 100
    check_type: str = "完整性"
    severity: str = "阻断"
    rule_code: str = ""


class SupplementPayload(BaseModel):
    asset_code: str
    partition: str = ""
    source_asset: str = ""
    source_window: str = ""
    source_partition: str = ""
    actual_value: Any = None
    metrics: dict[str, Any] | None = None
    note: str = ""


def _batch_or_404(batch_id: int) -> dict[str, Any]:
    batch = QUALITY_BATCH_SERVICE.get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"质检批次 {batch_id} 不存在")
    return batch


@router.post("/register")
def register_batch(payload: BatchRegisterPayload) -> dict[str, Any]:
    result = QUALITY_BATCH_SERVICE.register_group(
        payload.group_code, payload.window, payload.assets,
        group_name=payload.group_name, description=payload.description,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "登记失败"))
    return result


@router.post("/rules")
def register_rule(payload: RulePayload) -> dict[str, Any]:
    result = QUALITY_BATCH_SERVICE.register_asset_rule(
        payload.asset_code, payload.window, payload.threshold_low,
        threshold_high=payload.threshold_high, check_type=payload.check_type,
        severity=payload.severity, rule_code=payload.rule_code,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "规则登记失败"))
    return result


@router.get("/{batch_id}/report")
def batch_report(batch_id: int) -> dict[str, Any]:
    _batch_or_404(batch_id)
    report = QUALITY_BATCH_SERVICE.report(batch_id)
    if not report.get("ok"):
        raise HTTPException(status_code=400, detail=report.get("error", "报告生成失败"))
    return report


@router.get("/{batch_id}/ledger")
def batch_ledger(batch_id: int, asset_code: str = "") -> dict[str, Any]:
    batch = _batch_or_404(batch_id)
    rows = PARTITION_LEDGER_SERVICE.ledger_for(batch["批次编号"], asset_code)
    return {"items": rows, "total": len(rows)}


@router.post("/{batch_id}/submit")
def batch_submit(batch_id: int, payload: BatchSubmitPayload) -> dict[str, Any]:
    _batch_or_404(batch_id)
    result = QUALITY_BATCH_SERVICE.submit(batch_id, payload.submissions)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "批量提交失败"))
    return result


@router.post("/{batch_id}/schedule")
def batch_schedule(batch_id: int, job_id: int | None = None) -> dict[str, Any]:
    _batch_or_404(batch_id)
    result = QUALITY_BATCH_SERVICE.schedule(batch_id, job_id=job_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "调度失败"))
    return result


@router.post("/{batch_id}/supplement")
def batch_supplement(batch_id: int, payload: SupplementPayload) -> dict[str, Any]:
    _batch_or_404(batch_id)
    result = QUALITY_BATCH_SERVICE.supplement(
        batch_id, payload.asset_code,
        partition=payload.partition,
        source_asset=payload.source_asset,
        source_window=payload.source_window,
        source_partition=payload.source_partition,
        actual_value=payload.actual_value,
        metrics=payload.metrics,
        note=payload.note,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "补检失败"))
    return result
