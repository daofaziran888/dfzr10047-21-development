"""日报更正共享口径：渠道批次、批次凭据、有效隔离快照与审计落库。

指标读数只给百分比时无法复核，所有更正与对账都必须落回真实的投递批次、
解析批次与隔离行号。这里集中定义这些口径，避免各服务各算一套。
"""
from __future__ import annotations

from typing import Any

from app.store import store

# 仍然占用隔离名额的状态；已回补/已废弃都不再计入有效隔离
ACTIVE_QUARANTINE_STATUSES = ("已隔离", "复核中")


def text_of(value: Any) -> str:
    return str(value or "").strip()


def int_of(row: dict[str, Any], field: str, default: int = 0) -> int:
    text = text_of(row.get(field)).lstrip("+")
    return int(text) if text.isdigit() else default


def split_list(value: Any) -> list[str]:
    return [part.strip() for part in text_of(value).split(",") if part.strip()]


def channel_batches(channel: str) -> list[str]:
    """来源渠道下的全部投递批次编号。"""
    return sorted(
        text_of(row.get("批次编号"))
        for row in store.rows("file_intake")
        if text_of(row.get("来源渠道")) == channel
    )


def batch_evidence(channel: str) -> dict[str, Any]:
    """按来源渠道汇总真实批次凭据：批次记录编号与总行数。

    已作废批次不计入分母；返回的 total_rows 是日报分母的唯一来源。
    """
    batches = channel_batches(channel)
    batch_set = set(batches)
    parts: list[str] = []
    total_rows = 0
    for batch in store.rows("parse_batch"):
        if text_of(batch.get("关联投递批次")) not in batch_set:
            continue
        if text_of(batch.get("批次状态")) == "已作废" or text_of(batch.get("status")) == "已作废":
            continue
        rows = int_of(batch, "总行数")
        total_rows += rows
        parts.append(f"{text_of(batch.get('批次记录编号'))}:{rows}")
    return {
        "batches": batches,
        "batch_records": parts,
        "total_rows": total_rows,
        "text": ",".join(parts),
    }


def quarantine_rows(channel: str) -> list[dict[str, Any]]:
    """来源渠道下全部隔离行明细（含已回补、已废弃与重新隔离记录）。"""
    batch_set = set(channel_batches(channel))
    return [
        row for row in store.rows("row_quarantine")
        if text_of(row.get("关联批次")) in batch_set
    ]


def effective_quarantine(channel: str) -> dict[str, Any]:
    """有效隔离快照：同一原始行以最新一条隔离记录为准。"""
    return _effective_of(quarantine_rows(channel))


def _effective_of(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    latest: dict[tuple[str, str], tuple[tuple[str, int], dict[str, Any]]] = {}
    for row in candidates:
        key = (text_of(row.get("关联批次")), text_of(row.get("原始行号")))
        order = (text_of(row.get("隔离时间")), int_of(row, "id"))
        current = latest.get(key)
        if current is None or order >= current[0]:
            latest[key] = (order, row)

    rows: list[int] = []
    records: list[dict[str, Any]] = []
    for _order, row in latest.values():
        if text_of(row.get("status")) not in ACTIVE_QUARANTINE_STATUSES:
            continue
        records.append(dict(row))
        row_no = text_of(row.get("原始行号"))
        if row_no.isdigit() and int(row_no) not in rows:
            rows.append(int(row_no))
    rows.sort()
    return {
        "rows": rows,
        "row_numbers": ",".join(str(row) for row in rows),
        "count": len(rows),
        "records": records,
    }


def snapshot_for_channel(channel: str) -> dict[str, Any]:
    """按来源渠道给出真实隔离分子、批次分母、有效行号与批次凭据。"""
    effective = effective_quarantine(channel)
    evidence = batch_evidence(channel)
    return {
        "channel": channel,
        "numerator": effective["count"],
        "denominator": evidence["total_rows"],
        "row_numbers": effective["row_numbers"],
        "evidence": evidence["text"],
    }


def snapshot_for_batch(intake_batch: str) -> dict[str, Any]:
    """按投递批次给出快照；能关联到日报读数时以整条渠道口径结算。"""
    reading = reading_for_batch(intake_batch)
    channel = text_of(reading.get("来源渠道")) if reading else ""
    if channel:
        return snapshot_for_channel(channel)
    batch = next(
        (row for row in store.rows("parse_batch")
         if text_of(row.get("关联投递批次")) == text_of(intake_batch)), None)
    total = int_of(batch or {}, "总行数")
    candidates = [
        row for row in store.rows("row_quarantine")
        if text_of(row.get("关联批次")) == text_of(intake_batch)
    ]
    effective = _effective_of(candidates)
    batch_no = text_of(batch.get("批次记录编号")) if batch else ""
    return {
        "channel": "",
        "numerator": effective["count"],
        "denominator": total,
        "row_numbers": effective["row_numbers"],
        "evidence": f"{batch_no}:{total}" if batch_no else "",
    }


def reading_for_batch(intake_batch: str) -> dict[str, Any] | None:
    """找到覆盖该投递批次的已发布日报读数（优先已发布版本）。"""
    published: list[dict[str, Any]] = []
    settled: list[dict[str, Any]] = []
    for reading in store.rows("parse_metric"):
        channel = text_of(reading.get("来源渠道"))
        if intake_batch not in channel_batches(channel):
            continue
        if text_of(reading.get("发布状态")) == "已发布":
            published.append(reading)
        elif text_of(reading.get("status")) == "已结算":
            settled.append(reading)
    pool = published or settled
    if not pool:
        return None
    return max(pool, key=lambda row: (text_of(row.get("采集时间")), int_of(row, "id")))


def reading_chain(reading_no: str) -> list[dict[str, Any]]:
    """按前序/更正来源追溯同一日报的全部版本，原读数排在最前。"""
    rows = store.rows("parse_metric")
    found: list[dict[str, Any]] = []
    pending = [reading_no]
    seen: set[str] = set()
    while pending:
        current = pending.pop(0)
        if current in seen:
            continue
        seen.add(current)
        for row in rows:
            number = text_of(row.get("指标读数编号"))
            if number == current:
                found.append(row)
            elif text_of(row.get("前序读数编号")) == current or text_of(row.get("更正来源前序")) == current:
                pending.append(number)
    order = {id(row): index for index, row in enumerate(found)}
    return sorted(found, key=lambda row: (
        int_of(row, "发布版本", 1), text_of(row.get("采集时间")), order[id(row)]))


def latest_published_reading(reading_no: str) -> dict[str, Any] | None:
    """同一日报链上最新的已发布读数，作为下一次更正的基准。"""
    chain = reading_chain(reading_no)
    published = [row for row in chain if text_of(row.get("发布状态")) == "已发布"]
    if published:
        return published[-1]
    original = next(
        (row for row in chain if text_of(row.get("指标读数编号")) == reading_no), None)
    return original


def quarantine_event_key(row: dict[str, Any]) -> str:
    """隔离行的动作幂等键：同一原始行同次回补只会有一个键。

    重新隔离使用独立计数，因此会得到另一个键，影响计数不会互相覆盖。
    """
    number = text_of(row.get("隔离编号"))
    if text_of(row.get("重新隔离")) in ("是", "True", "1"):
        return f"{number}:重新隔离:{int_of(row, '隔离次数', 1)}"
    if text_of(row.get("status")) == "已回补":
        return f"{number}:回补:{int_of(row, '回补次数', 1)}"
    return f"{number}:隔离:{int_of(row, '隔离次数', 1)}"


def append_audit(object_ref: str, action: str, before: str, after: str,
                 actor: str, source: str, action_time: str) -> dict[str, Any]:
    """把更正与对账动作写入投递审计，形成可封存核查的链路。"""
    rows = store.rows("intake_audit")
    entry_id = max((int_of(row, "id") for row in rows), default=0) + 1
    entry = {
        "id": entry_id, "status": "已记录", "pending": True, "abnormal": False,
        "审计编号": f"IA-{entry_id:03d}",
        "关联对象": object_ref,
        "操作类型": action,
        "变更前值": before,
        "变更后值": after,
        "操作者标识": actor or "system-auto",
        "操作来源": source,
        "操作时间": action_time,
    }
    rows.append(entry)
    return entry
