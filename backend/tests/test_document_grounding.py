import pytest

from app.assistant.answer_checks import document_grounding_error
from app.assistant.schemas import Evidence, GeneratedAnswer


@pytest.mark.parametrize(
    "text,expected",
    [
        (
            "coolite-loop-v1（cooling-loop-v1）是该回放。",
            "unsupported_versioned_identifier",
        ),
        (
            "质量码→quality_code（可选映射），预期5条坏质量警告。",
            "quality_mapping_required_for_warning_count",
        ),
        ("平台所有文件都是合成数据。", "unsupported_database_provenance_claim"),
        ("全部数据均为模拟数据。", "unsupported_database_provenance_claim"),
        (
            "其所涉及的数据来源要么是合成回放，要么是模拟测点。",
            "unsupported_database_provenance_claim",
        ),
        ("平台也不存储良率这类质量指标。", "unsupported_database_provenance_claim"),
        ("cooling-loop-v1是合成回放。", None),
        ("quality_code必须映射，才可复现5条坏质量警告。", None),
        ("不能把所有文件都当成模拟数据。", None),
        ("本次授权检索没有取得昨天良率的企业记录，无法计算。", None),
        ("文件格式不代表来源，当前未检查该文件。", None),
    ],
)
def test_narrow_document_grounding_guards(text: str, expected: str | None) -> None:
    evidence = Evidence(
        id="doc:test",
        kind="document",
        title="合成回放说明",
        data={"text": "cooling-loop-v1是合成回放。保留quality_code才能识别坏质量。"},
    )
    answer = GeneratedAnswer(status="answered", answer=text, citation_ids=[evidence.id])
    assert document_grounding_error(answer, [evidence], "解释回放") == expected


def test_refusal_cannot_add_unverified_database_inventory_claim() -> None:
    answer = GeneratedAnswer(
        status="no_answer", answer="没有依据。平台也不存储良率数据。", citation_ids=[]
    )
    assert (
        document_grounding_error(answer, [], "昨天良率多少")
        == "unsupported_database_provenance_claim"
    )
