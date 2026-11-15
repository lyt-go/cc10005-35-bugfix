"""监控面板生命周期与限速规则绑定的回归测试。

运行：cd backend && .venv/bin/python -m unittest app.tests.test_lifecycle -v
不依赖第三方库，直接在 service/store 层验证状态机、事务与并发口径。
"""
from __future__ import annotations

import threading
import unittest

from app.services import binding
from app.services.monitor import MonitorService
from app.services.throttle import ThrottleService
from app.store import store


class LifecycleTestCase(unittest.TestCase):
    def setUp(self) -> None:
        store.reset()
        self.monitor = MonitorService()
        self.throttle = ThrottleService()
        self.moni1 = store.find_by_code("monitor", "面板编号", "MONI-0001")
        self.moni2 = store.find_by_code("monitor", "面板编号", "MONI-0002")
        self.moni3 = store.find_by_code("monitor", "面板编号", "MONI-0003")
        self.thro2 = store.find_by_code("throttle", "规则编号", "THRO-0002")
        self.thro3 = store.find_by_code("throttle", "规则编号", "THRO-0003")

    # ---- MONI-0001：归档传播 ------------------------------------------------

    def test_archive_stops_alerts_and_cascades_in_one_commit(self) -> None:
        """归档面板：停止采样、保留窗口/通知组，引用规则同事务进入待复核。"""
        panel, message, related = self.monitor.run_action(self.moni1["id"], "归档面板")
        self.assertIsNotNone(panel)
        self.assertEqual(panel["status"], "已归档")
        self.assertFalse(panel["采样中"], "归档面板必须停止采样")
        self.assertTrue(panel["最后告警窗口"], "最后告警窗口必须保留")
        self.assertTrue(panel["最后通知组"], "通知组必须保留")
        # 级联结果随同一动作结果返回
        self.assertEqual([r["规则编号"] for r in related], ["THRO-0002"])
        rule = store.find_by_code("throttle", "规则编号", "THRO-0002")
        self.assertEqual(rule["status"], "待复核")
        self.assertTrue(rule["绑定失效"])
        self.assertEqual(rule["绑定面板编号"], "MONI-0001")
        self.assertEqual(rule["绑定面板版本"], 1)

    def test_archive_is_idempotent_only_first_succeeds(self) -> None:
        """重复归档：只允许第一个提交成功。"""
        first, _, related = self.monitor.run_action(self.moni1["id"], "归档面板")
        self.assertIsNotNone(first)
        self.assertEqual(len(related), 1)
        second, message, _ = self.monitor.run_action(self.moni1["id"], "归档面板")
        self.assertIsNone(second)
        self.assertIn("已归档", message)
        # 终态后任何动作都被拒绝
        third, _, _ = self.monitor.run_action(self.moni1["id"], "启用面板")
        self.assertIsNone(third)

    def test_archive_transaction_rolls_back_together(self) -> None:
        """级联阶段抛错时面板与规则必须整体回滚（同一提交语义）。"""
        original = binding.cascade_archive

        def boom(panel):  # type: ignore[no-untyped-def]
            raise RuntimeError("模拟级联更新失败")

        binding.cascade_archive = boom
        try:
            with self.assertRaises(RuntimeError):
                self.monitor.run_action(self.moni1["id"], "归档面板")
        finally:
            binding.cascade_archive = original
        panel = store.find_by_code("monitor", "面板编号", "MONI-0001")
        rule = store.find_by_code("throttle", "规则编号", "THRO-0002")
        self.assertEqual(panel["status"], "已启用")
        self.assertTrue(panel["采样中"])
        self.assertEqual(rule["status"], "已启用")
        self.assertFalse(rule["绑定失效"])

    def test_archived_panel_cannot_bind_new_enable(self) -> None:
        """归档面板不能作为任何规则的新绑定版本。"""
        self.monitor.run_action(self.moni1["id"], "归档面板")
        rule = store.find_by_code("throttle", "规则编号", "THRO-0002")
        # 待复核状态尝试引用已归档的旧面板 -> 拒绝
        entry, message = self.throttle.run_action(
            rule["id"], "启用规则", {"绑定面板编号": "MONI-0001"}
        )
        self.assertIsNone(entry)
        self.assertIn("已归档", message)

    def test_rebind_requires_new_panel_version_and_snapshot(self) -> None:
        """待复核规则重新启用必须绑定新面板编号，并快照其版本与错误率阈值。"""
        self.monitor.run_action(self.moni1["id"], "归档面板")
        rule = store.find_by_code("throttle", "规则编号", "THRO-0002")
        self.assertEqual(rule["status"], "待复核")
        # 缺少绑定面板编号也不能启用
        entry, message = self.throttle.run_action(rule["id"], "启用规则", {})
        self.assertIsNone(entry)
        self.assertIn("必须引用监控面板编号", message)
        # 引用新面板 MONI-0003（阈值 12.0）启用成功
        entry, message = self.throttle.run_action(
            rule["id"], "启用规则", {"绑定面板编号": "MONI-0003"}
        )
        self.assertIsNotNone(entry)
        self.assertEqual(entry["status"], "已启用")
        self.assertEqual(entry["绑定面板编号"], "MONI-0003")
        self.assertEqual(entry["绑定面板版本"], 1)
        self.assertEqual(entry["绑定错误率阈值"], 12.0)
        self.assertFalse(entry["绑定失效"])

    # ---- MONI-0002 / THRO-0002：跳级启用 -----------------------------------

    def test_closed_rule_cannot_skip_review_to_enable(self) -> None:
        """已关闭规则不能绕过评审直接启用，必须先走评审再绑定新面板。"""
        # 先把 THRO-0002 正常关闭
        self.throttle.run_action(self.thro2["id"], "关闭规则")
        rule = store.find_by_code("throttle", "规则编号", "THRO-0002")
        self.assertEqual(rule["status"], "已关闭")
        # 直接启用 = 跳级，必须失败
        entry, message = self.throttle.run_action(
            rule["id"], "启用规则", {"绑定面板编号": "MONI-0003"}
        )
        self.assertIsNone(entry)
        self.assertIn("禁止跳级", message)
        # 正确路径：重新评审 -> 绑定新面板启用
        entry, _ = self.throttle.run_action(rule["id"], "提交规则评审", {})
        self.assertEqual(entry["status"], "规则评审中")
        entry, _ = self.throttle.run_action(
            rule["id"], "启用规则", {"绑定面板编号": "MONI-0003"}
        )
        self.assertEqual(entry["status"], "已启用")
        self.assertEqual(entry["绑定面板编号"], "MONI-0003")

    def test_review_with_still_active_panel_keeps_binding_valid(self) -> None:
        """关闭后重走评审，若绑定面板仍有效，绑定不应被误判失效，可重新引用启用。"""
        rule_id = self.thro2["id"]  # THRO-0002 绑定 MONI-0001（仍已启用）
        self.throttle.run_action(rule_id, "关闭规则")
        entry, _ = self.throttle.run_action(rule_id, "提交规则评审", {})
        self.assertFalse(entry["绑定失效"])
        entry, message = self.throttle.run_action(
            rule_id, "启用规则", {"绑定面板编号": "MONI-0001"}
        )
        self.assertIsNotNone(entry, message)
        self.assertEqual(entry["status"], "已启用")
        self.assertEqual(entry["绑定面板编号"], "MONI-0001")

    def test_draft_cannot_skip_review_to_enable(self) -> None:
        """草稿规则同样不能直接启用。"""
        thro1 = store.find_by_code("throttle", "规则编号", "THRO-0001")
        entry, message = self.throttle.run_action(
            thro1["id"], "启用规则", {"绑定面板编号": "MONI-0003"}
        )
        self.assertIsNone(entry)
        self.assertIn("禁止跳级", message)

    def test_reviewing_panel_cannot_be_bound(self) -> None:
        """MONI-0002 处于面板评审中，不能被限速规则引用启用。"""
        self.throttle.run_action(self.thro2["id"], "关闭规则")
        rule = store.find_by_code("throttle", "规则编号", "THRO-0002")
        self.throttle.run_action(rule["id"], "提交规则评审", {})
        entry, message = self.throttle.run_action(
            rule["id"], "启用规则", {"绑定面板编号": "MONI-0002"}
        )
        self.assertIsNone(entry)
        self.assertIn("面板评审中", message)

    def test_threshold_mismatch_rejected(self) -> None:
        """提交的错误率阈值与面板版本不一致时拒绝，避免告警条件与实际速率脱节。"""
        rule = store.find_by_code("throttle", "规则编号", "THRO-0002")
        self.throttle.run_action(rule["id"], "关闭规则")
        self.throttle.run_action(rule["id"], "提交规则评审", {})
        entry, message = self.throttle.run_action(
            rule["id"],
            "启用规则",
            {"绑定面板编号": "MONI-0003", "错误率阈值": 99.5},
        )
        self.assertIsNone(entry)
        self.assertIn("不一致", message)

    def test_concurrent_enable_only_one_succeeds(self) -> None:
        """并发启用同一条规则：只允许一个提交成功，其余因状态前置校验失败。"""
        rule_id = self.thro2["id"]
        # 先让规则进入评审中（待启用）
        self.throttle.run_action(rule_id, "关闭规则")
        self.throttle.run_action(rule_id, "提交规则评审", {})
        results: list[bool] = []
        errors: list[str] = []
        barrier = threading.Barrier(4)

        def worker() -> None:
            barrier.wait()
            entry, message = self.throttle.run_action(
                rule_id, "启用规则", {"绑定面板编号": "MONI-0003"}
            )
            results.append(entry is not None)
            errors.append(message)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(results), 1, f"应只有一个启用成功，实际：{results} / {errors}")
        rule = store.find_by_code("throttle", "规则编号", "THRO-0002")
        self.assertEqual(rule["status"], "已启用")

    def test_concurrent_archive_only_one_succeeds(self) -> None:
        """并发归档同一面板：只允许一个提交成功。"""
        panel_id = self.moni1["id"]
        results: list[bool] = []
        barrier = threading.Barrier(4)

        def worker() -> None:
            barrier.wait()
            entry, _, _ = self.monitor.run_action(panel_id, "归档面板")
            results.append(entry is not None)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(results), 1)

    # ---- THRO-0002：历史保留与边界值 ---------------------------------------

    def test_decision_history_keeps_original_alert_version(self) -> None:
        """重新绑定后，历史限速决策仍保留决策当时的面板版本与阈值。"""
        before = self.throttle.list_decisions(self.thro2["id"])
        self.assertTrue(before)
        self.assertTrue(
            all(d["面板编号"] == "MONI-0001" and d["面板版本"] == 1 for d in before)
        )
        # 归档旧面板并重新绑定到 MONI-0003
        self.monitor.run_action(self.moni1["id"], "归档面板")
        rule = store.find_by_code("throttle", "规则编号", "THRO-0002")
        self.throttle.run_action(
            rule["id"], "启用规则", {"绑定面板编号": "MONI-0003"}
        )
        # 再产生一条新决策
        rule = store.find_by_code("throttle", "规则编号", "THRO-0002")
        record, _ = self.throttle.record_decision(
            rule["id"], {"实测错误率": 13, "当前速率": 100}
        )
        self.assertEqual(record["面板编号"], "MONI-0003")
        history = self.throttle.list_decisions(rule["id"])
        versions = {(d["面板编号"], d["面板版本"], d["错误率阈值"]) for d in history}
        # 旧版本（MONI-0001 v1, 5.0）与新版本同时存在，旧记录未被改写
        self.assertIn(("MONI-0001", 1, 5.0), versions)
        self.assertIn(("MONI-0003", 1, 12.0), versions)

    def test_archived_panel_rule_stops_making_decisions(self) -> None:
        """归档后处于待复核的规则不能继续产生限速决策。"""
        self.monitor.run_action(self.moni1["id"], "归档面板")
        rule = store.find_by_code("throttle", "规则编号", "THRO-0002")
        entry, message = self.throttle.record_decision(
            rule["id"], {"实测错误率": 50, "当前速率": 100}
        )
        self.assertIsNone(entry)
        self.assertIn("只有已启用规则", message)

    def test_boundary_values_still_go_through_decision(self) -> None:
        """边界值（0%、100%、等于阈值、0 速率）必须正常参与限速决策，不能被绕过。"""
        # 0% 错误率 -> 维持
        record, _ = self.throttle.record_decision(
            self.thro2["id"], {"实测错误率": 0, "当前速率": 100}
        )
        self.assertEqual(record["决策结果"], "维持")
        self.assertEqual(record["决策速率"], 100)
        # 等于阈值 5.0 -> 维持观察（边界不绕过）
        record, _ = self.throttle.record_decision(
            self.thro2["id"], {"实测错误率": 5.0, "当前速率": 100}
        )
        self.assertEqual(record["决策结果"], "维持观察")
        # 100% 错误率 -> 减速
        record, _ = self.throttle.record_decision(
            self.thro2["id"], {"实测错误率": 100, "当前速率": 100}
        )
        self.assertEqual(record["决策结果"], "减速")
        # 0 速率减速后不会出现负数
        record, _ = self.throttle.record_decision(
            self.thro2["id"], {"实测错误率": 100, "当前速率": 0}
        )
        self.assertEqual(record["决策速率"], 0)
        # 越界值被拒
        entry, message = self.throttle.record_decision(
            self.thro2["id"], {"实测错误率": 100.01, "当前速率": 100}
        )
        self.assertIsNone(entry)
        self.assertIn("超出边界", message)

    def test_list_and_detail_share_same_data_chain(self) -> None:
        """列表与详情取数一致：归档后两条链路读到同一份状态。"""
        self.monitor.run_action(self.moni1["id"], "归档面板")
        listed, _ = self.monitor.list_entries(keyword="MONI-0001")
        detail = self.monitor.get_entry(self.moni1["id"])
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["status"], detail["status"])
        self.assertEqual(listed[0]["采样中"], detail["采样中"])
        self.assertEqual(listed[0]["最后告警窗口"], detail["最后告警窗口"])

        rules, _ = self.throttle.list_entries(keyword="THRO-0002")
        rule_detail = self.throttle.get_entry(self.thro2["id"])
        self.assertEqual(rules[0]["status"], rule_detail["status"])
        self.assertEqual(rules[0]["绑定失效"], rule_detail["绑定失效"])


    def test_overview_excludes_internal_decision_table(self) -> None:
        """运营概览只统计业务模块，不可变决策历史表不混入。"""
        overview = store.overview()
        names = [item["name"] for item in overview["modules"]]
        self.assertIn("monitor", names)
        self.assertIn("throttle", names)
        self.assertNotIn("throttle_decision", names)


if __name__ == "__main__":
    unittest.main()
