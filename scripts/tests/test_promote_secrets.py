"""Promoted Secrets: git gets the manifest with every value replaced by a
${VAR} placeholder, which Flux fills from the cluster-secrets Secret with
the value's base64, as the Secret stores it. Key Vault holds the value
plain, and cluster-secrets' template encodes it. Until that Secret holds
exactly those values, capture lists them, plain and with their template
entries, in the gitignored kubernetes/.local/vaultwarden-pending.yaml and
holds the manifest back -- applying it early would overwrite the real
Secret with empty (or stale) strings.

No cluster access: kube calls are monkeypatched, and everything writes to
a throwaway tree under tmp_path. Fake values are built at runtime so this
file itself never trips the repo's secret scanner.
"""

from __future__ import annotations

import base64

from ruamel.yaml import YAML

from k8s_backup import capture, kube, layout, promote, varsub
from k8s_backup.filetracker import FileTracker
from k8s_backup.fluxowner import FluxOwnership
from k8s_backup.ownership import capture_owner
from k8s_backup.sink import Sink

_yaml = YAML(typ="safe")
APP = "kubernetes/prod/system/noip-duc/noip-duc/app"
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
    marker = tmp_path / "kubernetes/prod/system/noip-duc/noip-duc/.promote"
    marker.parent.mkdir(parents=True)
    marker.write_text("", encoding="utf-8")


def _serve(monkeypatch, secrets, live):
    """`live`: what the cluster-secrets Secret holds (None: it doesn't
    exist yet)."""
    deployment = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "noip-duc", "namespace": "noip-duc"}}
    monkeypatch.setattr(kube, "get_all", lambda c, kind: ([deployment] if kind == "deployment" else [], None))
    monkeypatch.setattr(kube, "get_namespaced", lambda c, kind, ns: (secrets if kind == "secret" else [], None))
    monkeypatch.setattr(kube, "get_namespace", lambda c, ns: {
        "apiVersion": "v1", "kind": "Namespace", "metadata": {"name": ns, "labels": {layout.CATEGORY_LABEL: "system"}}})

    def get_object(c, kind, ns, name):
        if kind == "secret" and name == varsub.SECRETS_SECRET_NAME and live is not None:
            return {"data": {k: _b64(v) for k, v in live.items()}}, None
        return None, f'Error from server (NotFound): {kind}s "{name}" not found'

    monkeypatch.setattr(kube, "get_object", get_object)


def _run(tmp_path):
    """The promoted-Secret part of capture.run(): returns (warnings, the
    values pending for Key Vault as {name: the literal capture replaces})."""
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
    # values listed for Key Vault, and a warning saying what to do. The
    # literal capture replaces is the base64, as the manifest holds it.
    _serve(monkeypatch, [_secret()], live={})
    warnings, pending = _run(tmp_path)
    assert not (tmp_path / SECRET_FILE).exists()
    assert any("not written yet" in w for w in warnings)
    assert pending == {"NOIP_DUC_CREDENTIALS_NOIP_PASSWORD": password, "NOIP_DUC_CREDENTIALS_NOIP_USERNAME": user}
    assert (tmp_path / f"{APP}/deployment-noip-duc.yaml").is_file()

    # Once they're in Key Vault and synced: the manifest is written,
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
    not_found = 'Error from server (NotFound): secrets "cluster-secrets" not found'
    monkeypatch.setattr(kube, "get_object", lambda c, kind, ns, name: (None, not_found))
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


def test_pending_and_cache_rows():
    entries = varsub.from_live({"data": {"BASE_DOMAIN": _b64("example.com")}}, {"data": {"MEDIA_PUID": "1000"}})
    assert varsub.cache_rows(entries) == [
        {"literal": "1000", "placeholder": "${MEDIA_PUID}", "replace": False},
        {"literal": "example.com", "placeholder": "${BASE_DOMAIN}", "replace": True},
    ]
    assert varsub.pending_rows([{"literal": "v", "placeholder": "${NEW}", "note": "n"}]) == [
        {"name": "NEW", "value": "v", "note": "n"}]


def test_a_secrets_pending_value_is_plain_with_its_template_entry():
    # Key Vault holds every value plain; the template encodes a Secret's back into
    # the base64 its data: takes, so the literal (what capture replaces) stays base64.
    entry = {"literal": _b64("pw-" + "one"), "placeholder": PASS_VAR, "note": "n", "secret_data": True}
    assert varsub.pending_rows([entry]) == [{
        "name": "NOIP_DUC_CREDENTIALS_NOIP_PASSWORD", "value": "pw-" + "one",
        "template": "{{ .NOIP_DUC_CREDENTIALS_NOIP_PASSWORD | b64enc }}", "note": "n"}]
    assert entry["literal"] == _b64("pw-" + "one")
    # Key Vault holds only text, so a value that isn't stays base64, untemplated.
    binary = {**entry, "literal": base64.b64encode(b"\xff\xfe").decode()}
    [row] = varsub.pending_rows([binary])
    assert row["value"] == binary["literal"] and "template" not in row
    assert row["note"].startswith("n; not text")


# ---- capture.run end to end ----------------------------------------------------

def _fake_cluster(monkeypatch, secret_result):
    """A cluster with one Ingress domain and one ACME email, and
    cluster-secrets answering `secret_result` ((object, error))."""
    ingresses = [{"spec": {"rules": [{"host": f"{a}.example.org"} for a in ("a", "b")]}}]
    issuer = {"metadata": {"name": "le"}, "spec": {"acme": {"email": "ops@example.org"}}}
    objects = {"ingress": ingresses, "clusterissuer.cert-manager.io": [issuer]}
    monkeypatch.setattr(kube, "get_all", lambda c, kind: (objects.get(kind, []), None))
    monkeypatch.setattr(kube, "get_object", lambda c, kind, ns, name: secret_result if kind == "secret" else (None, "x"))
    for name, value in {
        "current_context": lambda: "test", "get_namespaced": lambda c, k, ns: ([], None), "list_nodes": lambda c: [],
        "list_namespaces": lambda c: [], "get_namespace": lambda c, ns: None, "list_secrets": lambda c: [],
        "list_crds": lambda c: [], "server_version": lambda c: {"serverVersion": {"gitVersion": "v1"}},
    }.items():
        monkeypatch.setattr(kube, name, value)
    monkeypatch.setattr(capture.helmcli, "repo_list", lambda c: [])
    monkeypatch.setattr(capture.helmcli, "list_releases", lambda c: [])
    monkeypatch.setattr(capture.helmcli, "repo_update", lambda c: None)


def test_capture_keeps_its_cache_and_lists_pending_values(tmp_path, monkeypatch):
    live = ({"data": {"BASE_DOMAIN": _b64("example.org")}}, None)
    _fake_cluster(monkeypatch, live)
    cache = tmp_path / "kubernetes/.local/substitutions-cache.yaml"
    pending = tmp_path / "kubernetes/.local/vaultwarden-pending.yaml"

    report, _ = capture.run(tmp_path, None, False, False)
    assert any("aren't in cluster-secrets yet (ACME_EMAIL)" in w for w in report.warnings)
    # Not from a Secret's data:, so as found, with no template entry.
    assert _yaml.load(pending.read_text()) == [
        {"name": "ACME_EMAIL", "value": "ops@example.org", "note": "ACME account email (ClusterIssuer le)"}]
    inventory = (tmp_path / "docs/inventory.md").read_text()
    assert "example.org" not in inventory  # neither the known nor the pending value

    # A second run must not sweep its own files (they were once written
    # outside the file tracker, and every later run deleted them).
    capture.run(tmp_path, None, False, False)
    assert cache.is_file() and pending.is_file()
    assert {e["placeholder"] for e in varsub.load_cache(tmp_path)} >= {"${BASE_DOMAIN}", "${ACME_EMAIL}"}

    # Once cluster-secrets has it, the pending file goes.
    _fake_cluster(monkeypatch, ({"data": {"BASE_DOMAIN": _b64("example.org"), "ACME_EMAIL": _b64("ops@example.org")}}, None))
    capture.run(tmp_path, None, False, False)
    assert cache.is_file() and not pending.exists()


def test_capture_lists_a_promoted_secrets_values_plain_with_their_template(tmp_path, monkeypatch):
    _mark(tmp_path)
    _fake_cluster(monkeypatch, ({"data": {"BASE_DOMAIN": _b64("example.org"), "ACME_EMAIL": _b64("ops@example.org")}}, None))
    monkeypatch.setattr(kube, "get_namespaced", lambda c, kind, ns: ([_secret()] if kind == "secret" else [], None))
    monkeypatch.setattr(kube, "get_namespace", lambda c, ns: {
        "apiVersion": "v1", "kind": "Namespace", "metadata": {"name": ns, "labels": {layout.CATEGORY_LABEL: "system"}}})
    capture.run(tmp_path, None, False, False)
    rows = _yaml.load((tmp_path / "kubernetes/.local/vaultwarden-pending.yaml").read_text())
    assert {r["name"]: (r["value"], r.get("template")) for r in rows} == {
        "NOIP_DUC_CREDENTIALS_NOIP_PASSWORD": ("pw-" + "one", "{{ .NOIP_DUC_CREDENTIALS_NOIP_PASSWORD | b64enc }}"),
        "NOIP_DUC_CREDENTIALS_NOIP_USERNAME": ("someone", "{{ .NOIP_DUC_CREDENTIALS_NOIP_USERNAME | b64enc }}"),
    }
    # The cache, which the leak check reads, keeps the literal the manifests hold.
    cached = {e["placeholder"]: e["literal"] for e in varsub.load_cache(tmp_path)}
    assert cached[PASS_VAR] == _b64("pw-" + "one")


def test_capture_stops_if_cluster_secrets_cannot_be_read(tmp_path, monkeypatch):
    _fake_cluster(monkeypatch, (None, "could not get secret flux-system/cluster-secrets: Unable to connect to the server"))
    try:
        capture.run(tmp_path, None, False, False)
    except capture.ToolError as exc:
        assert "Nothing was written" in str(exc)
    else:
        raise AssertionError("capture carried on without the cluster-secrets values")
    assert not (tmp_path / "kubernetes").exists() or not any((tmp_path / "kubernetes").rglob("*.yaml"))


def test_capture_falls_back_only_when_cluster_secrets_does_not_exist(tmp_path, monkeypatch):
    varsub._write_entries(tmp_path, [{"literal": "example.org", "placeholder": "${BASE_DOMAIN}"}])
    _fake_cluster(monkeypatch, (None, 'could not get secret flux-system/cluster-secrets: Error from server (NotFound): secrets "cluster-secrets" not found'))
    report, _ = capture.run(tmp_path, None, False, False)
    assert any("legacy" in w for w in report.warnings)
