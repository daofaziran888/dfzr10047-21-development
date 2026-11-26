"""日报更正领域服务：以坏行隔离回补对账为依据发起读数更正，产出日报更正候选数据集。

核心行为：
- 运营从坏行隔离/解析指标详情进入审批入口，选择受影响的结算读数与对账记录提交更正
- 候选冻结提交时的分子分母、有效隔离行号与批次凭据，审核前不得改写
- 发布前按有效隔离批次重新核对：基准已变化（例如原行再次隔离）时拒绝发布并标记失效
- 已发布日报保持原样，更正确认另发新版本，原已领取候选与待做更正分别可查。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.ledger import (
    append_audit,
    int_of,
    snapshot_for_channel,
    text_of,
)
from app.services.reinstate_ledger import ReinstateLedgerService
from app.store import store


class ReportCorrectionService(BaseModuleService):
    MODULE = "report_correction"
    ENTITY = "日报更正"
    OPERATION_TERM = "审核"
    LIST_FIELDS = ["更正编号", "关联读数编号", "更正类型", "更正前分子分母", "更正后分子分母", "覆盖对账", "领取人", "复核动作", "提交时间"]
    REQUIRED_FIELDS = ["更正编号", "关联读数编号"]
    STATUS_ORDER = ["待审核", "已领取", "已发布", "已失效", "已退回"]
    ACTION_RULES = {
        "领取候选": "已领取",
        "确认发布": "已发布",
        "退回更正": "已退回",
        "作废更正": "已失效",
    }
    NEGATIVE_ACTIONS = ["退回更正", "作废更正"]

    def __init__(self) -> None:
        self.ledger = ReinstateLedgerService()

    def _find(self, correction_no: str) -> dict[str, Any] | None:
        for row in store.rows(self.MODULE):
            if row.get("更正编号") == correction_no:
                return row
        return None

    def _find_reading(self, reading_no: str) -> dict[str, Any] | None:
        for row in store.rows("parse_metric"):
            if text_of(row.get("指标读数编号")) == text_of(reading_no):
                return row
        return None

    @staticmethod
    def _basis_text(snapshot: dict[str, Any]) -> str:
        return f"{snapshot['numerator']}/{snapshot['denominator']}"

    def _supersede_open_candidates(self, channel: str, keep_no: str,
                                   reason: str, action_time: str,
                                   keep_ledgers: set[str] | None = None) -> list[str]:
        """更正发布后，把同渠道其他未发布的候选标记失效，但保留可查。

        被失效候选覆盖的对账记录退回待更正，仍能发起下一次更正；
        本次已发布的更正覆盖的对账不退回，避免重复计数。
        """
        reserved = keep_ledgers or set()
        touched: list[str] = []
        for row in store.rows(self.MODULE):
            if text_of(row.get("更正编号")) == keep_no:
                continue
            if text_of(row.get("来源渠道")) != channel:
                continue
            if text_of(row.get("status")) not in ("待审核", "已领取"):
                continue
            row["status"] = "已失效"
            row["pending"] = False
            row["abnormal"] = True
            row["复核动作"] = reason
            row["失效时间"] = action_time
            touched.append(text_of(row.get("更正编号")))
            for ledger_no in str(row.get("覆盖对账") or "").split(","):
                no = ledger_no.strip()
                if not no or no in reserved:
                    continue
                ledger = self.ledger._find(no)
                if ledger is not None and text_of(ledger.get("对账状态")) == "已纳入更正":
                    ledger["对账状态"] = "待更正"
                    ledger["status"] = "待更正"
                    ledger["pending"] = True
                    ledger["关联更正编号"] = ""
        return touched

    # ------------------------------------------------------------------
    # 提交更正
    # ------------------------------------------------------------------
    def submit_correction(self, correction_no: str, reading_no: str,
                          ledger_nos: list[str], operator: str,
                          submit_time: str, remark: str = "") \
            -> tuple[bool, str, dict[str, Any] | None]:
        """以选中结算读数与对账记录发起日报更正候选。

        对账记录必须尚未纳入其他更正；同一原行的同次回补重复提交只计一次影响，
        已纳入的记录不会重复出现在候选的覆盖清单里。
        """
        reading = self._find_reading(reading_no)
        if reading is None:
            return False, f"指标读数 {reading_no} 不存在，无法发起更正", None
        if text_of(reading.get("status")) == "已作废":
            return False, f"指标读数 {reading_no} 已作废，不能作为更正基准", None
        if any(text_of(row.get("更正编号")) == correction_no
               for row in store.rows(self.MODULE)):
            return False, f"更正 {correction_no} 已存在，不得重复提交", None

        channel = text_of(reading.get("来源渠道"))
        requested = [no.strip() for no in ledger_nos if no.strip()]
        if not requested:
            return False, "未选择任何回补对账记录，更正无法复核到原行", None
        # 去重：同一对账记录重复勾选只计一次
        unique: list[str] = []
        for no in requested:
            if no not in unique:
                unique.append(no)

        ledger_rows: list[dict[str, Any]] = []
        for no in unique:
            ledger = self.ledger._find(no)
            if ledger is None:
                return False, f"对账记录 {no} 不存在，更正无法复核到原行", None
            if text_of(ledger.get("更正来源渠道")) != channel:
                return False, (
                    f"对账记录 {no} 属于来源 {ledger.get('更正来源渠道') or '—'}，"
                    f"与读数 {reading_no} 的来源 {channel} 不一致"
                ), None
            if text_of(ledger.get("对账状态")) in ("已纳入更正", "已发布"):
                return False, (
                    f"对账记录 {no} 已纳入更正 {ledger.get('关联更正编号') or '—'}，"
                    f"不能重复计入本次更正"
                ), None
            ledger_rows.append(ledger)

        snapshot = snapshot_for_channel(channel)
        before_numerator = int_of(reading, "隔离行分子")
        before_denominator = int_of(reading, "隔离行分母")
        if not text_of(reading.get("发布状态")) == "已发布":
            # 未发布读数的历史口径取自身记录
            before_numerator = int_of(reading, "隔离行计数") or before_numerator
            before_denominator = int_of(reading, "批次总行计数") or before_denominator

        rows = store.rows(self.MODULE)
        entry_id = max((int_of(row, "id") for row in rows), default=0) + 1
        entry = {
            "id": entry_id, "status": "待审核", "pending": True, "abnormal": False,
            "更正编号": correction_no,
            "关联读数编号": reading_no,
            "前序读数编号": text_of(reading.get("指标读数编号")),
            "更正类型": "隔离回补更正",
            "来源渠道": channel,
            "统计窗口": text_of(reading.get("统计窗口")),
            "更正前分子分母": f"{before_numerator}/{before_denominator}",
            "更正后分子分母": self._basis_text(snapshot),
            "更正前隔离率": text_of(reading.get("隔离率")),
            "更正后隔离率": (
                f"{snapshot['numerator'] / snapshot['denominator']:.4f}"
                if snapshot["denominator"] else "0.0000"
            ),
            "覆盖对账": ",".join(unique),
            "覆盖动作数": str(len(unique)),
            "实际隔离行号": snapshot["row_numbers"],
            "批次凭据": snapshot["evidence"],
            "领取人": "",
            "复核动作": "",
            "提交人": operator or "system-auto",
            "提交时间": submit_time,
            "备注": remark,
            "关联失效候选": "",
        }
        rows.append(entry)
        self.ledger.mark_covered(unique, correction_no, submit_time)
        append_audit(
            correction_no, "日报更正提交",
            f"读数 {reading_no} 原 {entry['更正前分子分母']}",
            f"候选 {entry['更正后分子分母']}，覆盖对账 {entry['覆盖对账']}",
            operator, "report_correction", submit_time)
        message = (
            f"日报更正 {correction_no} 已提交：读数 {reading_no} "
            f"{entry['更正前分子分母']} → {entry['更正后分子分母']}，"
            f"覆盖 {len(unique)} 条对账记录"
        )
        return True, message, dict(entry)

    # ------------------------------------------------------------------
    # 领取 / 发布
    # ------------------------------------------------------------------
    def claim_candidate(self, correction_no: str, reviewer: str,
                        claim_time: str = "") -> tuple[bool, str]:
        """审核员领取候选：已领取的候选不能被他人在未复核前重复领取。"""
        candidate = self._find(correction_no)
        if candidate is None:
            return False, f"更正 {correction_no} 不存在"
        if text_of(candidate.get("status")) == "已发布":
            return False, f"更正 {correction_no} 已发布，不能再领取"
        if text_of(candidate.get("status")) == "已失效":
            return False, f"更正 {correction_no} 已失效，不能再领取"
        if text_of(candidate.get("status")) == "已退回":
            return False, f"更正 {correction_no} 已退回，不能再领取"
        claimed = text_of(candidate.get("领取人"))
        if claimed and claimed != reviewer:
            return False, (
                f"更正 {correction_no} 已由 {claimed} 领取，未复核前不得重复领取"
            )
        candidate["status"] = "已领取"
        candidate["pending"] = True
        candidate["领取人"] = reviewer
        candidate["领取时间"] = claim_time or text_of(candidate.get("领取时间"))
        return True, f"更正 {correction_no} 已由 {reviewer} 领取，等待确认发布"

    def publish_correction(self, correction_no: str, reviewer: str,
                           publish_time: str) \
            -> tuple[bool, str, dict[str, Any] | None]:
        """确认发布：先按有效隔离批次复核基准，只有真实一致才写入日报。

        若候选冻结后原行再次隔离，基准分子分母会变化，本次候选不得按旧快照发布；
        候选转为已失效，需基于最新对账重新发起更正。
        """
        candidate = self._find(correction_no)
        if candidate is None:
            return False, f"更正 {correction_no} 不存在", None
        if text_of(candidate.get("status")) == "已发布":
            return False, f"更正 {correction_no} 已发布，不能重复发布", dict(candidate)
        if text_of(candidate.get("status")) == "已失效":
            return False, f"更正 {correction_no} 已失效，不能发布", dict(candidate)
        if text_of(candidate.get("status")) == "已退回":
            return False, f"更正 {correction_no} 已退回，不能发布", dict(candidate)

        channel = text_of(candidate.get("来源渠道"))
        current = snapshot_for_channel(channel)
        frozen = text_of(candidate.get("更正后分子分母"))
        if frozen != self._basis_text(current):
            candidate["status"] = "已失效"
            candidate["pending"] = False
            candidate["abnormal"] = True
            candidate["复核动作"] = (
                f"发布前复核发现基准已变化：候选冻结 {frozen}，"
                f"当前有效隔离 {self._basis_text(current)}"
            )
            for ledger_no in str(candidate.get("覆盖对账") or "").split(","):
                ledger = self.ledger._find(ledger_no.strip())
                if ledger is not None and text_of(ledger.get("对账状态")) == "已纳入更正":
                    ledger["对账状态"] = "待更正"
                    ledger["status"] = "待更正"
                    ledger["pending"] = True
                    ledger["关联更正编号"] = ""
            append_audit(
                correction_no, "日报更正失效", f"候选冻结 {frozen}",
                f"当前有效隔离 {self._basis_text(current)}，候选未发布",
                reviewer, "report_correction", publish_time)
            return False, (
                f"更正 {correction_no} 未发布：候选冻结口径 {frozen} 与当前有效隔离 "
                f"{self._basis_text(current)}（行号 {current['row_numbers'] or '无'}）不一致，"
                f"候选已失效，请基于最新回补对账重新发起更正"
            ), dict(candidate)

        # 覆盖的对账记录必须仍然真实存在
        covered = [
            no.strip() for no in str(candidate.get("覆盖对账") or "").split(",")
            if no.strip()
        ]
        missing = [no for no in covered if self.ledger._find(no) is None]
        if missing:
            return False, f"覆盖的对账记录 {missing} 已不存在，候选不得发布", dict(candidate)

        base_no = text_of(candidate.get("关联读数编号"))
        base = self._find_reading(base_no)
        if base is None:
            return False, f"基准读数 {base_no} 不存在", dict(candidate)

        # 沿前序链找到根读数，版本号始终挂在原日报编号下，避免编号越接越长
        root = base
        seen: set[str] = set()
        while text_of(root.get("前序读数编号")) and text_of(root.get("前序读数编号")) not in seen:
            seen.add(text_of(root.get("指标读数编号")))
            parent = self._find_reading(text_of(root.get("前序读数编号")))
            if parent is None:
                break
            root = parent
        root_no = text_of(root.get("指标读数编号"))

        # 已发布日报保持原样，更正确认另发新版本
        history = [
            row for row in store.rows("parse_metric")
            if text_of(row.get("指标读数编号")) == root_no
            or text_of(row.get("前序读数编号")) == root_no
        ]
        version = max((int_of(row, "发布版本", 1) for row in history), default=1) + 1
        new_no = f"{root_no}-C{version - 1}"
        rows = store.rows("parse_metric")
        entry_id = max((int_of(row, "id") for row in rows), default=0) + 1
        reading = {
            "id": entry_id, "status": "已结算", "pending": False, "abnormal": False,
            "指标读数编号": new_no,
            "前序读数编号": base_no,
            "根读数编号": root_no,
            "关联更正编号": correction_no,
            "统计窗口": text_of(candidate.get("统计窗口")),
            "来源渠道": channel,
            "解析吞吐行数": text_of(base.get("解析吞吐行数")),
            "平均解析耗时": text_of(base.get("平均解析耗时")),
            "隔离率": candidate.get("更正后隔离率"),
            "回退率": text_of(base.get("回退率")),
            "采集时间": publish_time,
            "隔离行计数": current["numerator"],
            "批次总行计数": current["denominator"],
            "隔离行分子": str(current["numerator"]),
            "隔离行分母": str(current["denominator"]),
            "实际隔离行号": current["row_numbers"],
            "批次凭据": current["evidence"],
            "发布版本": str(version),
            "发布状态": "已发布",
            "发布人": reviewer,
        }
        rows.append(reading)

        ok, msg = self.ledger.consistency_check(new_no)
        if not ok:
            # 不应发生：发布前已冻结同一快照；出现即回滚，不落地不一致读数
            rows.remove(reading)
            return False, f"更正 {correction_no} 未发布：{msg}", dict(candidate)

        candidate["status"] = "已发布"
        candidate["pending"] = False
        candidate["abnormal"] = False
        candidate["复核人"] = reviewer
        candidate["发布读数编号"] = new_no
        candidate["复核动作"] = f"确认发布：日报更新为 {candidate.get('更正后分子分母')}"
        candidate["发布时间"] = publish_time
        self.ledger.mark_covered(covered, correction_no, publish_time, published=True)

        # 同一渠道的其它未发布候选随新版本发布而失效，但保留可查
        superseded = self._supersede_open_candidates(
            channel, correction_no,
            f"同渠道日报已发布 {new_no}，本候选不再作为发布依据",
            publish_time, set(covered))

        append_audit(
            correction_no, "日报更正发布",
            f"原日报 {base_no} 保持 {text_of(base.get('隔离率'))}",
            f"新读数 {new_no} 隔离率 {candidate.get('更正后隔离率')}"
            f"（{candidate.get('更正后分子分母')}），凭据 {current['evidence']}",
            reviewer, "report_correction", publish_time)
        message = (
            f"更正 {correction_no} 已发布：新读数 {new_no} "
            f"隔离 {candidate.get('更正后分子分母')}，"
            f"有效行号 {current['row_numbers'] or '无'}，原日报 {base_no} 未改变"
        )
        if superseded:
            message += f"；同渠道旧候选 {superseded} 已标记失效"
        return True, message, dict(candidate)

    # ------------------------------------------------------------------
    # 复核视角：已发日报 / 正在审的候选 / 稍后重新隔离的事项
    # ------------------------------------------------------------------
    def published_reports(self, channel: str = "") -> list[dict[str, Any]]:
        """已发布的日报版本，按版本时间升序，原日报排在最前。"""
        rows = [
            row for row in store.rows("parse_metric")
            if text_of(row.get("发布状态")) == "已发布"
            and (not channel or text_of(row.get("来源渠道")) == channel)
        ]
        return sorted(rows, key=lambda row: (
            text_of(row.get("来源渠道")), int_of(row, "发布版本", 1),
            text_of(row.get("采集时间"))))

    def claimed_candidates(self, channel: str = "") -> list[dict[str, Any]]:
        """原已领取但未发布的候选，单独可查（即使后来失效）。"""
        return [
            row for row in store.rows(self.MODULE)
            if text_of(row.get("领取人"))
            and text_of(row.get("status")) in ("已领取", "已失效", "已退回")
            and (not channel or text_of(row.get("来源渠道")) == channel)
        ]

    def open_candidates(self, channel: str = "") -> list[dict[str, Any]]:
        return [
            row for row in store.rows(self.MODULE)
            if text_of(row.get("status")) in ("待审核", "已领取")
            and (not channel or text_of(row.get("来源渠道")) == channel)
        ]

    def pending_corrections(self, channel: str = "") -> list[dict[str, Any]]:
        """仍待处理的更正：未纳入任何已发布更正的对账事项。"""
        return self.ledger.available_for_correction_for_all(channel)

    def requarantined_matters(self, channel: str = "") -> list[dict[str, Any]]:
        """稍后重新隔离的事项：对账中动作类型为重新隔离的记录。"""
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
                "对账状态": row.get("对账状态"),
                "对账时间": row.get("对账时间"),
            }
            for row in store.rows("reinstate_ledger")
            if text_of(row.get("回补动作")) == "重新隔离"
            and (not channel or text_of(row.get("更正来源渠道")) == channel)
        ]

    def review_board(self, channel: str = "") -> dict[str, Any]:
        """审核界面三分区：已发日报、正在审的候选、稍后重新隔离的事项。"""
        return {
            "channel": channel,
            "published_reports": self.published_reports(channel),
            "candidates": self.open_candidates(channel),
            "claimed_candidates": self.claimed_candidates(channel),
            "requarantined": self.requarantined_matters(channel),
            "pending_ledger": self.ledger.available_for_correction_for_all(channel),
        }

    def coverage(self, correction_no: str) -> tuple[bool, str, dict[str, Any]]:
        """所选更正覆盖了哪些实际动作，以及同原行未纳入、可再发起更正的事项。"""
        candidate = self._find(correction_no)
        if candidate is None:
            return False, f"更正 {correction_no} 不存在", {}
        covered: list[dict[str, Any]] = []
        for no in str(candidate.get("覆盖对账") or "").split(","):
            ledger = self.ledger._find(no.strip())
            if ledger is None:
                continue
            covered.append({
                "对账编号": ledger.get("对账编号"),
                "回补动作": ledger.get("回补动作"),
                "原始行号": ledger.get("原始行号"),
                "回补结果": ledger.get("回补结果"),
                "对账状态": ledger.get("对账状态"),
                "对账时间": ledger.get("对账时间"),
            })
        uncovered = [
            {
                "对账编号": row.get("对账编号"),
                "回补动作": row.get("回补动作"),
                "原始行号": row.get("原始行号"),
                "对账状态": row.get("对账状态"),
                "可发起更正": text_of(row.get("对账状态")) == "待更正",
            }
            for row in store.rows("reinstate_ledger")
            if text_of(row.get("更正来源渠道")) == text_of(candidate.get("来源渠道"))
            and text_of(row.get("对账编号")) not in str(candidate.get("覆盖对账") or "")
        ]
        return True, (
            f"更正 {correction_no} 覆盖 {len(covered)} 个实际动作，"
            f"另有 {sum(1 for item in uncovered if item['可发起更正'])} 项待更正可继续发起"
        ), {
            "更正编号": correction_no,
            "关联读数编号": candidate.get("关联读数编号"),
            "更正前分子分母": candidate.get("更正前分子分母"),
            "更正后分子分母": candidate.get("更正后分子分母"),
            "covered_actions": covered,
            "covered_count": len(covered),
            "uncovered_actions": uncovered,
        }

    def reading_history(self, reading_no: str) -> list[dict[str, Any]]:
        """读数版本历史：历史读数保留提交当时的分子分母与实际隔离行号。"""
        from app.services.parse_metric import ParseMetricService
        return ParseMetricService().reading_history(reading_no)

    def reading_detail(self, reading_no: str) -> tuple[bool, str, dict[str, Any]]:
        """指标详情：不只给百分比，同时给出批次凭据与关联的对账原行。"""
        reading = self._find_reading(reading_no)
        if reading is None:
            return False, f"指标读数 {reading_no} 不存在", {}
        channel = text_of(reading.get("来源渠道"))
        ledger_rows = [
            {
                "对账编号": row.get("对账编号"),
                "原始行号": row.get("原始行号"),
                "回补动作": row.get("回补动作"),
                "对账状态": row.get("对账状态"),
                "关联更正编号": row.get("关联更正编号"),
            }
            for row in store.rows("reinstate_ledger")
            if text_of(row.get("更正来源渠道")) == channel
        ]
        return True, f"读数 {reading_no} 详情", {
            "reading": dict(reading),
            "channel": channel,
            "published_reports": self.published_reports(channel),
            "ledger_rows": ledger_rows,
            "available_ledger": self.ledger.available_for_correction(channel),
            "history": self.reading_history(reading_no),
        }

    def run_action(self, entry_id: int, action: str) \
            -> tuple[dict[str, Any] | None, str]:
        """通用动作入口：领取/发布/退回都走领域校验，不允许直接改状态。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"日报更正 {entry_id} 不存在或已归档"
        correction_no = text_of(entry.get("更正编号"))
        if action == "领取候选":
            ok, message = self.claim_candidate(correction_no, "值班审核员")
        elif action == "确认发布":
            ok, message, _entry = self.publish_correction(
                correction_no, "值班审核员", "2026-11-26T00:00:00")
        elif action == "退回更正":
            if text_of(entry.get("status")) == "已发布":
                return None, f"更正 {correction_no} 已发布，不能退回"
            entry["status"] = "已退回"
            entry["pending"] = False
            entry["abnormal"] = True
            entry["复核动作"] = "退回更正"
            ok, message = True, f"更正 {correction_no} 已退回"
        elif action == "作废更正":
            entry["status"] = "已失效"
            entry["pending"] = False
            entry["abnormal"] = True
            entry["复核动作"] = "作废更正"
            ok, message = True, f"更正 {correction_no} 已失效"
        else:
            return None, f"动作「{action}」不属于{self.MODULE}可执行范围"
        return (store.find(self.MODULE, entry_id) if ok else None), message
