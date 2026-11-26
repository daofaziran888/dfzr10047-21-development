"""日报更正与回补对账链路测试：真实计数、冻结口径与历史留痕。"""
from __future__ import annotations

import pytest

from app.services import SERVICES
from app.store import store

CHANNEL = "sftp-node-09"
BATCH = "FI-20260903001"
READING = "PM-009"


@pytest.fixture(autouse=False)
def fresh_store():
    store.reload_seed()
    yield store
    store.reload_seed()


def ledger_rows():
    return store.rows("reinstate_ledger")


def readings():
    return store.rows("parse_metric")


def reading(number: str) -> dict:
    return next(row for row in readings() if row["指标读数编号"] == number)


class TestReinstateLedger:
    def test_reinstate_row7_records_ledger_with_snapshot(self, fresh_store):
        svc = SERVICES["row_quarantine"]
        metric = SERVICES["parse_metric"]
        assert metric.effective_quarantine_rate(CHANNEL) == pytest.approx((0.2, 2, 10))

        ok, msg, row = svc.reinstate_to_batch(
            "RQ-FI-20260903001-7", "reviewer-01", "日期格式人工确认",
            "2026-11-26T01:00:00")
        assert ok is True
        assert row["status"] == "已回补"

        # 坏行隔离的真实回补结果同步到回补对账（不能只有百分比）
        entries = [
            item for item in ledger_rows()
            if item["关联隔离编号"] == "RQ-FI-20260903001-7"
        ]
        assert len(entries) == 1
        entry = entries[0]
        assert entry["原始行号"] == "7"
        assert entry["回补动作"] == "回补批次"
        assert entry["隔离行分子"] == "1"
        assert entry["隔离行分母"] == "10"
        assert entry["实际隔离行号"] == "3"
        assert entry["批次凭据"] == "PB-009:10"
        assert entry["对账状态"] == "待更正"
        assert metric.effective_quarantine_rate(CHANNEL) == pytest.approx((0.1, 1, 10))

    def test_duplicate_backfill_only_counts_once(self, fresh_store):
        svc = SERVICES["row_quarantine"]
        ledger = SERVICES["reinstate_ledger"]
        svc.reinstate_to_batch(
            "RQ-FI-20260903001-7", "r", "确认", "2026-11-26T01:00:00")
        before = len([
            item for item in ledger_rows()
            if item["关联隔离编号"] == "RQ-FI-20260903001-7"
        ])
        # 重复提交被终态拒绝，不产生第二条影响
        ok, msg, _row = svc.reinstate_to_batch(
            "RQ-FI-20260903001-7", "r", "确认", "2026-11-26T01:01:00")
        assert ok is False
        after = len([
            item for item in ledger_rows()
            if item["关联隔离编号"] == "RQ-FI-20260903001-7"
        ])
        assert before == after == 1
        assert ledger.impact_count(BATCH, 7) == 1

    def test_same_event_key_is_idempotent(self, fresh_store):
        """同一次回补即使被重复落账也只返回已有记录，不再新增影响。"""
        ledger = SERVICES["reinstate_ledger"]
        row = {
            "隔离编号": "RQ-FI-20260903001-7",
            "关联批次": BATCH,
            "原始行号": "7",
            "status": "已回补",
            "回补次数": "1",
            "隔离次数": "1",
        }
        ok1, _msg1, first = ledger.sync_from_backfill(
            row, "r", "2026-11-26T01:00:00")
        ok2, msg2, second = ledger.sync_from_backfill(
            row, "r", "2026-11-26T01:05:00")
        assert ok1 is True and ok2 is True
        assert first["对账编号"] == second["对账编号"]
        assert "不重复计入影响" in msg2
        assert ledger.impact_count(BATCH, 7) == 1

    def test_requarantine_counts_separately_and_keeps_history(self, fresh_store):
        row_svc = SERVICES["row_quarantine"]
        ledger = SERVICES["reinstate_ledger"]
        metric = SERVICES["parse_metric"]

        row_svc.reinstate_to_batch(
            "RQ-FI-20260903001-7", "r", "确认", "2026-11-26T01:00:00")
        ok, msg, requarantined = row_svc.requarantine_row(
            "RQ-FI-20260903001-7", "", "", "迟到结论：行7仍不可用",
            "2026-11-26T03:00:00")
        assert ok is True
        assert requarantined["重新隔离"] == "是"
        assert requarantined["隔离编号"] == "RQ-FI-20260903001-7-R2"

        # 重新隔离是另一笔影响，独立计数
        assert ledger.impact_count(BATCH, 7) == 2
        assert metric.effective_quarantine_rate(CHANNEL) == pytest.approx((0.2, 2, 10))

        history = ledger.history_for_row(BATCH, 7)
        assert [item["回补动作"] for item in history] == ["回补批次", "重新隔离"]
        # 历史读数保留当时的分子分母与实际隔离行号，不因回补改写
        assert (history[0]["隔离行分子"], history[0]["隔离行分母"]) == ("1", "10")
        assert history[0]["实际隔离行号"] == "3"
        assert (history[1]["隔离行分子"], history[1]["隔离行分母"]) == ("2", "10")
        assert history[1]["实际隔离行号"] == "3,7"

    def test_same_row_requarantine_rejected_before_backfill(self, fresh_store):
        svc = SERVICES["row_quarantine"]
        ok, msg, _row = svc.requarantine_row(
            "RQ-FI-20260903001-3", "", "", "迟到结论", "t")
        assert ok is False
        assert "只有已回补的行才能重新隔离" in msg

    def test_ledger_coverage_lists_uncovered_actions(self, fresh_store):
        row_svc = SERVICES["row_quarantine"]
        ledger = SERVICES["reinstate_ledger"]
        row_svc.reinstate_to_batch(
            "RQ-FI-20260903001-7", "r", "确认", "2026-11-26T01:00:00")
        row_svc.requarantine_row(
            "RQ-FI-20260903001-7", "", "", "迟到结论", "2026-11-26T03:00:00")

        ok, msg, detail = ledger.coverage("RL-RQ-FI-20260903001-7-B1")
        assert ok is True
        assert detail["covered_count"] == 1
        uncovered = detail["uncovered_actions"]
        assert len(uncovered) == 1
        assert uncovered[0]["回补动作"] == "重新隔离"
        assert uncovered[0]["可发起更正"] is True


class TestCorrectionWorkflow:
    def _reinstate_row7(self):
        SERVICES["row_quarantine"].reinstate_to_batch(
            "RQ-FI-20260903001-7", "r", "确认", "2026-11-26T01:00:00")

    def test_submit_freezes_snapshot_and_marks_ledger(self, fresh_store):
        self._reinstate_row7()
        cf = SERVICES["report_correction"]
        ledger = SERVICES["reinstate_ledger"]
        ok, msg, candidate = cf.submit_correction(
            "RC-T1", READING, ["RL-RQ-FI-20260903001-7-B1"], "operator",
            "2026-11-26T02:00:00")
        assert ok is True
        assert candidate["更正前分子分母"] == "2/10"
        assert candidate["更正后分子分母"] == "1/10"
        assert candidate["实际隔离行号"] == "3"
        assert candidate["批次凭据"] == "PB-009:10"
        assert candidate["status"] == "待审核"
        assert ledger._find("RL-RQ-FI-20260903001-7-B1")["对账状态"] == "已纳入更正"

    def test_submit_requires_at_least_one_ledger(self, fresh_store):
        cf = SERVICES["report_correction"]
        ok, msg, candidate = cf.submit_correction(
            "RC-T0", READING, [], "operator", "t")
        assert ok is False
        assert "无法复核到原行" in msg
        assert candidate is None

    def test_duplicate_ledger_selection_counted_once(self, fresh_store):
        self._reinstate_row7()
        cf = SERVICES["report_correction"]
        ok, _msg, candidate = cf.submit_correction(
            "RC-T2", READING,
            ["RL-RQ-FI-20260903001-7-B1", "RL-RQ-FI-20260903001-7-B1"],
            "operator", "2026-11-26T02:00:00")
        assert ok is True
        assert candidate["覆盖动作数"] == "1"
        assert candidate["覆盖对账"] == "RL-RQ-FI-20260903001-7-B1"

    def test_ledger_cannot_be_covered_twice(self, fresh_store):
        self._reinstate_row7()
        cf = SERVICES["report_correction"]
        cf.submit_correction(
            "RC-T3", READING, ["RL-RQ-FI-20260903001-7-B1"], "operator", "t")
        ok, msg, _candidate = cf.submit_correction(
            "RC-T4", READING, ["RL-RQ-FI-20260903001-7-B1"], "operator", "t")
        assert ok is False
        assert "不能重复计入本次更正" in msg

    def test_stale_candidate_cannot_publish_after_requarantine(self, fresh_store):
        """审核员打开第一份候选后原行再次隔离，候选不得按旧快照发布。"""
        self._reinstate_row7()
        cf = SERVICES["report_correction"]
        row_svc = SERVICES["row_quarantine"]
        cf.submit_correction(
            "RC-T5", READING, ["RL-RQ-FI-20260903001-7-B1"], "operator",
            "2026-11-26T02:00:00")
        assert cf.claim_candidate("RC-T5", "日报审核员", "2026-11-26T02:30:00")[0] is True

        row_svc.requarantine_row(
            "RQ-FI-20260903001-7", "", "", "迟到结论", "2026-11-26T03:00:00")
        ok, msg, candidate = cf.publish_correction(
            "RC-T5", "日报审核员", "2026-11-26T03:30:00")
        assert ok is False
        assert "不一致" in msg
        assert candidate["status"] == "已失效"
        # 原已领取候选仍可查
        claimed = {row["更正编号"] for row in cf.claimed_candidates(CHANNEL)}
        assert "RC-T5" in claimed
        # 原日报未被改写
        original = reading(READING)
        assert original["隔离率"] == "0.2000"
        assert original["隔离行分子"] == "2"
        assert original["实际隔离行号"] == "3,7"

    def test_requarantine_ledger_remains_available_for_next_correction(self, fresh_store):
        self._reinstate_row7()
        cf = SERVICES["report_correction"]
        row_svc = SERVICES["row_quarantine"]
        ledger = SERVICES["reinstate_ledger"]
        cf.submit_correction(
            "RC-T6", READING, ["RL-RQ-FI-20260903001-7-B1"], "operator", "t")
        cf.claim_candidate("RC-T6", "日报审核员", "t")
        row_svc.requarantine_row(
            "RQ-FI-20260903001-7", "", "", "迟到结论", "2026-11-26T03:00:00")
        cf.publish_correction("RC-T6", "日报审核员", "2026-11-26T03:30:00")

        available = ledger.available_for_correction(CHANNEL)
        numbers = {item["对账编号"] for item in available}
        # 重新隔离独立成一条待更正事项，可发起下一次更正
        assert "RL-RQ-FI-20260903001-7-R2-Q2" in numbers
        # 原回补被重新隔离抵消，仍保留在历史里但不再要求重复更正
        assert "RL-RQ-FI-20260903001-7-B1" not in numbers
        original = ledger._find("RL-RQ-FI-20260903001-7-B1")
        assert original["对账状态"] == "已抵消"
        assert original["抵消对账编号"] == "RL-RQ-FI-20260903001-7-R2-Q2"
        assert [item["回补动作"] for item in ledger.history_for_row(BATCH, 7)] == [
            "回补批次", "重新隔离"]

    def test_new_correction_publishes_new_reading_and_keeps_original(self, fresh_store):
        self._reinstate_row7()
        cf = SERVICES["report_correction"]
        row_svc = SERVICES["row_quarantine"]
        ledger = SERVICES["reinstate_ledger"]
        metric = SERVICES["parse_metric"]

        cf.submit_correction(
            "RC-T7", READING, ["RL-RQ-FI-20260903001-7-B1"], "operator", "t1")
        cf.claim_candidate("RC-T7", "日报审核员", "t2")
        row_svc.requarantine_row(
            "RQ-FI-20260903001-7", "", "", "迟到结论", "2026-11-26T03:00:00")
        cf.publish_correction("RC-T7", "日报审核员", "t3")

        numbers = [
            item["对账编号"] for item in ledger.available_for_correction(CHANNEL)
        ]
        ok, _msg, candidate = cf.submit_correction(
            "RC-T8", READING, numbers, "operator", "2026-11-26T04:00:00")
        assert ok is True
        assert candidate["更正后分子分母"] == "2/10"
        cf.claim_candidate("RC-T8", "日报审核员", "t4")
        ok, msg, published = cf.publish_correction(
            "RC-T8", "日报审核员", "2026-11-26T04:30:00")
        assert ok is True
        assert published["发布读数编号"] == "PM-009-C1"

        # 新读数与对账行一致，不是只有百分比没有凭据
        new_reading = reading("PM-009-C1")
        assert new_reading["批次凭据"] == "PB-009:10"
        assert new_reading["实际隔离行号"] == "3,7"
        assert ledger.consistency_check("PM-009-C1")[0] is True
        # 原日报保持原样
        original = reading(READING)
        assert original["发布版本"] == "1"
        assert original["隔离行分子"] == "2"
        history = metric.reading_history(READING)
        assert [item["指标读数编号"] for item in history] == ["PM-009", "PM-009-C1"]

    def test_publish_requires_consistent_reading(self, fresh_store):
        """只有新百分比、没有真实批次凭据的读数不得发布。"""
        ledger = SERVICES["reinstate_ledger"]
        store.rows("parse_metric").append({
            "id": 99, "status": "已结算", "pending": False, "abnormal": False,
            "指标读数编号": "PM-NOBASIS", "统计窗口": "20260903",
            "来源渠道": CHANNEL, "隔离率": "0.1", "采集时间": "t",
        })
        ok, msg = ledger.consistency_check("PM-NOBASIS")
        assert ok is False
        assert "缺少真实批次凭据" in msg

    def test_review_board_separates_three_views(self, fresh_store):
        self._reinstate_row7()
        cf = SERVICES["report_correction"]
        row_svc = SERVICES["row_quarantine"]
        cf.submit_correction(
            "RC-T9", READING, ["RL-RQ-FI-20260903001-7-B1"], "operator", "t1")
        cf.claim_candidate("RC-T9", "日报审核员", "t2")
        row_svc.requarantine_row(
            "RQ-FI-20260903001-7", "", "", "迟到结论", "2026-11-26T03:00:00")
        cf.publish_correction("RC-T9", "日报审核员", "t3")

        board = cf.review_board(CHANNEL)
        assert [item["指标读数编号"] for item in board["published_reports"]] == [READING]
        assert board["candidates"] == []
        assert {item["更正编号"] for item in board["claimed_candidates"]} == {"RC-T9"}
        assert [item["回补动作"] for item in board["requarantined"]] == ["重新隔离"]

    def test_action_entry_routes_through_domain_rules(self, fresh_store):
        self._reinstate_row7()
        cf = SERVICES["report_correction"]
        cf.submit_correction(
            "RC-TA", READING, ["RL-RQ-FI-20260903001-7-B1"], "operator", "t")
        entry = next(
            row for row in store.rows("report_correction")
            if row["更正编号"] == "RC-TA")
        _entry, message = cf.run_action(entry["id"], "领取候选")
        assert "领取" in message

    def test_first_candidate_never_publishes_stale_one_tenth(self, fresh_store):
        """审核后回查：第一份候选没有发出 1/10，确认的是最新 2/10，原日报未变。"""
        self._reinstate_row7()
        cf = SERVICES["report_correction"]
        row_svc = SERVICES["row_quarantine"]
        ledger = SERVICES["reinstate_ledger"]
        metric = SERVICES["parse_metric"]

        cf.submit_correction(
            "RC-FIRST", READING, ["RL-RQ-FI-20260903001-7-B1"], "operator", "t1")
        cf.claim_candidate("RC-FIRST", "日报审核员", "t2")
        row_svc.requarantine_row(
            "RQ-FI-20260903001-7", "", "", "迟到结论", "2026-11-26T03:00:00")

        # 新旧候选都能看到
        board = cf.review_board(CHANNEL)
        open_numbers = {item["更正编号"] for item in board["candidates"]}
        assert "RC-FIRST" in open_numbers
        assert board["requarantined"][0]["回补动作"] == "重新隔离"

        # 新候选与旧候选并存，审核员只确认最新一笔
        numbers = [
            item["对账编号"] for item in ledger.available_for_correction(CHANNEL)
        ]
        ok, _msg, newest = cf.submit_correction(
            "RC-SECOND", READING, numbers, "operator", "2026-11-26T04:00:00")
        assert ok is True
        assert newest["更正后分子分母"] == "2/10"
        cf.claim_candidate("RC-SECOND", "日报审核员", "t4")
        ok, _msg, published = cf.publish_correction(
            "RC-SECOND", "日报审核员", "2026-11-26T04:30:00")
        assert ok is True

        # 第一份候选因基准变化失效，从未发出 1/10
        assert cf._find("RC-FIRST")["status"] == "已失效"
        published_numbers = {
            row["指标读数编号"] for row in store.rows("parse_metric")
            if str(row.get("发布状态")) == "已发布"
            and str(row.get("来源渠道")) == CHANNEL
        }
        assert published_numbers == {READING, published["发布读数编号"]}
        for row in store.rows("parse_metric"):
            if row.get("来源渠道") == CHANNEL:
                assert row["隔离行分子"] != "1", "不应存在 1/10 的已发布读数"
        # 最终发出的是 2/10，原日报保持 2/10 未改变
        assert published["发布读数编号"] == "PM-009-C1"
        final = reading("PM-009-C1")
        assert final["隔离行分子"] == "2"
        assert final["隔离行分母"] == "10"
        original = reading(READING)
        assert original["隔离行分子"] == "2"
        assert original["发布版本"] == "1"
        # 原已领取候选与待做更正分别可查
        claimed = {item["更正编号"] for item in cf.claimed_candidates(CHANNEL)}
        assert "RC-FIRST" in claimed
        assert cf.open_candidates(CHANNEL) == []
        assert ledger.available_for_correction(CHANNEL) == []

    def test_multiple_actions_covered_and_uncovered_left_for_next(self, fresh_store):
        """一笔更正可覆盖多条实际动作；未纳入的事项仍可发起下一次更正。"""
        row_svc = SERVICES["row_quarantine"]
        cf = SERVICES["report_correction"]
        ledger = SERVICES["reinstate_ledger"]

        row_svc.reinstate_to_batch(
            "RQ-FI-20260903001-7", "r", "确认", "2026-11-26T01:00:00")
        row_svc.reinstate_to_batch(
            "RQ-FI-20260903001-3", "r", "确认", "2026-11-26T01:10:00")
        available = [
            item["对账编号"] for item in ledger.available_for_correction(CHANNEL)
        ]
        assert set(available) == {
            "RL-RQ-FI-20260903001-7-B1", "RL-RQ-FI-20260903001-3-B1"}

        # 只纳入其中一条
        ok, _msg, candidate = cf.submit_correction(
            "RC-ONE", READING, ["RL-RQ-FI-20260903001-7-B1"], "op", "t1")
        assert ok is True
        _ok, _msg, detail = cf.coverage("RC-ONE")
        assert detail["covered_count"] == 1
        remaining = [
            item for item in detail["uncovered_actions"] if item["可发起更正"]
        ]
        assert [item["对账编号"] for item in remaining] == [
            "RL-RQ-FI-20260903001-3-B1"]

        # 未纳入的仍能发起下一次更正
        ok, _msg, second = cf.submit_correction(
            "RC-ONE-NEXT", READING, ["RL-RQ-FI-20260903001-3-B1"], "op", "t2")
        assert ok is True
        assert second["更正前分子分母"] == "2/10"
        assert second["更正后分子分母"] == "0/10"

    def test_same_requarantine_not_double_counted(self, fresh_store):
        """同一原行的重新隔离只落一条对账，重复调用重新隔离直接被拒。"""
        row_svc = SERVICES["row_quarantine"]
        ledger = SERVICES["reinstate_ledger"]
        row_svc.reinstate_to_batch(
            "RQ-FI-20260903001-7", "r", "确认", "2026-11-26T01:00:00")
        row_svc.requarantine_row(
            "RQ-FI-20260903001-7", "", "", "迟到结论", "2026-11-26T03:00:00")
        # 原行已有未消解的重新隔离，再触发会被拒，不会虚增影响
        ok, msg, _row = row_svc.requarantine_row(
            "RQ-FI-20260903001-7", "", "", "迟到结论", "2026-11-26T03:05:00")
        assert ok is False
        assert "不得重复重新隔离" in msg
        assert ledger.impact_count(BATCH, 7) == 2  # 一次回补 + 一次重新隔离

    def test_reading_detail_exposes_evidence_not_only_percentage(self, fresh_store):
        cf = SERVICES["report_correction"]
        ok, _msg, detail = cf.reading_detail(READING)
        assert ok is True
        row = detail["reading"]
        assert row["隔离行分子"] == "2"
        assert row["隔离行分母"] == "10"
        assert row["实际隔离行号"] == "3,7"
        assert row["批次凭据"] == "PB-009:10"
        assert detail["channel"] == CHANNEL

    def test_ledger_content_matches_batch_after_backfill(self, fresh_store):
        """新读数与对账行在恢复后一致，不能只有百分比没有真实批次凭据。"""
        self._reinstate_row7()
        ledger = SERVICES["reinstate_ledger"]
        metric = SERVICES["parse_metric"]
        entry = ledger._find("RL-RQ-FI-20260903001-7-B1")

        batch = next(
            row for row in store.rows("parse_batch")
            if row["批次记录编号"] == "PB-009")
        # 对账记录的回补结果直接来自批次真实计数
        assert f"成功行数 {batch['成功行数']}" in entry["回补结果"]
        assert f"隔离行数 {batch['隔离行数']}" in entry["回补结果"]
        assert entry["批次凭据"] == f"PB-009:{batch['总行数']}"
        # 对账快照与当前有效隔离一致
        numerator, denominator = entry["隔离行分子"], entry["隔离行分母"]
        assert metric.effective_quarantine_rate(CHANNEL) == pytest.approx(
            (int(numerator) / int(denominator),
             int(numerator), int(denominator)))
        self._reinstate_row7()
        cf = SERVICES["report_correction"]
        cf.submit_correction(
            "RC-TB", READING, ["RL-RQ-FI-20260903001-7-B1"], "operator", "t")
        ok, _msg, detail = cf.coverage("RC-TB")
        assert ok is True
        assert detail["covered_count"] == 1
        assert detail["covered_actions"][0]["原始行号"] == "7"
        assert detail["covered_actions"][0]["回补动作"] == "回补批次"

    def test_second_correction_versions_under_root_reading(self, fresh_store):
        """第二次更正仍挂在原日报编号下，不会叠出 PM-009-C1-C2。"""
        row_svc = SERVICES["row_quarantine"]
        cf = SERVICES["report_correction"]
        ledger = SERVICES["reinstate_ledger"]
        metric = SERVICES["parse_metric"]

        row_svc.reinstate_to_batch(
            "RQ-FI-20260903001-7", "r", "确认", "2026-11-26T01:00:00")
        cf.submit_correction(
            "RC-V1", READING, ["RL-RQ-FI-20260903001-7-B1"], "operator", "t1")
        cf.claim_candidate("RC-V1", "rev", "t2")
        cf.publish_correction("RC-V1", "rev", "t3")

        row_svc.requarantine_row(
            "RQ-FI-20260903001-7", "", "", "迟到结论", "2026-11-26T03:00:00")
        numbers = [
            item["对账编号"] for item in ledger.available_for_correction(CHANNEL)
        ]
        # 直接以已发布的更正版本为基准再发起一次
        ok, _msg, candidate = cf.submit_correction(
            "RC-V2", "PM-009-C1", numbers, "operator", "t4")
        assert ok is True
        cf.claim_candidate("RC-V2", "rev", "t5")
        ok, _msg, published = cf.publish_correction("RC-V2", "rev", "t6")
        assert ok is True
        assert published["发布读数编号"] == "PM-009-C2"
        assert [item["指标读数编号"] for item in metric.reading_history(READING)] == [
            "PM-009", "PM-009-C1", "PM-009-C2"]

    def test_publish_invalidates_other_open_candidates(self, fresh_store):
        cf = SERVICES["report_correction"]
        ledger = SERVICES["reinstate_ledger"]
        channel = "sftp-node-11"

        # seed 里 sftp-node-11 有两条待更正对账，分别发起两条候选
        cf.submit_correction(
            "RC-P1", "PM-011-C1", ["RL-RQ-FI-20260905002-6-B1"],
            "operator", "t1")
        ok, _msg, other = cf.submit_correction(
            "RC-P2", "PM-011-C1", ["RL-RQ-FI-20260905001-11-R2-Q2"],
            "operator", "t2")
        assert ok is True
        assert other["status"] == "待审核"
        assert {item["更正编号"] for item in cf.open_candidates(channel)} == {
            "RC-P1", "RC-P2"}

        cf.claim_candidate("RC-P1", "rev", "t3")
        ok, _msg, _published = cf.publish_correction("RC-P1", "rev", "t4")
        assert ok is True
        assert cf._find("RC-P2")["status"] == "已失效"
        # 失效候选覆盖的对账退回待更正，可发起下一次更正
        assert ledger._find("RL-RQ-FI-20260905001-11-R2-Q2")["对账状态"] == "待更正"


class TestMetricEvidence:
    def test_settle_writes_numerator_denominator_and_evidence(self, fresh_store):
        metric = SERVICES["parse_metric"]
        ok, msg, settled = metric.settle_reading(
            "PM-T9", "20260903", CHANNEL, "2026-11-26T00:00:00")
        assert ok is True
        assert settled["隔离行分子"] == "2"
        assert settled["隔离行分母"] == "10"
        assert settled["实际隔离行号"] == "3,7"
        assert settled["批次凭据"] == "PB-009:10"
        assert "PB-009:10" in msg

    def test_settle_rejects_published_reading(self, fresh_store):
        metric = SERVICES["parse_metric"]
        ok, msg, row = metric.settle_reading(
            "PM-009", "20260903", CHANNEL, "2026-11-26T00:00:00")
        assert ok is False
        assert "已发布为日报" in msg
        assert row["隔离率"] == "0.2000"

    def test_cross_check_detects_evidence_mismatch(self, fresh_store):
        metric = SERVICES["parse_metric"]
        metric.settle_reading("PM-T10", "20260903", CHANNEL, "t")
        row = reading("PM-T10")
        row["批次凭据"] = "PB-009:99"
        ok, msg = metric.cross_check("PM-T10")
        assert ok is False
        assert "批次凭据" in msg

    def test_cross_check_uses_effective_quarantine(self, fresh_store):
        metric = SERVICES["parse_metric"]
        metric.settle_reading("PM-T11", "20260903", CHANNEL, "t")
        SERVICES["row_quarantine"].reinstate_to_batch(
            "RQ-FI-20260903001-7", "r", "确认", "t2")
        # 回补后真实有效隔离变为 1/10，读数冻结的 2/10 不再一致
        ok, msg = metric.cross_check("PM-T11")
        assert ok is False
        assert "不一致" in msg
