"""report-to-metrics.py: Renovate's run report as Prometheus text, written atomically, a missing
or unreadable report leaving the old file in place."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "kubernetes/prod/services/renovate/renovate/app/config/report-to-metrics.py"


def load():
    spec = importlib.util.spec_from_file_location("report_to_metrics_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    spec.loader.exec_module(module)
    return module


m = load()

REPORT = {
    "problems": [{"message": "top-level"}],
    "repositories": {"owner/repo": {
        "problems": [{"message": "one"}, {"message": "two"}],
        "packageFiles": {
            "helm-values": [{"packageFile": "a/values.yaml", "deps": [
                {"depName": "emby", "packageName": "ghcr.io/x/emby", "currentValue": "1.0", "datasource": "docker",
                 "updates": [{"newValue": "1.1", "updateType": "minor"}, {"newDigest": "sha256:abc", "updateType": "digest"}]},
                {"depName": "skipped", "currentValue": "x", "skipReason": "unsupported-value"},
            ]}],
            "flux": [{"packageFile": "b/helmrelease.yaml", "deps": [
                {"depName": "chart", "currentValue": "2.0", "datasource": "helm", "updates": []},
                {"depName": "chart", "currentValue": "2.0", "datasource": "helm"},  # listed twice: counted once
            ]}],
        },
    }},
}


def test_convert_names_each_dependency_and_update_and_counts_them():
    lines = m.convert(REPORT)
    assert ('renovate_dependency_info{file="a/values.yaml",manager="helm-values",package="ghcr.io/x/emby",'
            'current="1.0",datasource="docker"} 1') in lines
    assert ('renovate_dependency_update{file="a/values.yaml",manager="helm-values",package="ghcr.io/x/emby",'
            'current="1.0",latest="1.1",update_type="minor"} 1') in lines
    assert any('latest="sha256:abc",update_type="digest"' in line for line in lines)
    assert not any("skipped" in line for line in lines), "a dependency Renovate skips isn't one"
    assert "renovate_dependencies 2" in lines and "renovate_updates 2" in lines and "renovate_problems 3" in lines


def test_labels_leave_out_empty_values_and_escape_the_rest():
    assert m.labels(a="x", b=None, c="", d='q"\\\n') == '{a="x",d="q\\"\\\\ "}'


def test_an_empty_report_is_all_zeros():
    lines = m.convert({})
    assert "renovate_dependencies 0" in lines and "renovate_updates 0" in lines and "renovate_problems 0" in lines


def _run(monkeypatch, tmp_path, report_text):
    report = tmp_path / "report.json"
    if report_text is not None:
        report.write_text(report_text, encoding="utf-8")
        os.utime(report, (1_800_000_000, 1_800_000_000))
    out = tmp_path / "metrics" / "metrics.txt"
    monkeypatch.setattr(sys, "argv", ["report-to-metrics.py", str(report), str(out)])
    return out


def test_main_writes_the_metrics_with_the_report_s_time(monkeypatch, tmp_path):
    out = _run(monkeypatch, tmp_path, json.dumps(REPORT))
    assert m.main() == 0
    text = out.read_text(encoding="utf-8")
    assert text.endswith("renovate_report_timestamp_seconds 1800000000\n")
    assert "renovate_updates 2" in text
    assert not (out.parent / "metrics.txt.tmp").exists()


@pytest.mark.parametrize("report_text", [None, "{not json"])
def test_a_missing_or_unreadable_report_leaves_the_old_metrics(monkeypatch, tmp_path, report_text):
    out = _run(monkeypatch, tmp_path, report_text)
    out.parent.mkdir(parents=True)
    out.write_text("old\n", encoding="utf-8")
    with pytest.raises((OSError, ValueError)):
        m.main()
    assert out.read_text(encoding="utf-8") == "old\n"


def test_a_run_that_dies_before_the_swap_leaves_the_old_metrics_whole(monkeypatch, tmp_path):
    out = _run(monkeypatch, tmp_path, json.dumps(REPORT))
    out.parent.mkdir(parents=True)
    out.write_text("old\n", encoding="utf-8")

    def dies(src, dst):
        raise OSError("killed before the swap")

    monkeypatch.setattr(m.os, "replace", dies)
    with pytest.raises(OSError):
        m.main()
    assert out.read_text(encoding="utf-8") == "old\n"
