"""Ansible's default output, with each task numbered: "TASK [17/34 - Name]",
and each play counted: "PLAY [Name] (2/3)". Chosen as the stdout callback by
ansible.cfg at the repo root.

The total is every task the play runs when nothing fails (blocks and alwayses
included, rescues not, as they run only on a failure; the fact gathering
counted when the play gathers). Tasks
from include_tasks are only known when the include runs, so their file's tasks
are added then, and the total can grow mid-run. A loop is one task.
"""
from __future__ import annotations

import os

from ansible.playbook.block import Block
from ansible.plugins.callback.default import CallbackModule as DefaultCallback

DOCUMENTATION = """
    name: homelab_progress
    type: stdout
    short_description: default output, with each task numbered (17/34)
    description:
      - The default callback, whose task banners read "TASK [n/total - name]"
        and play banners end in "(n/plays)".
    extends_documentation_fragment:
      - default_callback
      - result_format_callback
    requirements:
      - set as stdout callback in configuration
"""


def count_tasks(items) -> int:
    """Tasks in a list of tasks and blocks, counted through nested blocks
    (rescues left out: they run only when something fails)."""
    n = 0
    for item in items or []:
        if isinstance(item, Block):
            n += count_tasks(item.block) + count_tasks(item.always)
        elif getattr(item, "action", None) == "meta" and getattr(item, "implicit", False):
            continue  # Ansible's own flush_handlers, never shown
        else:
            n += 1
    return n


def count_yaml_tasks(items) -> int:
    """The same for a task file read as YAML (an include's contents)."""
    n = 0
    for item in items or []:
        if isinstance(item, dict) and "block" in item:
            n += count_yaml_tasks(item.get("block")) + count_yaml_tasks(item.get("always"))
        elif isinstance(item, dict):
            n += 1
    return n


class CallbackModule(DefaultCallback):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "stdout"
    CALLBACK_NAME = "homelab_progress"

    def __init__(self):
        super().__init__()
        self._plays_total = 0
        self._play_n = 0
        self._task_n = 0
        self._task_total = 0
        self._counted_includes: set = set()
        self._total_is_floor = False

    def v2_playbook_on_start(self, playbook):
        try:
            self._plays_total = len(playbook.get_plays())
        except Exception:
            self._plays_total = 0
        super().v2_playbook_on_start(playbook)

    def v2_playbook_on_play_start(self, play):
        self._play_n += 1
        self._task_n = 0
        self._counted_includes = set()
        self._total_is_floor = False
        try:
            self._task_total = count_tasks(play.compile())
        except Exception:
            self._task_total = 0
        if play.gather_facts not in (False, "no", "false"):
            self._task_total += 1
        name = play.get_name().strip()
        if self._plays_total > 1:
            play.name = f"{name} ({self._play_n}/{self._plays_total})"
            try:
                super().v2_playbook_on_play_start(play)
            finally:
                play.name = name
        else:
            super().v2_playbook_on_play_start(play)

    def v2_playbook_on_include(self, included_file):
        path = getattr(included_file, "_filename", None)
        if path and path not in self._counted_includes:
            self._counted_includes.add(path)
            try:
                from ansible.parsing.dataloader import DataLoader
                self._task_total += count_yaml_tasks(DataLoader().load_from_file(path))
            except Exception:
                # Unreadable here: the total is then a floor, shown as "34+".
                self._total_is_floor = True
        super().v2_playbook_on_include(included_file)

    def _numbered(self, task) -> str:
        total = max(self._task_total, self._task_n)
        floor = "+" if self._total_is_floor else ""
        return f"{self._task_n}/{total}{floor} - {task.get_name().strip()}" if total else task.get_name().strip()

    def v2_playbook_on_task_start(self, task, is_conditional):
        self._task_n += 1
        super().v2_playbook_on_task_start(task, is_conditional)

    def _print_task_banner(self, task):
        # The default banner prints self._last_task_name: number it for this banner.
        name = getattr(self, "_last_task_name", None)
        handler = getattr(self, "_task_type_cache", {}).get(getattr(task, "_uuid", None)) == "RUNNING HANDLER"
        if self._task_n and not handler:
            self._last_task_name = self._numbered(task)
        try:
            super()._print_task_banner(task)
        finally:
            self._last_task_name = name
