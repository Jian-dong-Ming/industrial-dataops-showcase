"""Constraints from explicit Chinese relative trend requests, not general NLU."""

import re
from dataclasses import dataclass
from typing import Any

from app.assistant.schemas import Evidence

WINDOW = re.compile(
    r"(?:过去|最近|近|前)\s*([0-9]+(?:\.[0-9]+)?|[一二两三四五六七八九十百半]+)"
    r"\s*(小时|分钟|天|日|星期|周|个月|月|年)"
)
TREND = re.compile(r"趋势|变化|均值|平均|最大|最小|统计|极值")
GROUPING = re.compile(r"每(?:个)?(?:小时|天|日|分钟)|逐(?:小时|日)|按(?:小时|天|分钟)")


def required_operation(question: str) -> str | None:
    """Recognize narrow current-task queries, not general intent or authorization.

    Manuals, hypothetical questions and explicit non-query requests must not be
    silently substituted with a live snapshot. Unknown wording remains with the
    normal tool loop; this guard is deliberately not a universal NLU claim.
    """
    if not re.search(r"采集任务", question):
        return None
    if re.search(
        r"如何|怎么|怎样|步骤|假设|假如|如果|能否|能不能|是否可以|"
        r"(?:不要|不用|无需|不必|禁止)(?:去|再)?(?:查|查询|读取)|"
        r"(?:只|仅)(?:解释|说明|讨论|看)",
        question,
    ):
        return None
    if re.search(r"当前|现在|实际|查询|查一下|列出", question) and re.search(
        r"几个|多少|数量|总数|共有|状态|是否|运行|停止|连接|丢数", question
    ):
        return "acquisition_status"
    return None


def _number(token: str) -> float:
    if token == "半":
        return 0.5
    if token[0].isdigit():
        return float(token)
    digit = "[一二两三四五六七八九]"
    if not re.fullmatch(
        rf"(?:{digit}|{digit}?十{digit}?|{digit}百(?:{digit}?十)?{digit}?)", token
    ):
        raise ValueError("ambiguous_chinese_number")
    values = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    total = current = 0
    for char in token:
        if char in values:
            current = values[char]
        elif char in {"十", "百"}:
            total += (current or 1) * (10 if char == "十" else 100)
            current = 0
        else:
            raise ValueError("unsupported_number")
    return float(total + current)


@dataclass(frozen=True)
class TrendConstraint:
    hours: tuple[int, ...]
    blocked: bool
    reason: str

    def evidence(self, run_id: object) -> Evidence:
        return Evidence(
            id=f"capability:{run_id}:trend",
            kind="capability",
            title="趋势查询能力",
            data={
                "supported_hours": "最近1—24整小时",
                "supported_aggregation": "整个窗口内最近至多1000条样本的摘要，不支持逐小时/日分桶",
                "recognized_requested_hours": list(self.hours),
                "trend_tool_blocked": self.blocked,
                "reason": self.reason,
                "scope": "服务端能力约束，不是测点读数或生产规程；未识别的自然语言不代表已经验证支持。",
            },
        )

    def permits(self, arguments: str) -> bool:
        from app.assistant.tools import TagTrend

        params = TagTrend.model_validate_json(arguments)
        return not self.blocked and (not self.hours or params.hours in self.hours)


def trend_constraint(question: str) -> TrendConstraint | None:
    if not TREND.search(question):
        return None
    matches = list(WINDOW.finditer(question))
    grouping = bool(GROUPING.search(question))
    if not matches and not grouping:
        return None
    hours: list[int] = []
    unsupported = False
    for match in matches:
        token, unit = match.groups()
        if unit in {"个月", "月", "年"}:
            unsupported = True
            continue
        try:
            value = (
                _number(token)
                * {
                    "分钟": 1 / 60,
                    "小时": 1,
                    "天": 24,
                    "日": 24,
                    "周": 168,
                    "星期": 168,
                }[unit]
            )
        except ValueError:
            unsupported = True
            continue
        if not value.is_integer() or not 1 <= value <= 24:
            unsupported = True
        else:
            hours.append(int(value))
    blocked = unsupported or grouping
    reason = (
        "原问题包含不支持的时间范围或分桶统计。不得缩短/扩大窗口后代答，也不得先查询另一个窗口再拒答；可以说明限制或处理问题中的其他受支持部分。"
        if blocked
        else "趋势参数必须对应原问题明确的时间窗口，不得静默改为默认1小时。"
    )
    return TrendConstraint(tuple(sorted(set(hours))), blocked, reason)


def available_tools(
    definitions: list[dict[str, Any]], constraint: TrendConstraint | None
) -> list[dict[str, Any]]:
    return [
        tool
        for tool in definitions
        if not (
            constraint
            and constraint.blocked
            and tool["function"]["name"] == "tag_trend"
        )
    ]
