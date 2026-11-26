"""日报更正审批与回补对账接口：把坏行隔离、解析指标和审核台连成一条可复核链路。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.schemas import ActionResult
from app.services import SERVICES
from app.services.parse_metric import ParseMetricService
from app.services.reinstate_ledger import ReinstateLedgerService
from app.services.report_correction import ReportCorrectionService
from app.services.row_quarantine import RowQuarantineService
from app.store import store

router = APIRouter(prefix="/api/workflow", tags=["日报更正审批"])

quarantine_service = SERVICES["row_quarantine"]
metric_service = SERVICES["parse_metric"]
ledger_service = SERVICES["reinstate_ledger"]
correction_service = SERVICES["report_correction"]

assert isinstance(quarantine_service, RowQuarantineService)
assert isinstance(metric_service, ParseMetricService)
assert isinstance(ledger_service, ReinstateLedgerService)
assert isinstance(correction_service, ReportCorrectionService)


class CorrectionSubmit(BaseModel):
    correction_no: str
    reading_no: str
    ledger_nos: list[str] = Field(default_factory=list)
    operator: str = "值班运营"
    remark: str | None = None
    submit_time: str = "2026-11-26T00:00:00"


class ReviewerAction(BaseModel):
    reviewer: str = "日报审核员"
    action_time: str = "2026-11-26T00:00:00"
    remark: str | None = None


class RequarantineRequest(BaseModel):
    raw_text: str = ""
    failed_field: str = ""
    reason: str = "迟到结论要求重新隔离"
    quarantine_time: str = "2026-11-26T00:00:00"


@router.get("/quarantine/{quarantine_no}/correction-entry", response_model=dict)
def correction_entry(quarantine_no: str, channel: str | None = Query(default=None)) -> dict:
    """坏行隔离详情里的审批入口：可选结算读数 + 可纳入的对账记录。

    入口挂在隔离行上，运营在这里选择受影响的结算读数与对账记录发起更正。
    传入对账编号或历史隔离编号时，会解析到该原行最新的一条隔离记录。
    """
    target = quarantine_service.resolve_entry_row(quarantine_no)
    if target is None:
        raise HTTPException(status_code=404, detail=f"隔离行 {quarantine_no} 不存在")

    from app.services.ledger import reading_for_batch, text_of
    reading = reading_for_batch(str(target.get("关联批次")))
    if channel:
        resolved_channel = channel
    else:
        resolved_channel = text_of(reading.get("来源渠道")) if reading else ""

    candidates = [
        {
            "指标读数编号": row.get("指标读数编号"),
            "统计窗口": row.get("统计窗口"),
            "隔离率": row.get("隔离率"),
            "隔离行分子": row.get("隔离行分子"),
            "隔离行分母": row.get("隔离行分母"),
            "发布状态": row.get("发布状态") or "未发布",
            "status": row.get("status"),
            "selectable": str(row.get("status")) == "已结算",
        }
        for row in store.rows("parse_metric")
        if not resolved_channel or text_of(row.get("来源渠道")) == resolved_channel
    ]
    return {
        "quarantine_no": quarantine_no,
        "row_no": target.get("原始行号"),
        "channel": resolved_channel,
        "reading_candidates": candidates,
        "ledger_rows": ledger_service.available_for_correction(resolved_channel),
        "ledger_history": quarantine_service.ledger_coverage(quarantine_no),
    }


@router.post("/corrections", response_model=ActionResult)
def submit_correction(payload: CorrectionSubmit) -> ActionResult:
    ok, message, entry = correction_service.submit_correction(
        payload.correction_no, payload.reading_no, payload.ledger_nos,
        payload.operator, payload.submit_time, payload.remark or "")
    return ActionResult(ok=ok, message=message, entry=entry)


@router.post("/corrections/{correction_no}/claim", response_model=ActionResult)
def claim_correction(correction_no: str, payload: ReviewerAction) -> ActionResult:
    ok, message = correction_service.claim_candidate(
        correction_no, payload.reviewer, payload.action_time)
    entry = next(
        (row for row in store.rows("report_correction")
         if str(row.get("更正编号")) == correction_no), None)
    return ActionResult(ok=ok, message=message, entry=entry)


@router.post("/corrections/{correction_no}/publish", response_model=ActionResult)
def publish_correction(correction_no: str, payload: ReviewerAction) -> ActionResult:
    ok, message, entry = correction_service.publish_correction(
        correction_no, payload.reviewer, payload.action_time)
    return ActionResult(ok=ok, message=message, entry=entry)


@router.get("/corrections/{correction_no}/coverage", response_model=dict)
def correction_coverage(correction_no: str) -> dict:
    ok, message, detail = correction_service.coverage(correction_no)
    if not ok:
        raise HTTPException(status_code=404, detail=message)
    return {"ok": ok, "message": message, **detail}


@router.get("/review-board", response_model=dict)
def review_board(channel: str = Query(default="")) -> dict:
    """审核台：已发日报、正在审的候选、稍后重新隔离的事项三区分开。"""
    return correction_service.review_board(channel)


@router.get("/readings/{reading_no}/detail", response_model=dict)
def reading_detail(reading_no: str) -> dict:
    ok, message, detail = correction_service.reading_detail(reading_no)
    if not ok:
        raise HTTPException(status_code=404, detail=message)
    return {"ok": ok, "message": message, **detail}


@router.get("/readings/{reading_no}/history", response_model=dict)
def reading_history(reading_no: str) -> dict:
    return {
        "reading_no": reading_no,
        "history": metric_service.reading_history(reading_no),
    }


@router.get("/ledger/{ledger_no}/coverage", response_model=dict)
def ledger_coverage(ledger_no: str) -> dict:
    ok, message, detail = ledger_service.coverage(ledger_no)
    if not ok:
        raise HTTPException(status_code=404, detail=message)
    return {"ok": ok, "message": message, **detail}


@router.post("/quarantine/{quarantine_no}/requarantine", response_model=ActionResult)
def requarantine(quarantine_no: str, payload: RequarantineRequest) -> ActionResult:
    ok, message, entry = quarantine_service.requarantine_row(
        quarantine_no, payload.raw_text, payload.failed_field,
        payload.reason, payload.quarantine_time)
    return ActionResult(ok=ok, message=message, entry=entry)
