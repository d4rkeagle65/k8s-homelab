"""secret_words.py: the list it writes from an in-memory Key Vault, what stops it with the old
list left as it was, --check, --add-from, --if-older, where the vault and the list are found,
and that its config names cluster-secrets variables as Key Vault holds them. The boundaries
(the token, the REST calls, kubectl, the list's path) are replaced; every run also checks
that no word reaches the output."""

from __future__ import annotations

import base64
import importlib.util
import io
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from ruamel.yaml import YAML

SCRIPT = Path(__file__).resolve().parents[1] / "secret_words.py"
REPO = Path(__file__).resolve().parents[2]
VARIABLES = {"BASE-DOMAIN": "Example-Domain.TEST", "Child1": "childname"}
LISTED = "# a comment, kept\nAlpha Word\nbeta-host\nALPHA WORD\n"
WORDS = {"example-domain.test", "childname", "alpha word", "beta-host"}
CONFIG = 'key_vault_list = "SECRET-WORDS"\nvariables = ["BASE_DOMAIN", "Child1"]\n'


def load():
    spec = importlib.util.spec_from_file_location("secret_words_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    spec.loader.exec_module(module)
    return module


class FakeVault:
    """Key Vault's secrets API in memory: GET and PUT, each recorded; a name can be set to
    refuse reads, to get no answer, or to read back other than what was written."""

    def __init__(self, held, refuse_read=(), no_answer=(), corrupt=()):
        self.held = dict(held)
        self.refuse_read, self.no_answer, self.corrupt = set(refuse_read), set(no_answer), set(corrupt)
        self.calls = []
        self.Stop = Exception

    def __call__(self, method, url, token, body=None):
        assert token == "tok" and url.startswith("https://kv-test-01.vault.azure.net/secrets/")
        name = url.split("/secrets/")[1].split("?")[0]
        self.calls.append((method, name))
        if name in self.no_answer:
            raise self.Stop(f"{name}: no answer from Key Vault to a {method} (timed out)")
        if method == "GET":
            if name in self.refuse_read:
                return 403, {"error": {"code": "Forbidden"}}
            if name not in self.held:
                return 404, {"error": {"code": "SecretNotFound"}}
            return 200, {"value": self.held[name]}
        self.held[name] = body["value"] + ("x" if name in self.corrupt else "")
        self.tags = body.get("tags")
        return 200, {}


@pytest.fixture
def run(capsys, monkeypatch, tmp_path):
    """Runs main() with the boundaries replaced and the list at tmp_path/secret-words; returns
    (exit code, output, the list's path)."""
    config = tmp_path / "secret_words.toml"
    config.write_text(CONFIG, encoding="utf-8")
    path = tmp_path / "secret-words"

    def _run(argv, vault, stdin=""):
        m = load()
        monkeypatch.setattr(m, "CONFIG", config)
        monkeypatch.setattr(m, "list_path", lambda: path)
        monkeypatch.setattr(m.kv, "access_token", lambda: "tok")
        vault.Stop = m.kv.Stop
        monkeypatch.setattr(m.kv, "request", vault)
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
        code = m.main(["--vault", "kv-test-01", *argv])
        out = capsys.readouterr()
        text = (out.out + out.err).lower()
        for word in WORDS | {"new-word", "another-new"}:
            assert word not in text, "a word reached the output"
        return code, text, path

    return _run


def held(**extra):
    return {**VARIABLES, "SECRET-WORDS": LISTED, **extra}


def written(path):
    return load().parse(path.read_text(encoding="utf-8"))


m = load()


def test_config_names_cluster_secrets_variables_as_key_vault_holds_them():
    _, variables = m.load_config()
    spec = YAML(typ="safe", pure=True).load((REPO / "kubernetes/secrets/cluster-secrets.yaml").read_text(encoding="utf-8"))
    mapped = {d["secretKey"]: d["remoteRef"]["key"] for d in spec["spec"]["data"]}
    assert variables, "the config lists no variables"
    for variable in variables:
        assert variable in mapped, f"{variable} isn't a cluster-secrets variable"
        assert mapped[variable] == m.kv.keyvault_name(variable), f"{variable} is held under another Key Vault name"


def test_the_list_secret_stays_out_of_the_cluster():
    list_secret, _ = m.load_config()
    text = (REPO / "kubernetes/secrets/cluster-secrets.yaml").read_text(encoding="utf-8")
    assert f"key: {list_secret}\n" not in text


def test_parse_reads_a_list_as_the_hook_does():
    assert m.parse("# note\r\nAcme Widgets\r\n\r\n  host-one  \n  # indented note\nACME widgets\n") == {"acme widgets", "host-one"}


def test_a_refresh_writes_every_word_once_lowercased_and_sorted(run):
    code, text, path = run([], FakeVault(held()))
    assert code == 0
    body = path.read_text(encoding="utf-8")
    assert body.startswith("# Written by scripts/secret_words.py")
    assert [l for l in body.splitlines() if not l.startswith("#")] == sorted(WORDS)
    assert "\r" not in body
    assert "4 (2 from 2 variables, 2 from secret-words); 4 added here, 0 removed" in text


def test_a_refresh_reports_what_it_added_and_removed(run, tmp_path):
    (tmp_path / "secret-words").write_text("alpha word\nold-word\n", encoding="utf-8")
    code, text, path = run([], FakeVault(held()))
    assert code == 0 and written(path) == WORDS
    assert "3 added here, 1 removed" in text


@pytest.mark.parametrize("vault, named", [
    (FakeVault(held(), refuse_read={"Child1"}), "child1"),
    (FakeVault({"BASE-DOMAIN": VARIABLES["BASE-DOMAIN"], "SECRET-WORDS": LISTED}), "child1"),
    (FakeVault(VARIABLES), "secret-words"),
    (FakeVault(held(), no_answer={"SECRET-WORDS"}), "no answer"),
])
def test_a_read_that_doesnt_answer_stops_and_leaves_the_list(run, tmp_path, vault, named):
    (tmp_path / "secret-words").write_text("kept-word\n", encoding="utf-8")
    code, text, path = run([], vault)
    assert code == 2 and named in text and "1 words, left as it was" in text
    assert path.read_text(encoding="utf-8") == "kept-word\n"


def test_a_stop_with_no_list_says_the_rule_isnt_armed(run):
    code, text, path = run([], FakeVault(VARIABLES))
    assert code == 2 and "not armed" in text and not path.exists()


def test_a_value_too_short_is_left_out_and_named_by_its_variable(run):
    code, text, path = run([], FakeVault(held(Child1="abc")))
    assert code == 0 and "childname" not in written(path) and "abc" not in written(path)
    assert "left out, under 4 characters: child1" in text


def test_check_reports_a_difference_and_writes_nothing(run, tmp_path):
    list_file = tmp_path / "secret-words"
    list_file.write_text("alpha word\nold-word\n", encoding="utf-8")
    code, text, path = run(["--check"], FakeVault(held()))
    assert code == 1 and "2 here, 4 in key vault; 3 missing here, 1 only here" in text
    assert path.read_text(encoding="utf-8") == "alpha word\nold-word\n"


def test_check_passes_when_the_list_matches(run):
    vault = FakeVault(held())
    assert run([], vault)[0] == 0
    code, text, _ = run(["--check"], vault)
    assert code == 0 and "0 missing here, 0 only here" in text


def test_add_from_appends_only_new_words_and_reads_them_back(run, tmp_path):
    vault = FakeVault(held())
    adding = tmp_path / "old-list"
    adding.write_text("# a note\nALPHA WORD\nnew-word\nChildName\nab\n", encoding="utf-8")
    code, text, path = run(["--add-from", str(adding)], vault)
    assert code == 0
    assert vault.held["SECRET-WORDS"] == LISTED + "new-word\n"
    assert vault.tags == {"source": "secret-words"}
    assert "1 added, 1 already listed, 1 already a variable's value, 1 under 4 characters and not added; read back" in text
    assert written(path) == WORDS | {"new-word"}


def test_add_from_creates_the_list_secret_when_key_vault_has_none(run):
    vault = FakeVault(VARIABLES)
    code, _, path = run(["--add-from", "-"], vault, stdin="new-word\nanother-new\n")
    assert code == 0 and vault.held["SECRET-WORDS"] == "another-new\nnew-word\n"
    assert written(path) == {"example-domain.test", "childname", "new-word", "another-new"}


def test_add_from_with_nothing_new_writes_nothing_to_key_vault(run):
    vault = FakeVault(held())
    code, text, _ = run(["--add-from", "-"], vault, stdin="beta-host\n")
    assert code == 0 and ("PUT", "SECRET-WORDS") not in vault.calls and "0 added" in text


def test_a_write_that_reads_back_different_stops_and_leaves_the_list(run, tmp_path):
    (tmp_path / "secret-words").write_text("kept-word\n", encoding="utf-8")
    code, text, path = run(["--add-from", "-"], FakeVault(held(), corrupt={"SECRET-WORDS"}), stdin="new-word\n")
    assert code == 2 and "reads back a different value" in text
    assert path.read_text(encoding="utf-8") == "kept-word\n"


def test_if_older_skips_a_fresh_list_and_says_so(run, tmp_path):
    (tmp_path / "secret-words").write_text("kept-word\n", encoding="utf-8")
    vault = FakeVault(held())
    code, text, path = run(["--if-older", "12"], vault)
    assert code == 0 and vault.calls == [] and "key vault not read (--if-older 12)" in text


def test_if_older_reads_an_old_list_or_a_missing_one(run, tmp_path):
    vault = FakeVault(held())
    code, _, path = run(["--if-older", "12"], vault)
    assert code == 0 and written(path) == WORDS
    path.write_text("kept-word\n", encoding="utf-8")
    old = time.time() - 13 * 3600
    os.utime(path, (old, old))
    code, _, _ = run(["--if-older", "12"], vault)
    assert code == 0 and written(path) == WORDS


@pytest.mark.parametrize("flag", [["--check"], ["--add-from", "-"]])
def test_if_older_goes_only_with_a_plain_refresh(run, flag):
    with pytest.raises(SystemExit) as stop:
        run(["--if-older", "1", *flag], FakeVault(held()))
    assert stop.value.code == 2


def test_the_vault_comes_from_the_environment_then_the_bootstrap_secret(monkeypatch):
    module = load()
    monkeypatch.setenv("AZURE_KEYVAULT_URL", "https://kv-test-01.vault.azure.net/")
    assert module.vault_url(None) == "https://kv-test-01.vault.azure.net"
    monkeypatch.delenv("AZURE_KEYVAULT_URL")
    bootstrap = {"data": {"AZURE_KEYVAULT_URL": base64.b64encode(b"https://kv-test-02.vault.azure.net").decode()}}
    monkeypatch.setattr(module.kv, "kubectl_json", lambda context, *args: bootstrap)
    assert module.vault_url(None) == "https://kv-test-02.vault.azure.net"
    assert module.vault_url("kv-test-03") == "https://kv-test-03.vault.azure.net"


@pytest.mark.parametrize("url", ["http://kv-test-01.vault.azure.net", "https://example.com", "https://kv.vault.azure.net/x"])
def test_a_vault_url_of_another_shape_stops(monkeypatch, url):
    module = load()
    monkeypatch.setenv("AZURE_KEYVAULT_URL", url)
    with pytest.raises(module.kv.Stop, match="isn't a Key Vault URL"):
        module.vault_url(None)


def test_every_worktree_of_a_clone_finds_the_one_list(monkeypatch, tmp_path):
    main, tree = tmp_path / "main", tmp_path / "wt"
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "init.defaultBranch=main"]
    subprocess.run([*git, "init", "-q", str(main)], check=True)
    subprocess.run([*git, "-C", str(main), "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    subprocess.run([*git, "-C", str(main), "worktree", "add", "-q", "--detach", str(tree)], check=True)
    module = load()
    found = []
    for top in (main, tree):
        monkeypatch.setattr(module, "HERE", top)
        found.append(module.list_path().resolve())
    assert found[0] == found[1] == (main / ".git" / "secret-words").resolve()
