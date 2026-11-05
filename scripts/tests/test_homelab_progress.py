"""ansible/callback_plugins/homelab_progress.py: Ansible's default output with each task
numbered "n/total" and each play counted. Ansible's Block, its default callback and its
DataLoader are stood in for, so this runs wherever the suite does."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "ansible/callback_plugins/homelab_progress.py"


class Block:
    def __init__(self, block=(), rescue=(), always=()):
        self.block, self.rescue, self.always = list(block), list(rescue), list(always)


class Task:
    def __init__(self, name, action="debug", implicit=False):
        self.name, self.action, self.implicit, self._uuid = name, action, implicit, name

    def get_name(self):
        return self.name


class DefaultCallback:
    """Records each banner the default callback would print."""

    def __init__(self):
        self.banners = []
        self._task_type_cache = {}

    def v2_playbook_on_start(self, playbook):
        pass

    def v2_playbook_on_play_start(self, play):
        self.banners.append(f"PLAY [{play.name}]")

    def v2_playbook_on_include(self, included_file):
        pass

    def v2_playbook_on_task_start(self, task, is_conditional):
        self._last_task_name = task.get_name()
        self._print_task_banner(task)

    def _print_task_banner(self, task):
        self.banners.append(f"TASK [{self._last_task_name}]")


class Play:
    def __init__(self, name, items, gather_facts=True):
        self.name, self.items, self.gather_facts = name, items, gather_facts

    def compile(self):
        return self.items

    def get_name(self):
        return self.name


@pytest.fixture
def cb(monkeypatch):
    def module(name, **attrs):
        mod = types.ModuleType(name)
        mod.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, mod)
        return mod

    for name in ("ansible", "ansible.playbook", "ansible.plugins", "ansible.plugins.callback", "ansible.parsing"):
        module(name)
    module("ansible.playbook.block", Block=Block)
    module("ansible.plugins.callback.default", CallbackModule=DefaultCallback)

    class DataLoader:
        def load_from_file(self, path):
            return json.loads(Path(path).read_text(encoding="utf-8"))  # JSON is YAML

    module("ansible.parsing.dataloader", DataLoader=DataLoader)
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    spec = importlib.util.spec_from_file_location("homelab_progress_under_test", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_tasks_are_counted_through_blocks_without_rescues_or_implicit_meta(cb):
    items = [Task("a"), Block(block=[Task("b"), Block(block=[Task("c")], always=[Task("d")])],
                              rescue=[Task("never")], always=[Task("e")]),
             Task("flush_handlers", action="meta", implicit=True), Task("meta shown", action="meta")]
    assert cb.count_tasks(items) == 6
    assert cb.count_yaml_tasks([{"name": "a"}, {"block": [{"name": "b"}], "rescue": [{"name": "x"}],
                                                 "always": [{"name": "c"}]}, "not a task"]) == 3


def _play(cb, plays, *, tasks):
    run = cb.CallbackModule()
    run.v2_playbook_on_start(types.SimpleNamespace(get_plays=lambda: plays))
    for play in plays:
        run.v2_playbook_on_play_start(play)
        for t in tasks[play.name]:
            run.v2_playbook_on_task_start(t, False)
    return run


def test_each_task_banner_reads_n_of_total_and_each_play_its_count(cb):
    one = Play("Setup", [Task("a"), Task("b")])
    two = Play("Deploy", [Task("c")], gather_facts=False)
    run = _play(cb, [one, two], tasks={"Setup": [Task("Gathering Facts"), Task("a"), Task("b")], "Deploy": [Task("c")]})
    assert run.banners == ["PLAY [Setup (1/2)]", "TASK [1/3 - Gathering Facts]", "TASK [2/3 - a]", "TASK [3/3 - b]",
                           "PLAY [Deploy (2/2)]", "TASK [1/1 - c]"]
    assert one.name == "Setup", "the play's own name is put back"


def test_a_single_play_isnt_counted_and_a_total_never_runs_behind(cb):
    play = Play("Only", [Task("a")], gather_facts="no")
    run = _play(cb, [play], tasks={"Only": [Task("a"), Task("surprise")]})
    assert run.banners == ["PLAY [Only]", "TASK [1/1 - a]", "TASK [2/2 - surprise]"]


def test_an_include_adds_its_file_s_tasks_once_and_an_unreadable_one_makes_a_floor(cb, tmp_path):
    inc = tmp_path / "inc.yml"
    inc.write_text(json.dumps([{"name": "x"}, {"name": "y"}]), encoding="utf-8")
    run = cb.CallbackModule()
    run.v2_playbook_on_start(types.SimpleNamespace(get_plays=lambda: [1]))
    run.v2_playbook_on_play_start(Play("P", [Task("include")], gather_facts=False))
    run.v2_playbook_on_task_start(Task("include"), False)
    for _ in range(2):  # the same file included twice (a loop) is counted once
        run.v2_playbook_on_include(types.SimpleNamespace(_filename=str(inc)))
    run.v2_playbook_on_task_start(Task("x"), False)
    run.v2_playbook_on_include(types.SimpleNamespace(_filename=str(tmp_path / "missing.yml")))
    run.v2_playbook_on_task_start(Task("y"), False)
    assert run.banners[1:] == ["TASK [1/1 - include]", "TASK [2/3 - x]", "TASK [3/3+ - y]"]


def test_a_handler_s_banner_isnt_numbered(cb):
    run = _play(cb, [Play("P", [Task("a")], gather_facts=False)], tasks={"P": [Task("a")]})
    handler = Task("restart")
    run._task_type_cache[handler._uuid] = "RUNNING HANDLER"
    run._last_task_name = "restart"
    run._print_task_banner(handler)
    assert run.banners[-1] == "TASK [restart]"
