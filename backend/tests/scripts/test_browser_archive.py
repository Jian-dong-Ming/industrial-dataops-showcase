import runpy
from pathlib import Path

import pytest

archive = runpy.run_path(
    str(Path(__file__).parents[2] / "scripts/archive_browser_fixtures.py")
)
verified = archive["verified_fixture"]


@pytest.mark.parametrize(
    "code,name,location",
    [
        ("AI_UI_1788794102851_12", "AI界面测试 AI_UI_1788794102851_12", "合成数据测试"),
        ("P_E2E_12345678", "数据治理测试工厂", "隔离测试环境"),
        ("PLANT_ABCD1234", "Synthetic Plant PLANT_ABCD1234 Updated", "Test Zone"),
        (
            "AI_LIVE_1788794102851",
            "AI真实联调合成工厂 AI_LIVE_1788794102851",
            "合成验收数据",
        ),
        (
            "AI_BUSINESS_1788794102851",
            "AI业务验收演示工厂 1788794102851",
            "合成演示，不是生产数据",
        ),
        ("RET_A_1788794102851", "保留预览测试A", None),
    ],
)
def test_requires_all_fixture_identifiers(code, name, location):
    row = {"code": code, "name": name, "location": location}
    assert verified(row)
    assert not verified({**row, "name": "用户维护的工厂"})
    assert not verified({**row, "location": "企业现场"})
    assert not verified({**row, "code": code + "_CUSTOM"})


def test_never_selects_opc_demo_or_unknown():
    assert not verified(
        {
            "code": "OPC_DEMO",
            "name": "OPC UA 教学工厂",
            "location": "本地 Docker 模拟环境",
        }
    )
    assert not verified({"code": "MY_PLANT", "name": "我的工厂", "location": None})


def test_archive_writer_refuses_overwrite(tmp_path):
    path = tmp_path / "archive.json"
    archive["write_new"](path, {"original": True})
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        archive["write_new"](path, {"original": False})
    assert path.read_bytes() == original
