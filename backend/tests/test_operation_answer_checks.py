from app.assistant.answer_checks import attach_data_cautions
from app.assistant.schemas import Evidence, GeneratedAnswer


def render(title: str, data: dict) -> GeneratedAnswer:
    evidence = Evidence(id="tool:test:0", kind="tool", title=title, data=data)
    answer = GeneratedAnswer(
        status="answered", answer="设备别名：错误；均值999；时间09:00。"
    )
    attach_data_cautions(answer, [evidence])
    assert answer.citation_ids == [evidence.id]
    assert "设备别名：错误" not in answer.answer
    return answer


def test_trend_facts_are_scoped_and_keep_timezone_and_truncation() -> None:
    data = {
        "code": "L2_PUMP_FLOW",
        "name": "冷却水流量",
        "source": "opcua",
        "enabled": True,
        "unit": "m³/h",
        "window_start": "2026-09-08T08:00:00+00:00",
        "window_end": "2026-09-08T09:00:00+00:00",
        "sample_count": 1000,
        "good_numeric_count": 999,
        "bad_quality_count": 1,
        "truncated": True,
        "mean": 44.153936,
        "minimum": 41.878065,
        "maximum": 46.392212,
        "first_to_last_change": -0.785103,
        "first_timestamp": "2026-09-08T08:51:00+00:00",
        "last_timestamp": "2026-09-08T09:00:00+00:00",
    }
    answer = render("tag_trend", data).answer
    assert "均值 44.153936" in answer and "最大值 46.392212" in answer
    assert "结果已截断" in answer and "不是整个请求时间范围" in answer
    assert "坏质量 1 条" in answer and "返回样本首末时间" in answer
    assert "2026-09-08T17:00:00+08:00" in answer
    assert "首末差 -0.785103" in answer

    data.update(good_numeric_count=1, first_to_last_change=None, truncated=False)
    answer = render("tag_trend", data).answer
    assert "无法计算首末差" in answer
    assert "结果已截断" not in answer
    data.update(
        good_numeric_count=0, mean=None, minimum=None, maximum=None, enabled=False
    )
    answer = render("tag_trend", data).answer
    assert "测点已禁用" in answer and "不能解释为零" in answer
    assert "均值 0" not in answer


def test_task_and_asset_facts_do_not_invent_online_status_or_aliases() -> None:
    task = {
        "name": "演示任务",
        "desired_state": "stopped",
        "connection_state": "stopped",
        "heartbeat_at": "2026-09-08T09:00:00+00:00",
        "last_sample_at": None,
        "received": 10,
        "written": 9,
        "dropped": 1,
        "duplicates": 0,
        "errors": 2,
        "reconnects": 1,
    }
    text = render("acquisition_status", {"tasks": [task], "truncated": False}).answer
    assert "期望停止" in text and "最后样本 暂无" in text
    assert "17:00:00+08:00" in text and "写入9 / 丢弃1" in text
    assert "不是本小时计数" in text and "累计错误不等于当前故障" in text
    assert (
        "没有采集任务"
        in render("acquisition_status", {"tasks": [], "truncated": False}).answer
    )
    assert (
        "列表已截断"
        in render(
            "acquisition_status", {"tasks": [task] * 11, "truncated": False}
        ).answer
    )
    device = {"line": "一号线", "device": "原始设备名", "tag_count": 4}
    text = render(
        "asset_overview", {"line_count": 1, "devices": [device], "truncated": False}
    ).answer
    assert "一号线 / 原始设备名：4 个测点" in text
    assert "包含禁用资产" in text
    assert (
        "列表已截断"
        in render(
            "asset_overview",
            {"line_count": 1, "devices": [device] * 16, "truncated": False},
        ).answer
    )
    assert (
        "没有查到设备"
        in render(
            "asset_overview", {"line_count": 1, "devices": [], "truncated": False}
        ).answer
    )
