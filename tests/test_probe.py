"""Phase 1 验收测试：格式注册表、能力探测、尺寸约束、CLI 预检。

运行方式（开发环境）：
    pip install -e ".[dev]"
    pytest tests/test_probe.py

基线：av 18.x wheel（全部 8 条受支持路径可用、hap 无 encoder）。
在 av 19 上跑同一组测试即完成附录 B.2 的升级校准。
"""

import json

import pytest

from tgtv import cli, formats, probe


# ---------------------------------------------------------------- 注册表 --


def test_registry_has_eight_supported_formats():
    assert len(formats.FORMATS) == 8
    assert "vp9" in formats.FORMATS
    assert "hap" not in formats.FORMATS


def test_registry_hap_is_removed_with_note():
    assert "hap" in formats.REMOVED_FORMATS
    note = formats.REMOVED_FORMATS["hap"].note
    assert "移除" in note and "hap encoder" in note


def test_writer_options_mirror_skill_templates():
    """注册表选项必须逐项对应旧 SKILL.md §3 的命令模板（抽查关键字段）。"""
    assert formats.FORMATS["vp9"].writer_options["crf"] == "30"
    assert formats.FORMATS["vp9"].writer_options["auto-alt-ref"] == "0"
    assert formats.FORMATS["vp9-lossless"].writer_options["lossless"] == "1"
    assert formats.FORMATS["prores4444"].writer_options == {"profile": "4444", "alpha_bits": "8"}
    assert formats.FORMATS["mp4-black"].writer_options["crf"] == "20"
    assert formats.FORMATS["mp4-black"].muxer_options == {"movflags": "+faststart"}
    assert formats.FORMATS["mp4-black"].alpha is False
    assert all(f.alpha for k, f in formats.FORMATS.items() if k != "mp4-black")


# ------------------------------------------------------------------ 探测 --


def test_probe_all_supported_formats_available_on_baseline_stack():
    statuses = {s.key: s for s in probe.probe_all_formats() if s.offered}
    assert len(statuses) == 8
    for key, s in statuses.items():
        assert s.available, f"{key}: {s.reason}"


def test_probe_hap_reported_honestly():
    statuses = {s.key: s for s in probe.probe_all_formats() if not s.offered}
    assert set(statuses) == {"hap"}
    s = statuses["hap"]
    assert not s.available
    assert "hap encoder" in s.reason or "hap encoder" in s.removed_note


def test_probe_uses_writer_config_so_pass_implies_writable():
    """probe 打开的配置必须与 writer_options 一致（防止探测与实现漂移）。"""
    spec = formats.FORMATS["vp9"]
    ok, reason = probe._try_open_encoder(spec)
    assert ok, reason


def test_probe_decoders():
    decs = probe.probe_decoders()
    assert set(decs) == set(probe.VERIFICATION_DECODERS)
    assert all(decs.values()), decs


def test_probe_all_is_json_serializable():
    payload = json.dumps(probe.probe_all(), ensure_ascii=False)
    assert "vp9" in payload and "hap" in payload


def test_probe_environment_reports_no_system_ffmpeg_as_diagnostic():
    env = probe.probe_environment()
    assert env["av_version"]
    assert "libavcodec" in env["ffmpeg_libraries"]
    # system_ffmpeg 只是诊断字段（本工具不依赖），键必须存在
    assert "system_ffmpeg" in env


# ------------------------------------------------------------ 尺寸约束 --


def test_size_constraint_mp4_rejects_odd_width():
    err = probe.size_error(formats.FORMATS["mp4-black"], 999, 1000)
    assert err and "宽 999 不是偶数" in err


def test_size_constraint_mp4_rejects_odd_height():
    err = probe.size_error(formats.FORMATS["mp4-black"], 1000, 999)
    assert err and "高 999 不是偶数" in err


def test_size_constraint_mp4_accepts_even():
    assert probe.size_error(formats.FORMATS["mp4-black"], 1000, 998) is None


def test_size_constraint_webm_has_no_even_rule():
    """旧文档实测：WebM 侧 999×999 / 1000×999 / 999×1000 均正常。"""
    for spec in (formats.FORMATS["vp9"], formats.FORMATS["vp8"], formats.FORMATS["vp9-lossless"]):
        assert probe.size_error(spec, 999, 999) is None
        assert probe.size_error(spec, 1000, 999) is None
        assert probe.size_error(spec, 999, 1000) is None


def test_size_constraint_hap_multiple_of_four():
    err = probe.size_error(formats.REMOVED_FORMATS["hap"], 1002, 1000)
    assert err and "multiple of 4" in err
    assert probe.size_error(formats.REMOVED_FORMATS["hap"], 1000, 1000) is None


# ------------------------------------------------------------------ CLI --


def test_cli_probe_informational_exit_zero(capsys):
    assert cli.main(["probe"]) == 0
    out = capsys.readouterr().out
    assert "输出格式" in out and "hap" in out and "验证解码器" in out


def test_cli_probe_json_parses(capsys):
    assert cli.main(["probe", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert len([f for f in report["formats"] if f["offered"]]) == 8
    assert report["environment"]["av_version"]


def test_cli_require_available_format_exits_zero(capsys):
    assert cli.main(["probe", "--require", "vp9"]) == 0
    assert "预检通过：vp9" in capsys.readouterr().out


def test_cli_require_removed_hap_exits_one(capsys):
    assert cli.main(["probe", "--require", "hap"]) == 1
    out = capsys.readouterr().out
    assert "预检失败：hap" in out and "移除" in out


def test_cli_require_unknown_key_exits_two(capsys):
    assert cli.main(["probe", "--require", "nope"]) == 2
    assert "未知格式" in capsys.readouterr().err


def test_cli_require_with_size_constraint(capsys):
    # 黑底 MP4 偶数规则：999x999 必须拒绝，1000x998 通过
    assert cli.main(["probe", "--require", "mp4-black", "--size", "999x999"]) == 1
    assert "不是偶数" in capsys.readouterr().out
    assert cli.main(["probe", "--require", "mp4-black", "--size", "1000x998"]) == 0
    capsys.readouterr()


def test_cli_require_webm_odd_size_passes(capsys):
    assert cli.main(["probe", "--require", "vp9", "--size", "999x999"]) == 0
    capsys.readouterr()


def test_cli_size_without_require_is_error(capsys):
    assert cli.main(["probe", "--size", "999x999"]) == 2
    assert "--size 必须与 --require 同用" in capsys.readouterr().err


def test_cli_no_command_prints_help(capsys):
    assert cli.main([]) == 2
    assert "probe" in capsys.readouterr().out


def test_cli_version(capsys):
    with pytest.raises(SystemExit) as ei:
        cli.main(["--version"])
    assert ei.value.code == 0
    assert __import__("tgtv").__version__ in capsys.readouterr().out
