"""docker/netbox/patch_topology_views.py: the plugin's RoleImageSerializer patched so NetBox's
change log can render it, and the build stopped when the class isn't as expected. Against a
scratch serializers.py; the patched class is run with a stand-in for DRF."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "docker/netbox/patch_topology_views.py"
ORIGINAL = (
    "from rest_framework.serializers import ModelSerializer\n"
    "from ..models import RoleImage\n"
    "\n"
    "\n"
    "class RoleImageSerializer(ModelSerializer):\n"
    "    class Meta:\n"
    "        model = RoleImage\n"
    "        fields = ('role', 'image')\n"
)


def load():
    spec = importlib.util.spec_from_file_location("patch_topology_views_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    spec.loader.exec_module(module)
    return module


m = load()


def _patch(monkeypatch, tmp_path, text):
    target = tmp_path / "serializers.py"
    target.write_text(text, encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["patch_topology_views.py", str(target)])
    m.main()
    return target


def test_the_serializer_accepts_netbox_s_arguments_and_renders_its_role(monkeypatch, tmp_path):
    target = _patch(monkeypatch, tmp_path, ORIGINAL)
    text = target.read_text(encoding="utf-8")
    assert "from rest_framework.serializers import ModelSerializer, SerializerMethodField\n" in text
    assert text.count("class RoleImageSerializer(ModelSerializer):") == 1
    assert "        fields = ('role', 'image')\n" in text, "the rest of the class as it was"

    class ModelSerializer:
        def __init__(self, *args, **kwargs):
            self.args, self.kwargs = args, kwargs

    drf = types.ModuleType("rest_framework.serializers")
    drf.ModelSerializer, drf.SerializerMethodField = ModelSerializer, lambda: "method field"
    monkeypatch.setitem(sys.modules, "rest_framework", types.ModuleType("rest_framework"))
    monkeypatch.setitem(sys.modules, "rest_framework.serializers", drf)
    namespace = {}
    exec(compile(text.replace("from ..models import RoleImage\n", "RoleImage = object\n"), "serializers.py", "exec"), namespace)
    cls = namespace["RoleImageSerializer"]
    s = cls("instance", nested=True, fields=["id"], omit=["x"], context={"c": 1})
    assert s.args == ("instance",) and s.kwargs == {"context": {"c": 1}}, "NetBox's three dropped, the rest passed on"
    role = types.SimpleNamespace(slug="switch", name="Switch")
    assert s.get_role(types.SimpleNamespace(role=role)) == {"slug": "switch", "name": "Switch"}
    assert cls.role == "method field"


@pytest.mark.parametrize("text", [
    ORIGINAL.replace("class RoleImageSerializer(ModelSerializer):\n    class Meta:\n",
                     "class RoleImageSerializer(ModelSerializer):\n    extra = 1\n\n    class Meta:\n"),
    ORIGINAL.replace("from rest_framework.serializers import ModelSerializer\n",
                     "from rest_framework.serializers import ModelSerializer, CharField\n"),
    ORIGINAL + ORIGINAL.split("\n\n\n", 1)[1],
])
def test_a_class_that_isnt_as_expected_stops_the_build_and_writes_nothing(monkeypatch, tmp_path, text):
    target = tmp_path / "serializers.py"
    target.write_text(text, encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["patch_topology_views.py", str(target)])
    with pytest.raises(SystemExit, match="isn't as expected"):
        m.main()
    assert target.read_text(encoding="utf-8") == text


def test_patching_twice_stops_the_second_time(monkeypatch, tmp_path):
    target = _patch(monkeypatch, tmp_path, ORIGINAL)
    with pytest.raises(SystemExit, match="isn't as expected"):
        m.main()
    assert target.read_text(encoding="utf-8").count("SerializerMethodField()") == 1


def test_without_a_path_it_finds_the_installed_plugin_or_stops(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["patch_topology_views.py"])
    monkeypatch.setattr(m.importlib.util, "find_spec", lambda name: None)
    with pytest.raises(SystemExit, match="netbox_topology_views isn't installed"):
        m.main()
