"""回补对账领域服务：把坏行隔离的真实回补结果落成可复核到原行的对账记录数据集。

核心行为：
- 每次回补或重新隔离都登记一条对账记录，事件键保证同次回补重复提交只记一次
- 对账记录保留当时的隔离分子/分母、实际隔离行号与批次凭据，历史不因后续回补改写
- 同一条原行的另一笔重新隔离使用独立事件键，影响计数分开统计
- 对账记录可被日报更正引用，被引用后标记已纳入，未纳入的仍可发起下一次更正。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.ledger import (
    append_audit,
    batch_evidence,
    effective_quarantine,
    int_of,
    quarantine_event_key,
    reading_for_batch,
    snapshot_for_batch,
    text_of,
)
from app.store import store


class ReinstateLedgerService(BaseModuleService):
    MODULE = "reinstate_ledger"
    ENTITY = "回补对账记录"
    OPERATION_TERM = "核对"
    LIST_FIELDS = ["对账编号", "关联隔离编号", "关联批次", "原始行号", "回补动作", "回补结果", "隔离行分子", "隔离行分母", "实际隔离行号", "批次凭据", "对账状态", "对账时间"]
    REQUIRED_FIELDS = ["对账编号", "关联隔离编号", "关联批次", "原始行号"]
    STATUS_ORDER = ["待更正", "已纳入更正", "已发布", "已抵消", "已作废"]
    ACTION_RULES = {"标记待更正": "待更正", "登记已发布": "已发布", "作废对账": "已作废"}
    NEGATIVE_ACTIONS = ["作废对账"]

    def _find(self, ledger_no: str) -> dict[str, Any] | None:
        for row in store.rows(self.MODULE):
            if row.get("对账编号") == ledger_no:
                return row
        return None

    def resolve_ledger(self, entry_no: str) -> dict[str, Any] | None:
        """按对账编号或隔离编号定位记录，隔离编号取该原行最新一条对账。"""
        direct = self._find(entry_no)
        if direct is not None:
            return direct
        matches = [
            row for row in store.rows(self.MODULE)
            if text_of(row.get("关联隔离编号")) == entry_no
            or text_of(row.get("关联隔离编号")).startswith(f"{entry_no}-R")
        ]
        if not matches:
            return None
        return max(matches, key=lambda row: int_of(row, "id"))

    def _find_by_event_key(self, event_key: str) -> dict[str, Any] | None:
        for row in store.rows(self.MODULE):
            if row.get("事件键") == event_key:
                return row
        return None

    def _snapshot(self, intake_batch: str) -> dict[str, Any]:
        return snapshot_for_batch(intake_batch)

    @staticmethod
    def _reading_of_batch(intake_batch: str) -> dict[str, Any] | None:
        return reading_for_batch(intake_batch)

    # ------------------------------------------------------------------
    # 回补 / 重新隔离落账
    # ------------------------------------------------------------------
    def sync_from_backfill(self, quarantine: dict[str, Any], actor: str,
                           action_time: str,
                           action_label: str = "回补批次") -> tuple[bool, str, dict[str, Any] | None]:
        """回补完成后同步对账：以隔离行的真实终态与批次真实计数落账。

        同一隔离行同一次回补使用同一个事件键，重复提交只返回已有记录，
        不会重复记一次影响。
        """
        quarantine_no = text_of(quarantine.get("隔离编号"))
        batch_no = text_of(quarantine.get("关联批次"))
        row_no = text_of(quarantine.get("原始行号"))
        event_key = quarantine_event_key(quarantine)
        existing = self._find_by_event_key(event_key)
        if existing is not None:
            return True, (
                f"对账记录 {existing.get('对账编号')} 已存在，"
                f"同次回补不重复计入影响"
            ), dict(existing)

        snapshot = self._snapshot(batch_no)
        batch = next(
            (b for b in store.rows("parse_batch")
             if text_of(b.get("关联投递批次")) == batch_no), None)
        success_after = int_of(batch or {}, "成功行数")
        isolated_after = int_of(batch or {}, "隔离行数")

        rows = store.rows(self.MODULE)
        entry_id = max((int_of(row, "id") for row in rows), default=0) + 1
        if text_of(quarantine.get("重新隔离")) == "是":
            prefix = f"RL-{quarantine_no}-Q{int_of(quarantine, '隔离次数', 1)}"
            status = "待更正"
        else:
            prefix = f"RL-{quarantine_no}-B{int_of(quarantine, '回补次数', 1)}"
            status = "待更正"

        entry = {
            "id": entry_id, "status": status, "pending": True, "abnormal": False,
            "对账编号": prefix,
            "关联隔离编号": quarantine_no,
            "关联批次": batch_no,
            "原始行号": row_no,
            "回补动作": action_label,
            "动作计数": "1",
            "回补结果": (
                f"已回补：批次成功行数 {success_after}、隔离行数 {isolated_after}"
                if text_of(quarantine.get("status")) == "已回补"
                else f"重新隔离：批次成功行数 {success_after}、隔离行数 {isolated_after}"
            ),
            "隔离行分子": str(snapshot["numerator"]),
            "隔离行分母": str(snapshot["denominator"]),
            "实际隔离行号": snapshot["row_numbers"],
            "批次凭据": snapshot["evidence"],
            "对账状态": status,
            "更正来源渠道": snapshot["channel"],
            "事件键": event_key,
            "对账时间": action_time,
            "操作者标识": actor or "system-auto",
        }
        rows.append(entry)
        append_audit(
            entry["对账编号"], "回补对账", f"隔离行 {quarantine_no} 行 {row_no}",
            f"{action_label}；有效隔离 {snapshot['numerator']}/{snapshot['denominator']}"
            f"（行号 {snapshot['row_numbers'] or '无'}）",
            actor, "reinstate_ledger", action_time)
        return True, (
            f"对账记录 {entry['对账编号']} 已登记：原行 {batch_no}/{row_no}，"
            f"有效隔离 {snapshot['numerator']}/{snapshot['denominator']}"
        ), dict(entry)

    def record_requarantine(self, quarantine: dict[str, Any], actor: str,
                            action_time: str) -> tuple[bool, str, dict[str, Any] | None]:
        """重新隔离单独落一条对账记录，与之前的回补互不抵扣。

        同时把该原行尚未纳入更正的旧回补对账标记为已抵消：重新隔离才是
        当前有效状态的依据，旧回补仍保留在历史里可查，但不再作为待更正事项，
        避免同一原行被两笔对账重复要求更正。
        """
        result = self.sync_from_backfill(
            quarantine, actor, action_time, action_label="重新隔离")
        ok, msg, entry = result
        original_no = text_of(quarantine.get("原回补编号")) or text_of(
            quarantine.get("关联隔离编号"))
        if ok and original_no:
            event_key = f"{original_no}:回补:1"
            prior = self._find_by_event_key(event_key)
            if prior is None:
                prior = next(
                    (row for row in store.rows(self.MODULE)
                     if text_of(row.get("关联隔离编号")) == original_no
                     and text_of(row.get("回补动作")) == "回补批次"), None)
            if prior is not None and text_of(prior.get("对账状态")) in (
                    "待更正", "已纳入更正"):
                prior["对账状态"] = "已抵消"
                prior["status"] = "已抵消"
                prior["pending"] = False
                prior["抵消对账编号"] = text_of((entry or {}).get("对账编号"))
                prior["抵消时间"] = action_time
        return ok, msg, entry

    # ------------------------------------------------------------------
    # 覆盖范围：选中的更正覆盖了哪些实际动作
    # ------------------------------------------------------------------
    def coverage(self, ledger_no: str) -> tuple[bool, str, dict[str, Any]]:
        """查看一条对账记录覆盖的实际动作，以及同一原行尚未纳入的事项。

        未纳入的兄弟对账记录仍保持待更正，可继续发起下一次更正。
        """
        ledger = self.resolve_ledger(ledger_no)
        if ledger is None:
            return False, f"对账记录 {ledger_no} 不存在", {}
        ledger_no = text_of(ledger.get("对账编号"))
        covered = [{
            "对账编号": ledger.get("对账编号"),
            "回补动作": ledger.get("回补动作"),
            "原始行号": ledger.get("原始行号"),
            "回补结果": ledger.get("回补结果"),
            "对账时间": ledger.get("对账时间"),
        }]
        siblings = [
            row for row in store.rows(self.MODULE)
            if text_of(row.get("关联批次")) == text_of(ledger.get("关联批次"))
            and text_of(row.get("原始行号")) == text_of(ledger.get("原始行号"))
            and text_of(row.get("对账编号")) != ledger_no
        ]
        return True, (
            f"对账 {ledger_no} 覆盖动作 {[c['回补动作'] for c in covered]}，"
            f"同原行另有 {len(siblings)} 条记录未纳入本条"
        ), {
            "对账编号": ledger_no,
            "关联隔离编号": ledger.get("关联隔离编号"),
            "关联批次": ledger.get("关联批次"),
            "原始行号": ledger.get("原始行号"),
            "covered_actions": covered,
            "covered_count": len(covered),
            "uncovered_actions": [
                {
                    "对账编号": row.get("对账编号"),
                    "回补动作": row.get("回补动作"),
                    "对账状态": row.get("对账状态"),
                    "可发起更正": text_of(row.get("对账状态")) == "待更正",
                }
                for row in siblings
            ],
            "correction_no": ledger.get("关联更正编号") or "",
        }

    def available_for_correction(self, channel: str) -> list[dict[str, Any]]:
        """列出某来源渠道尚未纳入任何更正的待更正对账记录。"""
        return self.available_for_correction_for_all(channel)

    def available_for_correction_for_all(self, channel: str = "") -> list[dict[str, Any]]:
        return [
            {
                "对账编号": row.get("对账编号"),
                "原始行号": row.get("原始行号"),
                "关联批次": row.get("关联批次"),
                "回补动作": row.get("回补动作"),
                "隔离行分子": row.get("隔离行分子"),
                "隔离行分母": row.get("隔离行分母"),
            }
            for row in store.rows(self.MODULE)
            if (not channel or text_of(row.get("更正来源渠道")) == channel)
            and text_of(row.get("对账状态")) == "待更正"
        ]

    def mark_covered(self, ledger_nos: list[str], correction_no: str,
                     publish_time: str, published: bool = False) -> tuple[bool, str]:
        """把对账记录标记为已纳入更正；发布后才转为已发布终态。"""
        missing = [no for no in ledger_nos if self._find(no) is None]
        if missing:
            return False, f"对账记录 {missing} 不存在，无法纳入更正"
        for no in ledger_nos:
            ledger = self._find(no)
            if ledger is None:
                continue
            ledger["关联更正编号"] = correction_no
            # 已抵消/已作废的记录保留终态，纳入更正只推进待更正记录
            if text_of(ledger.get("对账状态")) in ("待更正", "已纳入更正"):
                ledger["对账状态"] = "已发布" if published else "已纳入更正"
                ledger["status"] = ledger["对账状态"]
            ledger["纳入时间"] = publish_time
            ledger["pending"] = not published and text_of(
                ledger.get("对账状态")) == "已纳入更正"
            ledger["abnormal"] = False
        return True, f"{len(ledger_nos)} 条对账记录已纳入更正 {correction_no}"

    # ------------------------------------------------------------------
    # 读数与对账一致性
    # ------------------------------------------------------------------
    def reading_snapshot(self, reading_no: str) -> dict[str, Any] | None:
        """按读数所属来源渠道重算有效隔离与批次凭据。"""
        reading = next(
            (r for r in store.rows("parse_metric")
             if text_of(r.get("指标读数编号")) == reading_no), None)
        if reading is None:
            return None
        channel = text_of(reading.get("来源渠道"))
        effective = effective_quarantine(channel)
        evidence = batch_evidence(channel)
        return {
            "channel": channel,
            "numerator": effective["count"],
            "denominator": evidence["total_rows"],
            "row_numbers": effective["row_numbers"],
            "evidence": evidence["text"],
        }

    def consistency_check(self, reading_no: str) -> tuple[bool, str]:
        """新读数必须与真实批次凭据、有效隔离行一致，不能只有百分比。"""
        reading = next(
            (r for r in store.rows("parse_metric")
             if text_of(r.get("指标读数编号")) == reading_no), None)
        if reading is None:
            return False, f"指标读数 {reading_no} 不存在"
        snapshot = self.reading_snapshot(reading_no)
        if snapshot is None:
            return False, f"指标读数 {reading_no} 无来源渠道，无法核对批次凭据"

        numerator = int_of(reading, "隔离行分子")
        denominator = int_of(reading, "隔离行分母")
        if not text_of(reading.get("批次凭据")):
            return False, (
                f"读数 {reading_no} 只有隔离率 {text_of(reading.get('隔离率')) or '—'}，"
                f"缺少真实批次凭据，不得发布"
            )
        if denominator != snapshot["denominator"]:
            return False, (
                f"读数 {reading_no} 分母 {denominator} 与真实批次总行 "
                f"{snapshot['denominator']} 不一致"
            )
        if numerator != snapshot["numerator"]:
            return False, (
                f"读数 {reading_no} 分子 {numerator} 与有效隔离行 "
                f"{snapshot['numerator']} 不一致（有效行号 {snapshot['row_numbers'] or '无'}）"
            )
        if text_of(reading.get("实际隔离行号")) != snapshot["row_numbers"]:
            return False, (
                f"读数 {reading_no} 记录的隔离行号 "
                f"{text_of(reading.get('实际隔离行号')) or '—'} 与真实有效行号 "
                f"{snapshot['row_numbers'] or '无'} 不一致"
            )
        if text_of(reading.get("批次凭据")) != snapshot["evidence"]:
            return False, (
                f"读数 {reading_no} 的批次凭据与真实批次记录不一致："
                f"{text_of(reading.get('批次凭据'))} ≠ {snapshot['evidence']}"
            )
        rate = numerator / denominator if denominator else 0.0
        recorded = str(reading.get("隔离率") or "")
        try:
            recorded_rate = float(recorded)
        except ValueError:
            recorded_rate = -1.0
        if abs(recorded_rate - rate) > 0.0001:
            return False, (
                f"读数 {reading_no} 隔离率 {recorded or '—'} 与分子分母 "
                f"{numerator}/{denominator}={rate:.4f} 不一致"
            )
        return True, (
            f"读数 {reading_no} 与对账一致：{numerator}/{denominator}，"
            f"有效隔离行 {snapshot['row_numbers'] or '无'}，凭据 {snapshot['evidence']}"
        )

    def history_for_row(self, intake_batch: str, row_no: int) -> list[dict[str, Any]]:
        """按原行回溯全部对账事件，回补与重新隔离各自独立留痕。"""
        events = [
            {
                "对账编号": row.get("对账编号"),
                "回补动作": row.get("回补动作"),
                "回补结果": row.get("回补结果"),
                "隔离行分子": row.get("隔离行分子"),
                "隔离行分母": row.get("隔离行分母"),
                "实际隔离行号": row.get("实际隔离行号"),
                "对账状态": row.get("对账状态"),
                "对账时间": row.get("对账时间"),
            }
            for row in store.rows(self.MODULE)
            if text_of(row.get("关联批次")) == text_of(intake_batch)
            and text_of(row.get("原始行号")) == str(row_no)
        ]
        return sorted(events, key=lambda item: text_of(item.get("对账时间")))

    def impact_count(self, intake_batch: str, row_no: int) -> int:
        """原行的真实影响次数：同次回补去重，重新隔离独立计数。"""
        keys = {
            text_of(row.get("事件键"))
            for row in store.rows(self.MODULE)
            if text_of(row.get("关联批次")) == text_of(intake_batch)
            and text_of(row.get("原始行号")) == str(row_no)
        }
        return len(keys)

