"""多资产质检批次：可补检单元、分区去重、逐表有效规则、补检来源与整组报告。

场景与需求一一对应：
- 实收事实表 + 店铺维表已到，币种维表等待交付；
- 三张表登记不同的完整性下限，未齐资产不能借用邻表指标；
- 批量提交同分区重复导入只留一份质量依据；
- 补入币种分区要能指明取自哪张表、哪个窗口，且仍用币种表自己的阈值；
- 调度治理消费批次逐表运行有效规则，台账能按资产和分区回查；
- 一张维表先接入失败、补齐后再查三表关联报告和未结调度项。
"""
import app.store as store_mod
from app.services.quality_batch import QualityBatchService
from app.services.quality_rule import QualityRuleService
from app.services.partition_ledger import PartitionLedgerService
from app.services.schedule_job import ScheduleJobService

GROUP = "RETAIL_SETTLE_TEST"
WINDOW = "2026-11-22"
FACT, STORE, CURRENCY = "FACT_RECEIPT", "DIM_STORE", "DIM_CURRENCY"


def _fresh():
    store_mod.store.reset()
    return {
        "batch": QualityBatchService(),
        "rule": QualityRuleService(),
        "ledger": PartitionLedgerService(),
        "job": ScheduleJobService(),
    }


def _register(s, thresholds=None):
    """登记同组三张表；thresholds 按资产覆盖完整性下限。"""
    result = s["batch"].register_group(
        GROUP, WINDOW,
        [
            {"资产编号": FACT, "资产名称": "实收事实表", "资产类型": "事实表", "分区": "dt=2026-11-21"},
            {"资产编号": STORE, "资产名称": "店铺维表", "资产类型": "维表", "分区": "dt=2026-11-21"},
            {"资产编号": CURRENCY, "资产名称": "币种维表", "资产类型": "维表", "分区": "dt=2026-11-21"},
        ],
        group_name="零售结算日批",
    )
    assert result["ok"]
    batch = result["batch"]
    values = {"FACT_RECEIPT": 98, "DIM_STORE": 90, "DIM_CURRENCY": 95}
    values.update(thresholds or {})
    for code, low in values.items():
        s["batch"].register_asset_rule(code, WINDOW, low)
    return batch


def test_unready_asset_does_not_borrow_neighbour_metrics():
    s = _fresh()
    batch = _register(s)
    s["batch"].submit(batch["id"], [
        {"资产编号": FACT, "分区": "dt=2026-11-21", "metrics": {"完整性": 99.2}},
        {"资产编号": STORE, "分区": "dt=2026-11-21", "metrics": {"完整性": 96.5}},
    ])
    out = s["batch"].schedule(batch["id"])

    assert out["comparable"] is False
    assert CURRENCY in out["missing_assets"]
    currency_rows = [r for r in out["ledger"] if r["资产编号"] == CURRENCY]
    assert currency_rows == []
    # 已到的资产可以单独入账，但不能代表整组
    fact_rows = [r for r in out["ledger"] if r["资产编号"] == FACT]
    store_rows = [r for r in out["ledger"] if r["资产编号"] == STORE]
    assert fact_rows and store_rows
    assert fact_rows[0]["阈值下限"] == "98.0"
    assert store_rows[0]["阈值下限"] == "90.0"


def test_same_partition_reimport_keeps_single_evidence():
    s = _fresh()
    batch = _register(s)
    first = s["batch"].submit(batch["id"], [
        {"资产编号": FACT, "分区": "dt=2026-11-21", "metrics": {"完整性": 99.2}},
    ])
    assert first["deduplicated"] == 0
    second = s["batch"].submit(batch["id"], [
        {"资产编号": FACT, "分区": "dt=2026-11-21", "metrics": {"完整性": 97.4}},
    ])
    assert second["deduplicated"] == 1
    member = next(m for m in second["batch"]["成员"] if m["资产编号"] == FACT)
    assert sorted(member["证据"]) == ["dt=2026-11-21"]
    evidence = member["证据"]["dt=2026-11-21"]
    assert evidence["分区"] == "dt=2026-11-21"
    # 只有一份质量依据：首次测点不被重复导入覆盖
    assert evidence["测点"]["完整性"] == 99.2
    assert evidence["重复导入次数"] == 1


def test_multiple_partitions_keep_separate_evidence_and_ledger():
    s = _fresh()
    batch = _register(s)
    s["batch"].submit(batch["id"], [
        {"资产编号": FACT, "分区": "dt=2026-11-21", "metrics": {"完整性": 99.2}},
        {"资产编号": FACT, "分区": "dt=2026-11-20", "metrics": {"完整性": 97.0}},
    ])
    member = next(m for m in s["batch"].get_batch(batch["id"])["成员"] if m["资产编号"] == FACT)
    assert sorted(member["证据"]) == ["dt=2026-11-20", "dt=2026-11-21"]

    s["batch"].schedule(batch["id"])
    rows = s["ledger"].ledger_for(batch["批次编号"], FACT)
    by_partition = {row["分区"]: row for row in rows}
    assert by_partition["dt=2026-11-21"]["实际测点"] == "99.2"
    assert by_partition["dt=2026-11-20"]["实际测点"] == "97.0"
    assert by_partition["dt=2026-11-20"]["判定"] == "阻断"  # 97 < 98


def test_ledger_tracks_asset_and_partition():
    s = _fresh()
    batch = _register(s)
    s["batch"].submit(batch["id"], [
        {"资产编号": FACT, "分区": "dt=2026-11-21", "metrics": {"完整性": 99.2}},
    ])
    s["batch"].schedule(batch["id"])
    rows = s["ledger"].ledger_for(batch["批次编号"], FACT)
    assert len(rows) == 1
    row = rows[0]
    assert row["分区"] == "dt=2026-11-21"
    assert row["实际测点"] == "99.2"
    assert row["阈值下限"] == "98.0"
    assert row["判定"] == "通过"


def test_supplement_records_source_and_uses_own_threshold():
    s = _fresh()
    batch = _register(s)
    s["batch"].submit(batch["id"], [
        {"资产编号": FACT, "分区": "dt=2026-11-21", "metrics": {"完整性": 99.2}},
        {"资产编号": STORE, "分区": "dt=2026-11-21", "metrics": {"完整性": 96.5}},
    ])
    s["batch"].schedule(batch["id"])

    # 币种表自己的下限是 95，实测 93 应判阻断；若误用实收表已通过的 98 或测点会失真。
    out = s["batch"].supplement(
        batch["id"], CURRENCY,
        partition="dt=2026-11-21",
        source_asset=CURRENCY,
        source_window="2026-11-21 上午批次",
        source_partition="dt=2026-11-21",
        metrics={"完整性": 93.0},
        note="上午交付补入",
    )
    assert out["ok"]
    assert out["supplement"]["来源资产"] == CURRENCY
    assert out["supplement"]["来源窗口"] == "2026-11-21 上午批次"
    row = out["ledger"][0]
    assert row["资产编号"] == CURRENCY
    assert row["阈值下限"] == "95.0"
    assert row["实际测点"] == "93.0"
    assert row["判定"] == "阻断"
    assert row["补检"] == "是"
    assert row["来源窗口"] == "2026-11-21 上午批次"
    # 三张表都到齐后整组可比
    assert out["comparable"] is True


def test_supplement_refuses_without_own_rules():
    s = _fresh()
    batch = s["batch"].register_group(
        GROUP, WINDOW,
        [{"资产编号": FACT, "分区": "dt=2026-11-21"},
         {"资产编号": CURRENCY, "分区": "dt=2026-11-21"}],
    )["batch"]
    s["batch"].register_asset_rule(FACT, WINDOW, 98)
    out = s["batch"].supplement(
        batch["id"], CURRENCY, partition="dt=2026-11-21",
        source_asset=FACT, source_window=WINDOW, metrics={"完整性": 99.0},
    )
    assert out["ok"] is False
    assert "未登记有效规则" in out["error"]


def test_failed_ingest_then_resubmit_then_report():
    s = _fresh()
    batch = _register(s)

    # 批量提交：一张事实表 + 两张维表，店铺维表接入失败
    submitted = s["batch"].submit(batch["id"], [
        {"资产编号": FACT, "分区": "dt=2026-11-21", "metrics": {"完整性": 99.2}},
        {"资产编号": STORE, "分区": "dt=2026-11-21", "接入状态": "接入失败",
         "失败原因": "店铺维表文件校验失败"},
        {"资产编号": CURRENCY, "分区": "dt=2026-11-21", "metrics": {"完整性": 96.0}},
    ])
    assert submitted["failed"] == 1
    assert s["batch"].get_batch(batch["id"])["到齐情况"] == "2/3"

    scheduled = s["batch"].schedule(batch["id"])
    assert scheduled["comparable"] is False
    store_rows = [r for r in scheduled["ledger"] if r["资产编号"] == STORE]
    assert store_rows == []

    # 补齐同一分区后重提：店铺维表按自己的 90 下限入账
    resubmitted = s["batch"].submit(batch["id"], [
        {"资产编号": STORE, "分区": "dt=2026-11-21", "metrics": {"完整性": 91.0}},
    ])
    assert resubmitted["ok"]
    assert any(r["资产编号"] == STORE and r["ok"] for r in resubmitted["results"])

    report = s["batch"].report(batch["id"])
    assert report["ok"]
    assert {a["资产编号"] for a in report["assets"]} == {FACT, STORE, CURRENCY}
    assert report["comparable"] is True
    assert report["组结论"] == "整组可比-通过"
    judgements = {a["资产编号"]: a["判定"] for a in report["assets"]}
    assert judgements[FACT] == "通过"
    assert judgements[STORE] == "通过"
    assert judgements[CURRENCY] == "通过"

    # 未结调度项：作业应挂在批次上，且随着全部通过而收口
    jobs = s["batch"].unresolved_items(batch["批次编号"])
    job_rows = [j for j in store_mod.store.rows("schedule_job") if j.get("批次编号") == batch["批次编号"]]
    assert len(job_rows) == 1
    assert job_rows[0]["依赖检查"].startswith("消费质检批次")
    assert jobs == []  # 全部通过后没有未结项


def test_failed_ingest_clears_stale_evidence_before_resubmit():
    """分区曾导入成功后再报接入失败，不能保留旧证据把补齐误判为重复导入。"""
    s = _fresh()
    batch = _register(s)
    s["batch"].submit(batch["id"], [
        {"资产编号": STORE, "分区": "dt=2026-11-21", "metrics": {"完整性": 96.5}},
    ])
    s["batch"].submit(batch["id"], [
        {"资产编号": STORE, "分区": "dt=2026-11-21", "接入状态": "接入失败",
         "失败原因": "重跑文件损坏"},
    ])
    member = next(m for m in s["batch"].get_batch(batch["id"])["成员"] if m["资产编号"] == STORE)
    assert member["到齐状态"] == "接入失败"
    assert "dt=2026-11-21" not in member["证据"]

    resubmitted = s["batch"].submit(batch["id"], [
        {"资产编号": STORE, "分区": "dt=2026-11-21", "metrics": {"完整性": 92.0}},
    ])
    assert resubmitted["deduplicated"] == 0
    member = next(m for m in s["batch"].get_batch(batch["id"])["成员"] if m["资产编号"] == STORE)
    assert member["到齐状态"] == "已到齐"
    assert member["证据"]["dt=2026-11-21"]["测点"]["完整性"] == 92.0


def test_unresolved_job_visible_while_assets_missing():
    s = _fresh()
    batch = _register(s)
    s["batch"].submit(batch["id"], [
        {"资产编号": FACT, "分区": "dt=2026-11-21", "metrics": {"完整性": 99.2}},
        {"资产编号": STORE, "分区": "dt=2026-11-21", "metrics": {"完整性": 96.5}},
    ])
    s["batch"].schedule(batch["id"])
    jobs = s["batch"].unresolved_items(batch["批次编号"])
    assert len(jobs) == 1
    assert jobs[0]["status"] == "执行中"
    assert "币种维表" in jobs[0]["待办说明"] or "DIM_CURRENCY" in jobs[0]["待办说明"]


def test_rules_are_per_asset_and_paused_rule_is_skipped():
    s = _fresh()
    batch = _register(s, {"DIM_STORE": 90})
    # 暂停店铺维表的规则后，该表没有有效规则，不能借用实收表阈值
    for rule in store_mod.store.rows("quality_rule"):
        if rule["目标资产"] == STORE:
            rule["status"] = "已暂停"
    s["batch"].submit(batch["id"], [
        {"资产编号": STORE, "分区": "dt=2026-11-21", "metrics": {"完整性": 95.0}},
    ])
    out = s["batch"].schedule(batch["id"])
    store_rows = [r for r in out["ledger"] if r["资产编号"] == STORE]
    assert store_rows == []
    assert any(a["资产编号"] == STORE and a["status"] == "无有效规则" for a in out["asset_results"])


def test_schedule_job_links_batch_rule_count_and_breach():
    """调度治理消费批次逐表运行有效规则，规则阻断会落到问题追踪并让作业保持未结。"""
    s = _fresh()
    batch = _register(s)
    s["batch"].submit(batch["id"], [
        {"资产编号": FACT, "分区": "dt=2026-11-21", "metrics": {"完整性": 90.0}},
    ])
    out = s["batch"].schedule(batch["id"])

    job = out["job"]
    assert job["批次编号"] == batch["批次编号"]
    assert job["依赖检查"].startswith("消费质检批次")
    assert job["status"] == "执行中"
    assert job["pending"] is True

    # 实收表 90 < 98 触发阻断：台账留痕 + 规则最近检查更新 + 问题追踪建单
    fact_rows = [r for r in out["ledger"] if r["资产编号"] == FACT]
    assert fact_rows and fact_rows[0]["判定"] == "阻断"
    rule = next(r for r in store_mod.store.rows("quality_rule")
                if r["目标资产"] == FACT and r.get("目标窗口") == WINDOW)
    assert rule["最近检查"] != "-"
    issues = [i for i in store_mod.store.rows("issue_track") if i["关联资产"] == FACT]
    assert issues, "质量阻断应生成治理问题"

    # 其他两张表没到齐，作业仍是未结项
    assert s["batch"].unresolved_items(batch["批次编号"])[0]["id"] == job["id"]
