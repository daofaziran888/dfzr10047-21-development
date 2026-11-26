"""解析指标领域服务：按窗口和来源汇总吞吐、隔离率与回退率统计，产出指标读数数据集。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.ledger import (
    batch_evidence,
    effective_quarantine,
    int_of,
    reading_chain,
    text_of,
)
from app.store import store


class ParseMetricService(BaseModuleService):
    MODULE = "parse_metric"
    ENTITY = "指标读数"
    OPERATION_TERM = "聚合"
    LIST_FIELDS = ["指标读数编号", "统计窗口", "来源渠道", "解析吞吐行数", "平均解析耗时", "隔离率", "回退率", "采集时间"]
    REQUIRED_FIELDS = ["指标读数编号", "统计窗口", "来源渠道"]
    STATUS_ORDER = ["统计中", "已结算", "待复核", "已作废"]
    ACTION_RULES = {"提交结算": "已结算", "复核读数": "待复核", "作废读数": "已作废"}
    NEGATIVE_ACTIONS = ["作废读数"]

    @staticmethod
    def _int(row: dict[str, Any], field: str) -> int:
        text = str(row.get(field) or "").strip()
        return int(text) if text.isdigit() else 0

    @staticmethod
    def _float(row: dict[str, Any], field: str) -> float:
        try:
            return float(str(row.get(field) or "").strip())
        except ValueError:
            return 0.0

    def real_quarantine_rate(self, channel: str) -> tuple[float, int, int]:
        """隔离率的历史真实计数：该来源全部隔离事件（含已回补）与批次总行数。

        返回 (隔离率, 隔离事件数, 总行数)。用于核对历史读数当时的统计口径；
        需要反映当前有效隔离时请用 effective_quarantine_rate。
        """
        intake_batches = {
            str(r.get("批次编号")) for r in store.rows("file_intake")
            if r.get("来源渠道") == channel
        }
        total_rows = 0
        for batch in store.rows("parse_batch"):
            if str(batch.get("关联投递批次")) in intake_batches:
                total_rows += self._int(batch, "总行数")
        quarantined = sum(
            1 for r in store.rows("row_quarantine")
            if str(r.get("关联批次")) in intake_batches
        )
        rate = quarantined / total_rows if total_rows > 0 else 0.0
        return round(rate, 4), quarantined, total_rows

    def effective_quarantine_rate(self, channel: str) -> tuple[float, int, int]:
        """有效隔离率：同一原行取最新一次隔离记录，已回补的不计入。

        返回 (隔离率, 有效隔离行数, 批次总行数)。重新隔离会让原行重新计入分子。
        """
        effective = effective_quarantine(channel)
        evidence = batch_evidence(channel)
        total_rows = evidence["total_rows"]
        quarantined = effective["count"]
        rate = quarantined / total_rows if total_rows > 0 else 0.0
        return round(rate, 4), quarantined, total_rows

    def real_fallback_rate(self, channel: str) -> tuple[float, int, int]:
        """回退率来自解析路由的真实回退计数。

        返回 (回退率, 发生回退的决策数, 决策总数)。
        """
        intake_batches = {
            str(r.get("批次编号")) for r in store.rows("file_intake")
            if r.get("来源渠道") == channel
        }
        decisions = [
            r for r in store.rows("parser_route")
            if str(r.get("关联批次")) in intake_batches
        ]
        fallbacks = sum(1 for r in decisions if self._int(r, "回退次数") > 0)
        rate = fallbacks / len(decisions) if decisions else 0.0
        return round(rate, 4), fallbacks, len(decisions)

    def settle_reading(self, reading_no: str, window: str, channel: str,
                       collected_at: str) -> tuple[bool, str, dict[str, Any] | None]:
        """结算指标读数：吞吐、平均耗时、隔离率、回退率全部取自真实明细。

        结算同时写入隔离分子/分母、实际隔离行号与批次凭据，
        读数不能只有百分比而没有可复核的批次依据。
        """
        intake_batches = {
            str(r.get("批次编号")) for r in store.rows("file_intake")
            if r.get("来源渠道") == channel
        }
        batches = [
            b for b in store.rows("parse_batch")
            if str(b.get("关联投递批次")) in intake_batches
        ]
        throughput = sum(self._int(b, "总行数") for b in batches)

        durations = [
            self._float(p, "解析耗时") for p in store.rows("field_parse")
            if str(p.get("关联批次")) in intake_batches
            and str(p.get("解析耗时") or "").replace(".", "").isdigit()
        ]
        avg_ms = sum(durations) / len(durations) if durations else 0.0

        quarantine_rate, quarantined, total_rows = self.effective_quarantine_rate(channel)
        fallback_rate, fallbacks, decisions = self.real_fallback_rate(channel)
        effective = effective_quarantine(channel)
        evidence = batch_evidence(channel)

        rows = store.rows(self.MODULE)
        target = next((r for r in rows if r.get("指标读数编号") == reading_no), None)
        if target is None:
            entry_id = max((int(r.get("id", 0)) for r in rows), default=0) + 1
            target = {"id": entry_id, "指标读数编号": reading_no}
            rows.append(target)
        if text_of(target.get("发布状态")) == "已发布":
            return False, (
                f"指标读数 {reading_no} 已发布为日报，不得改写；"
                f"如需调整请发起日报更正"
            ), dict(target)
        target.update({
            "统计窗口": window,
            "来源渠道": channel,
            "解析吞吐行数": str(throughput),
            "平均解析耗时": f"{avg_ms:.2f}",
            "隔离率": f"{quarantine_rate:.4f}",
            "回退率": f"{fallback_rate:.4f}",
            "采集时间": collected_at,
            "隔离行计数": str(quarantined),
            "批次总行计数": str(total_rows),
            "隔离行分子": str(quarantined),
            "隔离行分母": str(total_rows),
            "实际隔离行号": effective["row_numbers"],
            "批次凭据": evidence["text"],
            "回退决策计数": str(fallbacks),
            "路由决策计数": str(decisions),
            "发布状态": text_of(target.get("发布状态")) or "未发布",
            "status": "已结算",
            "pending": False,
            "abnormal": False,
        })
        return True, (
            f"指标读数 {reading_no} 已结算：吞吐 {throughput} 行，"
            f"隔离率 {quarantine_rate:.2%}（{quarantined}/{total_rows}），"
            f"回退率 {fallback_rate:.2%}（{fallbacks}/{decisions}），"
            f"批次凭据 {evidence['text'] or '—'}"
        ), dict(target)

    def needs_review(self, reading_no: str, reason: str) -> tuple[bool, str]:
        """标记待复核：结算读数与其他模块真实计数对不上时使用。"""
        for row in store.rows(self.MODULE):
            if row.get("指标读数编号") == reading_no:
                row["status"] = "待复核"
                row["pending"] = True
                row["abnormal"] = True
                row["复核原因"] = reason
                return True, f"指标读数 {reading_no} 已转入待复核：{reason}"
        return False, f"指标读数 {reading_no} 不存在"

    def cross_check(self, reading_no: str) -> tuple[bool, str]:
        """与坏行隔离、解析批次的真实计数交叉核对，防止估算值入库。"""
        row = next(
            (r for r in store.rows(self.MODULE)
             if r.get("指标读数编号") == reading_no), None)
        if row is None:
            return False, f"指标读数 {reading_no} 不存在"

        channel = str(row.get("来源渠道") or "")
        rate, quarantined, total_rows = self.effective_quarantine_rate(channel)
        effective = effective_quarantine(channel)
        evidence = batch_evidence(channel)

        recorded_quarantined = self._int(row, "隔离行计数")
        if not text_of(row.get("隔离行计数")):
            recorded_quarantined = self._int(row, "隔离行分子")
        if recorded_quarantined != quarantined:
            return False, (
                f"读数 {reading_no} 记录的隔离行 {recorded_quarantined} "
                f"与坏行隔离真实计数 {quarantined} 不一致，读数不得结算"
            )
        recorded_denominator = self._int(row, "隔离行分母")
        if recorded_denominator != total_rows:
            return False, (
                f"读数 {reading_no} 分母 {recorded_denominator} 与批次总行 "
                f"{total_rows} 不一致"
            )
        recorded_rate = self._float(row, "隔离率")
        if abs(recorded_rate - rate) > 0.0001:
            return False, (
                f"读数 {reading_no} 隔离率 {recorded_rate:.4f} "
                f"与真实计数 {rate:.4f}（{quarantined}/{total_rows}）不一致"
            )
        if text_of(row.get("实际隔离行号")) != effective["row_numbers"]:
            return False, (
                f"读数 {reading_no} 实际隔离行号 "
                f"{text_of(row.get('实际隔离行号')) or '—'} 与有效行号 "
                f"{effective['row_numbers'] or '无'} 不一致"
            )
        if text_of(row.get("批次凭据")) != evidence["text"]:
            return False, (
                f"读数 {reading_no} 批次凭据与真实批次记录不一致"
            )
        return True, (
            f"读数 {reading_no} 与真实计数一致：隔离 {quarantined}/{total_rows} = {rate:.4f}，"
            f"有效行号 {effective['row_numbers'] or '无'}，批次凭据 {evidence['text'] or '—'}"
        )

    def reading_history(self, reading_no: str) -> list[dict[str, Any]]:
        """同一日报的全部版本快照，历史读数保留当时分子分母与隔离行号。"""
        return [
            {
                "指标读数编号": row.get("指标读数编号"),
                "前序读数编号": row.get("前序读数编号"),
                "发布版本": row.get("发布版本"),
                "发布状态": row.get("发布状态"),
                "隔离率": row.get("隔离率"),
                "隔离行分子": row.get("隔离行分子"),
                "隔离行分母": row.get("隔离行分母"),
                "实际隔离行号": row.get("实际隔离行号"),
                "批次凭据": row.get("批次凭据"),
                "采集时间": row.get("采集时间"),
                "status": row.get("status"),
            }
            for row in reading_chain(reading_no)
        ]

    def channel_throughput(self) -> dict[str, int]:
        """按来源渠道汇总吞吐行数，供分析看板消费。"""
        totals: dict[str, int] = {}
        for row in store.rows(self.MODULE):
            if row.get("status") == "已作废":
                continue
            channel = str(row.get("来源渠道") or "")
            totals[channel] = totals.get(channel, 0) + self._int(row, "解析吞吐行数")
        return totals
