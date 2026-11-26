"""坏行隔离领域服务：归集解析失败行及其失败原因，支持复核回补与重新隔离流程，产出隔离行数据集。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.reinstate_ledger import ReinstateLedgerService
from app.store import store


class RowQuarantineService(BaseModuleService):
    MODULE = "row_quarantine"
    ENTITY = "隔离行"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["隔离编号", "关联批次", "原始行号", "失败字段", "失败原因", "原始行文本", "复核结论", "隔离时间"]
    REQUIRED_FIELDS = ["隔离编号", "关联批次", "原始行号"]
    STATUS_ORDER = ["已隔离", "复核中", "已回补", "已废弃"]
    ACTION_RULES = {"提交复核": "复核中", "回补批次": "已回补", "废弃隔离行": "已废弃"}
    NEGATIVE_ACTIONS = ["废弃隔离行"]

    # 只有这些失败原因允许人工回补，其余必须走废弃
    REINSTATABLE_REASONS = ("日期格式", "格式异常", "人工确认", "可修正")

    def __init__(self) -> None:
        self.ledger = ReinstateLedgerService()

    def quarantine_failure(self, intake_batch: str, row_num: int, raw_text: str,
                           failed_field: str, reason: str,
                           quarantine_time: str) -> tuple[bool, str, dict[str, Any] | None]:
        """登记隔离行：同一批次同一行号重复登记时拒绝，避免隔离计数虚高。"""
        rows = store.rows(self.MODULE)
        exists = any(
            r.get("关联批次") == intake_batch and str(r.get("原始行号")) == str(row_num)
            for r in rows
        )
        if exists:
            return False, (
                f"批次 {intake_batch} 行 {row_num} 已在隔离清单中，不得重复登记"
            ), None

        entry_id = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        entry = {
            "id": entry_id,
            "status": "已隔离",
            "pending": True,
            "abnormal": False,
            "隔离编号": f"RQ-{intake_batch}-{row_num}",
            "关联批次": intake_batch,
            "原始行号": str(row_num),
            "失败字段": failed_field,
            "失败原因": reason,
            "原始行文本": raw_text,
            "复核结论": "",
            "隔离时间": quarantine_time,
            "隔离次数": "1",
            "回补次数": "0",
            "重新隔离": "否",
        }
        rows.append(entry)
        return True, f"批次 {intake_batch} 行 {row_num} 已隔离（{failed_field}: {reason}）", dict(entry)

    def submit_review(self, quarantine_no: str, reviewer: str) -> tuple[bool, str]:
        """提交复核：已回补/已废弃的隔离行不能再回到复核中。"""
        for row in store.rows(self.MODULE):
            if row.get("隔离编号") != quarantine_no:
                continue
            if row.get("status") in ("已回补", "已废弃"):
                return False, (
                    f"隔离行 {quarantine_no} 已终态（{row.get('status')}），不能回到复核中"
                )
            row["status"] = "复核中"
            row["pending"] = True
            row["复核人"] = reviewer
            return True, f"隔离行 {quarantine_no} 已进入复核，复核人 {reviewer}"
        return False, f"隔离行 {quarantine_no} 不存在"

    def reinstate_to_batch(self, quarantine_no: str, reviewer: str,
                           conclusion: str, review_time: str) -> tuple[bool, str, dict[str, Any] | None]:
        """回补批次：校验失败原因是否可人工修正，回补后同步批次成功/隔离行数。"""
        target = None
        for row in store.rows(self.MODULE):
            if row.get("隔离编号") == quarantine_no:
                target = row
                break
        if target is None:
            return False, f"隔离行 {quarantine_no} 不存在", None
        if target.get("status") == "已回补":
            return False, f"隔离行 {quarantine_no} 已回补，不能重复回补", None
        if target.get("status") == "已废弃":
            return False, f"隔离行 {quarantine_no} 已废弃，不能回补", None

        reason = str(target.get("失败原因") or "")
        if not any(k in reason for k in self.REINSTATABLE_REASONS):
            return False, (
                f"隔离行 {quarantine_no} 失败原因「{reason}」不属于可人工修正范围，"
                f"只能废弃不能回补"
            ), dict(target)

        target["status"] = "已回补"
        target["pending"] = False
        target["abnormal"] = False
        target["复核结论"] = f"{reviewer}: {conclusion}"
        target["复核时间"] = review_time
        target["回补次数"] = str(int(str(target.get("回补次数") or "0")) + 1
                               if str(target.get("回补次数") or "0").isdigit() else 1)

        batch_no = target.get("关联批次")
        for batch in store.rows("parse_batch"):
            if batch.get("关联投递批次") == batch_no:
                success = str(batch.get("成功行数") or "0")
                quarantined = str(batch.get("隔离行数") or "0")
                batch["成功行数"] = str(int(success) + 1 if success.isdigit() else 1)
                batch["隔离行数"] = str(
                    max(0, int(quarantined) - 1) if quarantined.isdigit() else 0)
                if batch.get("隔离行数") == "0":
                    batch["批次状态"] = "已闭合"
                    batch["status"] = "已闭合"
                    batch["pending"] = False
                    batch["abnormal"] = False
                break

        result = dict(target)
        synced, _sync_msg, _ledger = self.ledger.sync_from_backfill(
            result, reviewer, review_time)
        if not synced:  # pragma: no cover - 落账不会因场景失败
            return True, (
                f"隔离行 {quarantine_no} 已回补进批次 {batch_no}，回补对账未登记"
            ), result

        return True, (
            f"隔离行 {quarantine_no} 已回补进批次 {batch_no}，"
            f"批次成功行数 +1、隔离行数 -1，回补对账已登记"
        ), result

    def requarantine_row(self, quarantine_no: str, raw_text: str,
                         failed_field: str, reason: str,
                         quarantine_time: str) -> tuple[bool, str, dict[str, Any] | None]:
        """重新隔离：已回补的行在迟到结论到达后再次隔离，产生独立隔离记录。

        原回补记录保持已回补不动，新记录用递增的隔离次数区分，
        批次成功行数 -1、隔离行数 +1，回补对账据此独立计数。
        """
        target = next(
            (r for r in store.rows(self.MODULE)
             if r.get("隔离编号") == quarantine_no), None)
        if target is None:
            return False, f"隔离行 {quarantine_no} 不存在", None
        if target.get("status") != "已回补":
            return False, (
                f"隔离行 {quarantine_no} 当前为「{target.get('status')}」，"
                f"只有已回补的行才能重新隔离"
            ), dict(target)

        intake_batch = str(target.get("关联批次") or "")
        row_num = str(target.get("原始行号") or "")

        # 同一原行已经有一次未消解的重新隔离时，不允许再挂一条，避免影响虚增
        live_later = next(
            (row for row in store.rows(self.MODULE)
             if row.get("关联批次") == intake_batch
             and str(row.get("原始行号")) == row_num
             and row.get("status") in ("已隔离", "复核中")
             and int(row.get("id", 0)) > int(target.get("id", 0))), None)
        if live_later is not None:
            return False, (
                f"原行 {intake_batch}/{row_num} 已有未消解的重新隔离 "
                f"{live_later.get('隔离编号')}，不得重复重新隔离"
            ), dict(live_later)

        attempts = int(str(target.get("隔离次数") or "1")) + 1
        rows = store.rows(self.MODULE)
        entry_id = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        entry = {
            "id": entry_id,
            "status": "已隔离",
            "pending": True,
            "abnormal": True,
            "隔离编号": f"{quarantine_no}-R{attempts}",
            "关联批次": intake_batch,
            "原始行号": row_num,
            "失败字段": failed_field or str(target.get("失败字段") or ""),
            "失败原因": reason,
            "原始行文本": raw_text or str(target.get("原始行文本") or ""),
            "复核结论": "",
            "隔离时间": quarantine_time,
            "隔离次数": str(attempts),
            "回补次数": "0",
            "重新隔离": "是",
            "原回补编号": quarantine_no,
        }
        rows.append(entry)

        for batch in store.rows("parse_batch"):
            if batch.get("关联投递批次") != intake_batch:
                continue
            success = str(batch.get("成功行数") or "0")
            quarantined = str(batch.get("隔离行数") or "0")
            batch["成功行数"] = str(max(0, int(success) - 1) if success.isdigit() else 0)
            batch["隔离行数"] = str(int(quarantined) + 1 if quarantined.isdigit() else 1)
            batch["批次状态"] = "存在隔离"
            batch["status"] = "存在隔离"
            batch["pending"] = True
            batch["abnormal"] = True
            break

        result = dict(entry)
        self.ledger.record_requarantine(result, "system-auto", quarantine_time)
        return True, (
            f"隔离行 {quarantine_no} 已重新隔离（第 {attempts} 次）："
            f"原行 {intake_batch}/{row_num}，批次成功行数 -1、隔离行数 +1，"
            f"重新隔离已单独登记回补对账"
        ), result

    def discard_row(self, quarantine_no: str, reviewer: str, reason: str) -> tuple[bool, str]:
        """废弃隔离行：终态，不可再回补。"""
        for row in store.rows(self.MODULE):
            if row.get("隔离编号") != quarantine_no:
                continue
            if row.get("status") == "已废弃":
                return False, f"隔离行 {quarantine_no} 已废弃"
            row["status"] = "已废弃"
            row["pending"] = False
            row["abnormal"] = True
            row["复核结论"] = f"{reviewer}: 废弃，{reason}"
            return True, f"隔离行 {quarantine_no} 已废弃：{reason}"
        return False, f"隔离行 {quarantine_no} 不存在"

    def run_action(self, entry_id: int, action: str,
                   remark: str | None = None) -> tuple[dict[str, Any] | None, str]:
        """动作入口：回补、复核、废弃与重新隔离都走领域校验并同步对账。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"隔离行 {entry_id} 不存在或已归档"
        quarantine_no = str(entry.get("隔离编号") or "")
        actor = "值班审核员"
        note = (remark or "").strip()

        if action == "提交复核":
            ok, message = self.submit_review(quarantine_no, actor)
        elif action == "回补批次":
            ok, message, _row = self.reinstate_to_batch(
                quarantine_no, actor, note or "人工确认回补", "2026-11-26T00:00:00")
        elif action == "废弃隔离行":
            ok, message = self.discard_row(quarantine_no, actor, note or "人工确认废弃")
        elif action == "重新隔离":
            ok, message, _row = self.requarantine_row(
                quarantine_no, "", "", note or "迟到结论要求重新隔离",
                "2026-11-26T00:00:00")
        else:
            return None, f"动作「{action}」不属于{self.MODULE}可执行范围"
        current = store.find(self.MODULE, entry_id)
        return (current if ok else None), message

    def ledger_coverage(self, quarantine_no: str) -> list[dict[str, Any]]:
        """该隔离行及其重新隔离记录对应的回补对账，供详情页复核。"""
        return [
            {
                "对账编号": row.get("对账编号"),
                "关联隔离编号": row.get("关联隔离编号"),
                "关联批次": row.get("关联批次"),
                "原始行号": row.get("原始行号"),
                "回补动作": row.get("回补动作"),
                "回补结果": row.get("回补结果"),
                "隔离行分子": row.get("隔离行分子"),
                "隔离行分母": row.get("隔离行分母"),
                "实际隔离行号": row.get("实际隔离行号"),
                "批次凭据": row.get("批次凭据"),
                "对账状态": row.get("对账状态"),
                "关联更正编号": row.get("关联更正编号"),
            }
            for row in store.rows("reinstate_ledger")
            if str(row.get("关联隔离编号") or "") == quarantine_no
            or str(row.get("关联隔离编号") or "").startswith(f"{quarantine_no}-R")
        ]

    def resolve_entry_row(self, quarantine_no: str) -> dict[str, Any] | None:
        """把对账上的隔离编号解析成真实隔离行，供审批入口使用。

        先按隔离编号精确匹配；对账里保留的是历史编号时，退回按批次+原行
        找到该行最新的隔离记录，保证从对账仍能打开原行的审批入口。
        """
        rows = store.rows(self.MODULE)
        exact = next(
            (row for row in rows if str(row.get("隔离编号")) == quarantine_no), None)
        if exact is not None:
            return exact
        ledger = next(
            (row for row in store.rows("reinstate_ledger")
             if str(row.get("关联隔离编号")) == quarantine_no
             or str(row.get("对账编号")) == quarantine_no), None)
        if ledger is None:
            return None
        candidates = [
            row for row in rows
            if str(row.get("关联批次")) == str(ledger.get("关联批次"))
            and str(row.get("原始行号")) == str(ledger.get("原始行号"))
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda row: int(row.get("id", 0)))

    def quarantine_stats(self, intake_batch: str) -> dict[str, int]:
        """按批次输出隔离真实计数，供解析指标结算隔离率。"""
        rows = [r for r in store.rows(self.MODULE) if r.get("关联批次") == intake_batch]
        return {
            "total": len(rows),
            "isolated": sum(1 for r in rows if r.get("status") == "已隔离"),
            "reviewing": sum(1 for r in rows if r.get("status") == "复核中"),
            "reinstated": sum(1 for r in rows if r.get("status") == "已回补"),
            "discarded": sum(1 for r in rows if r.get("status") == "已废弃"),
        }

    def unreviewed_rows(self, intake_batch: str) -> list[dict[str, Any]]:
        """未复核隔离行清单，输出契约据此阻断批次通过。"""
        return [
            {"隔离编号": r.get("隔离编号"), "原始行号": r.get("原始行号"),
             "失败字段": r.get("失败字段"), "失败原因": r.get("失败原因")}
            for r in store.rows(self.MODULE)
            if r.get("关联批次") == intake_batch
            and r.get("status") in ("已隔离", "复核中")
        ]

    def blocks_contract(self, intake_batch: str) -> tuple[bool, str]:
        """存在未复核隔离行时批次不得进入输出契约。"""
        pending = self.unreviewed_rows(intake_batch)
        if pending:
            return True, (
                f"批次 {intake_batch} 有 {len(pending)} 行隔离未复核"
                f"（行号 {[p['原始行号'] for p in pending][:5]}），输出契约不得通过"
            )
        return False, f"批次 {intake_batch} 无未复核隔离行"
