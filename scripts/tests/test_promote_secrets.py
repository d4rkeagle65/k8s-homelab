"""Promoted Secrets: git gets the manifest with every value replaced by a
${VAR} placeholder; the value (base64, as the Secret stores it) belongs in
the cluster-secrets Vaultwarden item, which External Secrets turns into the
cluster-secrets Secret Flux substitutes from. Until that Secret holds
exactly those values, capture lists them in the gitignored
kubernetes/.local/vaultwarden-pending.yaml and holds the manifest back --
applying it early would overwrite the real Secret with empty (or stale)
strings.

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


# ---- capture end to end -----------------------------------------------------------

def _mark(tmp_path):
    marker = tmp_path / "kubernetes/apps/noip-duc/noip-duc/.promote"
    marker.parent.mkdir(parents=True)
    marker.write_text("", encoding="utf-8")


def _serve(monkeypatch, secrets, live):
    """`live`: what the cluster-secrets Secret holds (None: it doesn't
    exist yet)."""
    deployment = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "noip-duc", "namespace": "noip-duc"}}
    monkeypatch.setattr(kube, "get_all", lambda c, kind: ([deployment] if kind == "deployment" else [], None))
    monkeypatch.setattr(kube, "get_namespaced", lambda c, kind, ns: (secrets if kind == "secret" else [], None))
    monkeypatch.setattr(kube, "get_namespace", lambda c, ns: {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": ns}})

    def get_object(c, kind, ns, name):
        if kind == "secret" and name == varsub.SECRETS_SECRET_NAME and live is not None:
            return {"data": {k: _b64(v) for k, v in live.items()}}, None
        return None, "not found"

    monkeypatch.setattr(kube, "get_object", get_object)


def _run(tmp_path):
    """The promoted-Secret part of capture.run(): returns (warnings, the
    values pending for Vaultwarden as {name: value})."""
    warnings: list[str] = []
    substitutions = capture._load_substitutions(tmp_path, None, warnings)
    live_values = {varsub._var_name(s["placeholder"]): s["literal"] for s in substitutions}
    prepared, pending = capture._prepare_promoted_secrets(tmp_path, None, warnings, False, FluxOwnership([]), live_values)
    tracker = FileTracker(tmp_path, capture_owner(tmp_path))
    sink = Sink(tmp_path, False, tracker)
    capture._capture_namespaced_resources(
        sink, None, warnings, [], False, substitutions + pending, FluxOwnership([]), prepared, live_values
    )
    tracker.sweep_orphans()
    return warnings, {varsub._var_name(e["placeholder"]): e["literal"] for e in pending}


def test_secret_is_written_only_once_its_values_are_live(tmp_path, monkeypatch):
    _mark(tmp_path)
    user, password = _b64("someone"), _b64("pw-" + "one")

    # 1st run: cluster-secrets doesn't hold them yet -> no manifest, the
    # values listed for Vaultwarden, and a warning saying what to do.
    _serve(monkeypatch, [_secret()], live={})
    warnings, pending = _run(tmp_path)
    assert not (tmp_path / SECRET_FILE).exists()
    assert any("not written yet" in w for w in warnings)
    assert pending == {"NOIP_DUC_CREDENTIALS_NOIP_PASSWORD": password, "NOIP_DUC_CREDENTIALS_NOIP_USERNAME": user}
    assert (tmp_path / f"{APP}/deployment-noip-duc.yaml").is_file()

    # Once they're in Vaultwarden and synced: the manifest is written,
    # placeholders only, and nothing is pending.
    _serve(monkeypatch, [_secret()], live=pending)
    warnings, pending = _run(tmp_path)
    text = (tmp_path / SECRET_FILE).read_text()
    doc = _yaml.load(text)
    assert doc["data"] == {"NOIP_PASSWORD": PASS_VAR, "NOIP_USERNAME": USER_VAR}
    assert password not in text and user not in text
    assert not any("not written yet" in w for w in warnings)
    assert pending == {}


def test_rotated_value_holds_the_manifest_until_vaultwarden_has_it(tmp_path, monkeypatch):
    _mark(tmp_path)
    live = {"NOIP_DUC_CREDENTIALS_NOIP_USERNAME": _b64("someone"), "NOIP_DUC_CREDENTIALS_NOIP_PASSWORD": _b64("pw-" + "one")}
    _serve(monkeypatch, [_secret()], live=live)
    _run(tmp_path)
    before = (tmp_path / SECRET_FILE).read_text()

    # Password changed in the cluster; cluster-secrets still has the old
    # value, so Flux would revert it: keep the file as it was, list the new one.
    rotated = _secret(data={"NOIP_USERNAME": _b64("someone"), "NOIP_PASSWORD": _b64("pw-" + "two")})
    _serve(monkeypatch, [rotated], live=live)
    warnings, pending = _run(tmp_path)
    assert (tmp_path / SECRET_FILE).read_text() == before
    assert pending == {"NOIP_DUC_CREDENTIALS_NOIP_PASSWORD": _b64("pw-" + "two")}


def test_generated_secrets_stay_out(tmp_path, monkeypatch):
    _mark(tmp_path)
    tls = _secret(name="noip-duc-tls", type="kubernetes.io/tls",
                  metadata={"annotations": {"cert-manager.io/certificate-name": "noip-duc"}})
    _serve(monkeypatch, [tls], live={})
    _, pending = _run(tmp_path)
    assert not list((tmp_path / APP).glob("secret-*.yaml"))
    assert pending == {}


# ---- where the values come from ---------------------------------------------------

def test_live_values_replace_but_settings_never_do():
    secret = {"data": {"BASE_DOMAIN": _b64("example.com"), "EMPTY": ""}}
    settings = {"data": {"MEDIA_PUID": "1000", "SUBDOMAIN_SUFFIX": ""}}
    subs = varsub.from_live(secret, settings)
    assert {s["placeholder"]: s["replace"] for s in subs} == {
        # Every name is known (so ${EMPTY} isn't escaped), but an empty value
        # would match everywhere, so it's never replaced.
        "${BASE_DOMAIN}": True, "${EMPTY}": False, "${MEDIA_PUID}": False, "${SUBDOMAIN_SUFFIX}": False}

    doc = {"size": "1000Mi", "host": "app.example.com"}
    varsub.apply_substitutions(doc, subs)
    assert doc == {"size": "1000Mi", "host": "app.${BASE_DOMAIN}"}
    assert varsub.apply_substitutions_text("uid 1000 at example.com", subs) == "uid 1000 at ${BASE_DOMAIN}"


def test_without_cluster_secrets_the_legacy_file_is_used(tmp_path, monkeypatch):
    varsub._write_entries(tmp_path, [{"literal": "example.com", "placeholder": "${BASE_DOMAIN}"}])
    monkeypatch.setattr(kube, "get_object", lambda c, kind, ns, name: (None, "not found"))
    warnings: list[str] = []
    subs = capture._load_substitutions(tmp_path, None, warnings)
    assert [s["placeholder"] for s in subs] == ["${BASE_DOMAIN}"]
    assert any("legacy" in w for w in warnings)


def test_auto_seed_only_proposes_new_literals():
    issuer = {"metadata": {"name": "le"}, "spec": {"acme": {"email": "ops@example.com"}}}
    known = varsub.from_live({"data": {"ACME_EMAIL": _b64("ops@example.com")}}, None)
    assert varsub.auto_seed_candidates({"clusterissuer.cert-manager.io": [issuer]}, [], known) == []
    new = varsub.auto_seed_candidates({"clusterissuer.cert-manager.io": [issuer]}, [], [])
    assert [(e["literal"], e["placeholder"]) for e in new] == [("ops@example.com", "${ACME_EMAIL}")]


def test_pending_and_cache_files(tmp_path):
    entries = varsub.from_live({"data": {"BASE_DOMAIN": _b64("example.com")}}, {"data": {"MEDIA_PUID": "1000"}})
    varsub.write_cache(tmp_path, entries, dry_run=False)
    assert {s["placeholder"]: s["replace"] for s in varsub.load_cache(tmp_path)} == {
        "${BASE_DOMAIN}": True, "${MEDIA_PUID}": False}

    assert varsub.write_pending(tmp_path, [], dry_run=False) is None
    path = varsub.write_pending(tmp_path, [{"literal": "v", "placeholder": "${NEW}", "note": "n"}], dry_run=False)
    assert _yaml.load(path.read_text()) == [{"name": "NEW", "value": "v", "note": "n"}]
