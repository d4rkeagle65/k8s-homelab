"""Promoted Secrets: git gets the manifest with every value replaced by a
${VAR} placeholder; the value (base64, as the Secret stores it) goes into
the gitignored local substitutions file and so into the local
cluster-substitutions Secret. The manifest is only written once the live
cluster-substitutions Secret holds exactly those values -- before that,
Flux would overwrite the real Secret with empty (or stale) strings.

No cluster access: kube calls are monkeypatched, and everything writes to
a throwaway tree under tmp_path. Fake values are built at runtime so this
file itself never trips the repo's secret scanner.
"""

from __future__ import annotations

import base64

from ruamel.yaml import YAML

from k8s_backup import capture, kube, promote, varsub
from k8s_backup.filetracker import FileTracker
from k8s_backup.fluxowner import FluxOwnership
from k8s_backup.ownership import capture_owner
from k8s_backup.sink import Sink

_yaml = YAML(typ="safe")
APP = "kubernetes/apps/noip-duc/noip-duc/app"
SECRET_FILE = f"{APP}/secret-noip-duc-credentials.yaml"
USER_VAR = "${NOIP_DUC_CREDENTIALS_NOIP_USERNAME}"
PASS_VAR = "${NOIP_DUC_CREDENTIALS_NOIP_PASSWORD}"


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def _secret(name="noip-duc-credentials", ns="noip-duc", data=None, **extra):
    meta = {"name": name, "namespace": ns, **extra.pop("metadata", {})}
    return {"apiVersion": "v1", "kind": "Secret", "type": "Opaque", "metadata": meta,
            "data": data if data is not None else {"NOIP_USERNAME": _b64("someone"), "NOIP_PASSWORD": _b64("pw-" + "one")},
            **extra}


# ---- pure functions ------------------------------------------------------------

def test_placeholder_names_are_flux_variables():
    assert promote.secret_placeholder("noip-duc", "noip-duc-credentials", "NOIP_PASSWORD") == PASS_VAR
    assert promote.secret_placeholder("immich", "redis-password", "password") == "${IMMICH_REDIS_PASSWORD_PASSWORD}"
    assert promote.secret_placeholder("a", "a-tls", "tls.crt") == "${A_TLS_TLS_CRT}"
    assert promote.secret_placeholder("9ns", "9ns-x", "k") == "${_9NS_X_K}"


def test_generated_secrets_are_not_promoted():
    assert promote.generated_secret_reason(_secret(type="kubernetes.io/service-account-token"))
    assert promote.generated_secret_reason(_secret(type="helm.sh/release.v1"))
    assert promote.generated_secret_reason(_secret(metadata={"annotations": {"cert-manager.io/certificate-name": "c"}}))
    assert promote.generated_secret_reason(_secret(metadata={"labels": {"cnpg.io/cluster": "db"}}))
    certgen = _secret(name="ingress-nginx-admission", data={"ca": _b64("c"), "cert": _b64("c"), "key": _b64("k")})
    assert "kube-webhook-certgen" in promote.generated_secret_reason(certgen)
    assert promote.generated_secret_reason(_secret()) is None


def test_templated_secret_holds_no_values():
    secret = _secret(data={"NOIP_USERNAME": _b64("someone"), "NOIP_PASSWORD": _b64("pw-" + "one"), "EMPTY": ""},
                     metadata={"labels": {"app": "noip-duc"}})
    manifest, values = promote.templated_secret(secret)
    assert manifest["data"] == {"EMPTY": "", "NOIP_PASSWORD": PASS_VAR, "NOIP_USERNAME": USER_VAR}
    assert values == {PASS_VAR: _b64("pw-" + "one"), USER_VAR: _b64("someone")}
    assert manifest["metadata"]["labels"] == {"app": "noip-duc"}
    assert manifest["metadata"]["annotations"] == {promote.PRUNE_ANNOTATION: "disabled"}
    rendered = str(manifest)
    assert all(v not in rendered for v in values.values())


def test_sync_secret_entries_adds_updates_and_never_removes(tmp_path):
    source = promote.SECRET_SEED_SOURCE
    hand = [{"literal": "x", "placeholder": "${HAND}", "sensitivity": "secret"},
            {"literal": "mine", "placeholder": PASS_VAR, "sensitivity": "secret"}]  # hand-written: wins
    varsub._write_entries(tmp_path, hand)

    changes = varsub.sync_secret_entries(tmp_path, {USER_VAR: ("u1", "n"), PASS_VAR: ("p1", "n")}, source, False)
    assert changes == [f"added {USER_VAR} (n)"]
    by_ph = {e["placeholder"]: e for e in varsub.load_substitutions(tmp_path)}
    assert by_ph[USER_VAR]["literal"] == "u1" and by_ph[USER_VAR]["sensitivity"] == "secret"
    assert by_ph[PASS_VAR]["literal"] == "mine"

    before = varsub.local_substitutions_path(tmp_path).read_text()
    assert varsub.sync_secret_entries(tmp_path, {USER_VAR: ("u1", "n")}, source, False) == []
    assert varsub.local_substitutions_path(tmp_path).read_text() == before  # idempotent: not rewritten

    assert varsub.sync_secret_entries(tmp_path, {USER_VAR: ("u2", "n")}, source, False) == [
        f"updated {USER_VAR} (n): the live value changed"]
    assert {e["placeholder"] for e in varsub.load_substitutions(tmp_path)} == {"${HAND}", PASS_VAR, USER_VAR}
    assert varsub.render_secret(varsub.load_substitutions(tmp_path))["stringData"]["NOIP_DUC_CREDENTIALS_NOIP_USERNAME"] == "u2"


# ---- capture end to end -----------------------------------------------------------

def _mark(tmp_path):
    marker = tmp_path / "kubernetes/apps/noip-duc/noip-duc/.promote"
    marker.parent.mkdir(parents=True)
    marker.write_text("", encoding="utf-8")


def _serve(monkeypatch, secrets, live_substitutions):
    """`live_substitutions`: what the cluster-substitutions Secret's
    stringData held when applied (None: it doesn't exist)."""
    deployment = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "noip-duc", "namespace": "noip-duc"}}
    monkeypatch.setattr(kube, "get_all", lambda c, kind: ([deployment] if kind == "deployment" else [], None))
    monkeypatch.setattr(kube, "get_namespaced", lambda c, kind, ns: (secrets if kind == "secret" else [], None))
    monkeypatch.setattr(kube, "get_namespace", lambda c, ns: {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": ns}})

    def get_object(c, kind, ns, name):
        if live_substitutions is None:
            return None, "not found"
        return {"data": {k: _b64(v) for k, v in live_substitutions.items()}}, None

    monkeypatch.setattr(kube, "get_object", get_object)


def _run(tmp_path):
    warnings: list[str] = []
    prepared = capture._prepare_promoted_secrets(tmp_path, None, False, warnings, False, FluxOwnership([]))
    substitutions = varsub.load_substitutions(tmp_path)
    tracker = FileTracker(tmp_path, capture_owner(tmp_path))
    sink = Sink(tmp_path, False, tracker)
    capture._capture_namespaced_resources(sink, None, warnings, [], False, substitutions, FluxOwnership([]), prepared)
    tracker.sweep_orphans()
    return warnings


def test_secret_is_written_only_once_its_values_are_live(tmp_path, monkeypatch):
    _mark(tmp_path)
    user, password = _b64("someone"), _b64("pw-" + "one")

    # 1st run: values seeded locally, but the live substitution Secret
    # doesn't hold them yet -> no manifest, and a warning saying what to do.
    _serve(monkeypatch, [_secret()], live_substitutions=None)
    warnings = _run(tmp_path)
    assert not (tmp_path / SECRET_FILE).exists()
    assert any("not written yet" in w for w in warnings)
    local = varsub.render_secret(varsub.load_substitutions(tmp_path))["stringData"]
    assert local["NOIP_DUC_CREDENTIALS_NOIP_PASSWORD"] == password
    assert (tmp_path / f"{APP}/deployment-noip-duc.yaml").is_file()

    # After `kubectl apply` of the local Secret: the manifest is written,
    # placeholders only.
    _serve(monkeypatch, [_secret()], live_substitutions=local)
    warnings = _run(tmp_path)
    text = (tmp_path / SECRET_FILE).read_text()
    doc = _yaml.load(text)
    assert doc["data"] == {"NOIP_PASSWORD": PASS_VAR, "NOIP_USERNAME": USER_VAR}
    assert password not in text and user not in text
    assert not any("not written yet" in w for w in warnings)


def test_rotated_value_holds_the_manifest_until_reapplied(tmp_path, monkeypatch):
    _mark(tmp_path)
    _serve(monkeypatch, [_secret()], live_substitutions=None)
    _run(tmp_path)
    live = varsub.render_secret(varsub.load_substitutions(tmp_path))["stringData"]
    _serve(monkeypatch, [_secret()], live_substitutions=live)
    _run(tmp_path)
    before = (tmp_path / SECRET_FILE).read_text()

    # Password changed in the cluster; the substitution Secret still has the
    # old value, so Flux would revert it: keep the file as it was, re-seed.
    rotated = _secret(data={"NOIP_USERNAME": _b64("someone"), "NOIP_PASSWORD": _b64("pw-" + "two")})
    _serve(monkeypatch, [rotated], live_substitutions=live)
    warnings = _run(tmp_path)
    assert (tmp_path / SECRET_FILE).read_text() == before
    assert any("the live value changed" in w for w in warnings)
    assert varsub.render_secret(varsub.load_substitutions(tmp_path))["stringData"][
        "NOIP_DUC_CREDENTIALS_NOIP_PASSWORD"] == _b64("pw-" + "two")


def test_generated_secrets_stay_out(tmp_path, monkeypatch):
    _mark(tmp_path)
    tls = _secret(name="noip-duc-tls", type="kubernetes.io/tls",
                  metadata={"annotations": {"cert-manager.io/certificate-name": "noip-duc"}})
    _serve(monkeypatch, [tls], live_substitutions={})
    _run(tmp_path)
    assert not list((tmp_path / APP).glob("secret-*.yaml"))
    assert varsub.load_substitutions(tmp_path) == []
