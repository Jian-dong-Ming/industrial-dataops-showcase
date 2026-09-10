from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from openpyxl import Workbook  # type: ignore[import-untyped]

from app.core.config import settings
from app.data_import.processor import (
    _is_missing,
    _parse_timestamp,
    _parse_value,
    _quality,
    _raw_text,
)
from app.data_import.reader import (
    TabularReadError,
    _csv_dialect,
    _json_value,
    _validate_headers,
    detect_csv_encoding,
    detect_layout,
    inspect_tabular_file,
    iter_csv_rows,
    iter_xlsx_rows,
    suggest_mapping,
    suggest_timestamp_column,
)
from app.models import ImportFileFormat, ImportLayout, TagDataType


def test_value_and_timestamp_validation_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _raw_text(None) is None
    assert _raw_text("x" * 600) == "x" * 500
    assert _is_missing(None)
    assert _is_missing("  ")
    assert not _is_missing(0)

    fallback = datetime(2026, 8, 22, 8, 0, tzinfo=UTC)
    assert _parse_timestamp(fallback) == fallback
    assert _parse_timestamp("2026-08-22T08:00:00Z") == fallback
    assert _parse_timestamp("2026-08-22 16:00:00") == fallback
    with pytest.raises(ValueError, match="时间必须"):
        _parse_timestamp("not-a-time")
    monkeypatch.setattr(settings, "IMPORT_DEFAULT_TIMEZONE", "Invalid/Timezone")
    with pytest.raises(RuntimeError, match="默认时区不可用"):
        _parse_timestamp("2026-08-22 16:00:00")

    assert _parse_value(" text ", TagDataType.STRING) == ("text", None)
    assert _parse_value(True, TagDataType.BOOLEAN) == (True, 1.0)
    assert _parse_value("是", TagDataType.BOOLEAN) == (True, 1.0)
    assert _parse_value("关", TagDataType.BOOLEAN) == (False, 0.0)
    with pytest.raises(ValueError, match="布尔值"):
        _parse_value("maybe", TagDataType.BOOLEAN)
    with pytest.raises(ValueError, match="数值格式"):
        _parse_value("not-a-number", TagDataType.FLOAT)
    with pytest.raises(ValueError, match="NaN"):
        _parse_value("nan", TagDataType.FLOAT)
    with pytest.raises(ValueError, match="不能导入小数"):
        _parse_value("1.5", TagDataType.INTEGER)
    assert _parse_value("2", TagDataType.INTEGER) == (2, 2.0)
    assert _parse_value("2.5", TagDataType.FLOAT) == (2.5, 2.5)

    assert _quality(None) == ("Good", True)
    assert _quality("OK") == ("OK", True)
    assert _quality("BadSensorFailure") == ("BadSensorFailure", False)
    assert len(_quality("x" * 100)[0]) == 64


def test_tabular_reader_rejects_structural_and_encoding_errors(tmp_path: Path) -> None:
    with pytest.raises(TabularReadError, match="no header"):
        _validate_headers([])
    with pytest.raises(TabularReadError, match="non-empty"):
        _validate_headers(["timestamp", ""])
    with pytest.raises(TabularReadError, match="unique"):
        _validate_headers(["value", "value"])

    invalid_encoding = tmp_path / "invalid.csv"
    invalid_encoding.write_bytes(b"\xff\xff\xff")
    with pytest.raises(TabularReadError, match="UTF-8 or GB18030"):
        detect_csv_encoding(invalid_encoding)

    single_column = tmp_path / "single.csv"
    single_column.write_text("value\n1\n", encoding="utf-8")
    assert _csv_dialect(single_column, "utf-8").delimiter == ","
    assert list(iter_csv_rows(single_column, "utf-8")) == [(2, {"value": "1"})]

    corrupt_workbook = tmp_path / "corrupt.xlsx"
    corrupt_workbook.write_bytes(b"not an xlsx archive")
    with pytest.raises(TabularReadError, match="Unable to read"):
        list(iter_xlsx_rows(corrupt_workbook))

    empty_workbook = tmp_path / "empty.xlsx"
    workbook = Workbook()
    workbook.save(empty_workbook)
    workbook.close()
    with pytest.raises(TabularReadError):
        list(iter_xlsx_rows(empty_workbook))

    header_only = tmp_path / "header-only.csv"
    header_only.write_text("timestamp,tag_code,value\n", encoding="utf-8")
    with pytest.raises(TabularReadError, match="no data rows"):
        inspect_tabular_file(header_only, ImportFileFormat.CSV)


def test_mapping_suggestions_and_json_values() -> None:
    assert _json_value(date(2026, 8, 22)) == "2026-08-22"
    assert _json_value(1.5) == 1.5
    assert suggest_mapping(["unrelated"]) is None
    mapping = suggest_mapping(["采集时间", "变量编码", "测量值", "设备", "质量"])
    assert mapping is not None
    assert mapping.timestamp_column == "采集时间"
    assert mapping.tag_code_column == "变量编码"
    assert mapping.value_column == "测量值"
    assert mapping.device_code_column == "设备"
    assert mapping.quality_column == "质量"
    assert mapping.layout == ImportLayout.LONG
    assert detect_layout(["timestamp", "tag_code", "value"]) == ImportLayout.LONG
    assert detect_layout(["timestamp", "温度", "压力"]) == ImportLayout.WIDE
    assert suggest_timestamp_column(["批次", "采集时间", "温度"]) == "采集时间"
