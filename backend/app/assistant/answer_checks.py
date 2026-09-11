"""Validate generated structure, then attach safety facts from authorized tools.

These checks do not prove general semantic correctness. They prevent known
quality/source warnings from depending solely on the model's phrasing.
"""

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.assistant.schemas import Evidence, GeneratedAnswer


def conditional_conclusion_error(answer: GeneratedAnswer) -> str | None:
    """A narrow wording guard, not a general semantic/authorization verifier.

    Do not silently change 'yes' to 'no': ask for a grounded correction instead.
    Explicit positive conclusions followed immediately by unresolved prerequisites
    are ambiguous, even if a later sentence supplies the missing restriction.
    """
    if answer.status != "answered":
        return None
    leading = answer.answer.lstrip(" \n\t*#")
    if re.match(
        r"^(?:可以|能|允许|可继续)(?:[，,。；;：:]|\s)*"
        r"(?:但(?:是)?|不过)(?:[，,]|\s)*(?:需(?:要)?|须|必须|应先|要先|前提)",
        leading,
    ):
        return "ambiguous_conditional_conclusion"
    return None


def normalize_explicit_refusal(answer: GeneratedAnswer) -> None:
    """Normalize explicit assistant refusals, not all negative explanations."""
    leading = answer.answer.lstrip(" \n\t*#")
    if answer.status == "answered" and re.match(
        r"^(?:抱歉[，,。]?\s*)?(?:"
        r"(?:我|本助手|AI助手)(?:无法|不能|不会)(?:为你|为您|替你|替您)?"
        r"(?:绕过权限|执行(?:修改|删除|写入)|直接(?:修改|删除|写入)|批准旁路)"
        r"|(?:无法|不能|不会)(?:绕过权限|执行(?:修改|删除|写入)|直接(?:修改|删除|写入)))",
        leading,
    ):
        answer.status = "no_answer"


def _display_time(value: Any) -> str:
    if not value:
        return "暂无"
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        return f"{value}（时区未知）"
    return parsed.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def _operation_facts(item: Evidence) -> str:
    data = item.data
    if item.title == "tag_trend":
        text = (
            f"测点 {data['code']}（{data['name']}）；来源 {data['source']}；"
            f"测点{'已启用' if data['enabled'] else '已禁用'}。\n"
            f"请求时间范围：{_display_time(data['window_start'])} 至 {_display_time(data['window_end'])}。\n"
            f"返回 {data['sample_count']} 条；Good数值 {data['good_numeric_count']} 条；"
            f"坏质量 {data['bad_quality_count']} 条。\n"
        )
        if data["truncated"]:
            text += "结果已截断：以下仅为最近至多1000条样本的统计，不是整个请求时间范围的统计。\n"
        if data["good_numeric_count"]:
            unit = data.get("unit") or ""
            text += (
                f"均值 {data['mean']:.6f} {unit}；最小值 {data['minimum']:.6f} {unit}；"
                f"最大值 {data['maximum']:.6f} {unit}。\n"
                + (
                    f"首末差 {data['first_to_last_change']:.6f} {unit}。\n"
                    if data["first_to_last_change"] is not None
                    else "不足两个有效数值，无法计算首末差。\n"
                )
                + f"返回样本首末时间：{_display_time(data['first_timestamp'])} 至 {_display_time(data['last_timestamp'])}。\n"
            )
        else:
            text += "没有有效Good数值样本，不能计算均值或极值，不能解释为零。\n"
        return (
            text
            + "数值统计排除坏质量和非数值样本；Good不等于工艺合格，首末差不是趋势斜率，不进行故障根因判断或预测。"
            + ("file来源为历史文件，不是实时采集。" if data["source"] == "file" else "")
        )
    if item.title == "acquisition_status":
        states = {
            "running": "运行",
            "stopped": "停止",
            "connected": "已连接",
            "connecting": "连接中",
            "reconnecting": "重连中",
            "error": "异常",
        }
        rows = data["tasks"]
        text = "采集状态快照（时间含时区偏移+08:00；计数为累计值，不是本小时计数）：\n"
        if not data["truncated"]:
            text += f"当前查询共 {len(rows)} 个采集任务。\n"
        for row in rows[:10]:
            text += (
                f"{row['name']}：期望{states.get(row['desired_state'], row['desired_state'])}，"
                f"记录连接状态{states.get(row['connection_state'], row['connection_state'])}；"
                f"心跳 {_display_time(row['heartbeat_at'])}，最后样本 {_display_time(row['last_sample_at'])}；"
                f"接收{row['received']} / 写入{row['written']} / 丢弃{row['dropped']} / 重复{row['duplicates']} / 错误{row['errors']} / 重连{row['reconnects']}。\n"
            )
        if not rows:
            text += "当前查询没有采集任务。\n"
        if data["truncated"]:
            text += "列表已截断，不能据此推断任务总数，请到实时采集页面查看。\n"
        elif len(rows) > 10:
            text += "此处仅展示前10个任务，总数以上面的完整查询计数为准。\n"
        return (
            text
            + "连接状态是数据库记录，不保证所有测点实时有效；停止后仍保留历史值，累计错误不等于当前故障。"
        )
    rows = data["devices"]
    text = f"当前工厂有 {data['line_count']} 条产线。以下按数据库名称列出设备及测点数（包含禁用资产，不代表在线状态）：\n"
    if "device_count" in data and "tag_count" in data:
        text = (
            f"当前工厂共有 {data['line_count']} 条产线、{data['device_count']} 台设备、"
            f"{data['tag_count']} 个测点（包含禁用资产，不代表在线状态）。\n"
            "以下是设备分配明细：\n"
        )
    text += "\n".join(
        f"{row['line']} / {row['device']}：{row['tag_count']} 个测点。"
        for row in rows[:15]
    )
    if data["truncated"] or len(rows) > 15:
        text += (
            "\n设备列表已截断，不能用明细长度推断总数；请到资产管理页面查看完整明细。"
        )
    if not rows:
        text += "没有查到设备。"
    return text


def attach_data_cautions(answer: GeneratedAnswer, evidence: list[Evidence]) -> None:
    batches = [
        item
        for item in evidence
        if item.kind == "tool" and item.title == "inspect_import_batch"
    ]
    latest = [
        item
        for item in evidence
        if item.kind == "tool" and item.title == "latest_value"
    ]
    operations = [
        item
        for item in evidence
        if item.kind == "tool"
        and item.title in {"tag_trend", "acquisition_status", "asset_overview"}
    ]
    if (batches or latest or operations) and answer.status == "answered":
        # Counts and issue categories are deterministic business facts. Do not
        # ask the LLM to rewrite them (e.g. confusing warnings with failed rows).
        blocks = []
        for item in latest[:3]:
            data = item.data
            sample = data.get("sample")
            text = f"测点 {data['code']}（{data['name']}）："
            if sample:
                text += (
                    f"最新记录 {sample['value']} {data['unit'] or ''}；来源 {sample['source']}；"
                    f"采样时间 {sample['source_timestamp']}；质量码 {sample['status_code']}。"
                    "Good仅表示采样质量，不代表工艺合格。"
                )
            else:
                text += "指定来源没有样本，无法提供当前值。"
            blocks.append(text)
        for item in batches[:3]:
            data = item.data
            if data.get("status") != "completed":
                label = {
                    "uploaded": "已上传，尚未提交",
                    "queued": "排队中",
                    "processing": "处理中",
                    "failed": "处理失败",
                    "duplicate": "重复文件，当前批次未执行导入",
                }.get(str(data.get("status", "")), "状态待核对")
                blocks.append(
                    f"批次 {data['batch_id']}（{data['filename']}）：{label}。\n"
                    "当前不是已完成批次，不能把零计数解释为没有数据、没有问题或已成功入库。"
                    "请在数据治理页面查看任务进度或失败原因；处理中扫描数量也不等于已提交数据。"
                )
                continue
            blocks.append(
                f"批次 {data['batch_id']}（{data['filename']}）：\n"
                f"总行数 {data['total_rows']}；接受 {data['accepted_rows']}；拒绝 {data['rejected_rows']}；"
                f"重复行 {data['duplicate_rows']}；警告行 {data['warning_rows']}；问题条数 {data['issue_count']}。\n"
                "重复行属于拒绝行子集；警告行可能已经入库。问题条数不等于失败行数，不得将这些计数相加。\n"
                + (
                    "问题记录已截断，以下不是全量问题分布。\n"
                    if data["issues_truncated"]
                    else "以下为已保存的问题分布（包含警告）：\n"
                )
                + "\n".join(
                    f"{row['type']}：{row['count']} 条。排查建议：{row['suggestion']}"
                    for row in data["stored_issue_summary"]
                )
                + "\n行号示例："
                + "；".join(
                    f"第{row['row']}行 {row['type']}：{row['message']}"
                    for row in data["examples"][:5]
                )
            )
        blocks.extend(_operation_facts(item) for item in operations[:3])
        destinations = []
        if batches:
            destinations.append("请在数据治理页面查看任务及问题明细。")
        if any(item.title == "asset_overview" for item in operations):
            destinations.append("请在资产管理页面查看完整分配。")
        if latest or any(item.title != "asset_overview" for item in operations):
            destinations.append(
                "请在实时采集页面查看后续变化；本回答不是自动刷新的监控画面。"
            )
        answer.answer = ("\n\n".join(blocks) + "\n\n" + "".join(destinations))[:6000]
        answer.citation_ids = [
            item.id for item in latest[:3] + batches[:3] + operations[:3]
        ]
    if answer.status == "clarification":
        matches = [
            item
            for item in evidence
            if item.kind == "tool"
            and item.title == "find_tags"
            and len(item.data.get("tags", [])) > 1
        ]
        if matches:
            item = matches[-1]
            tags = item.data["tags"]
            # Codes/identifiers must not be rewritten by the language model.
            answer.answer = (
                "找到多个候选测点，不能代你选择。请将目标测点编码写进下一次完整问题：\n"
                + "\n".join(
                    f"{tag['code']} · {tag['name']} · 设备：{tag['device']}"
                    for tag in tags[:10]
                )
            )
            if len(tags) > 10 or item.data.get("truncated"):
                answer.answer += (
                    "\n这里只展示前10个候选，请补充设备名称或更具体的测点编码。"
                )
            answer.citation_ids = [item.id]
    cautions: list[str] = []
    for item in evidence:
        if item.kind != "tool" or item.title != "latest_value":
            continue
        data = item.data
        flags: list[str] = []
        if not data.get("enabled", True):
            flags.append("测点已禁用")
        sample = data.get("sample")
        if sample is None:
            flags.append("指定来源没有样本，无法提供当前值")
        else:
            if not sample["is_good"]:
                flags.append(f"质量码异常（{sample['status_code']}）")
            if sample["stale"]:
                flags.append("数据已过期")
            if sample["future_timestamp"]:
                flags.append("采样时间超前，请核对时钟")
            if sample["source"] == "file":
                flags.append("来源为历史文件导入，不是实时采集")
        if flags:
            cautions.append(
                f"{data['code']}：{'；'.join(flags)}。不能据此认定当前工况正常。"
            )
            if item.id not in answer.citation_ids:
                answer.citation_ids.append(item.id)
    if cautions:
        answer.answer = (
            "【系统数据提示】\n" + "\n".join(cautions) + "\n\n" + answer.answer
        )[:6000]
