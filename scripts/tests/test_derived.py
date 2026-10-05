"""derived.py: the data and exposure labels capture works out from the cluster."""

from __future__ import annotations

from k8s_backup import derived


def _obj(ns, **spec):
    return {"metadata": {"namespace": ns}, "spec": spec}


def _ingress(ns, cls, *hosts):
    return _obj(ns, ingressClassName=cls, rules=[{"host": h} for h in hosts])


def test_data_is_database_over_files_over_stateless():
    pvcs = [_obj("immich"), _obj("emby")]
    clusters = [_obj("immich")]
    assert derived.data_class("immich", pvcs, clusters) == "database"
    assert derived.data_class("emby", pvcs, clusters) == "files"
    assert derived.data_class("noip-duc", pvcs, clusters) == "stateless"


def test_data_is_unknown_when_a_list_could_not_be_read():
    assert derived.data_class("immich", None, []) is None
    assert derived.data_class("immich", [], None) is None


ROUTES = [
    _ingress("traefik", "cloudflare-tunnel", "bb.example.org"),   # tunnel route on to Traefik
    _ingress("babybuddy", "traefik", "bb.example.org"),
    _ingress("immich", "cloudflare-tunnel", "photos.example.org"),  # tunnel straight to the app
    _ingress("immich", "traefik", "photos.example.org"),
    _ingress("mealie", "traefik", "mealie.example.org"),
    _ingress("manictime", "traefik-isolated", "mt.example.org"),
]


def test_exposure_follows_the_tunnel_routes():
    assert derived.exposure("babybuddy", ROUTES) == "internet"   # its host is on the tunnel
    assert derived.exposure("immich", ROUTES) == "internet"      # a tunnel Ingress of its own
    assert derived.exposure("mealie", ROUTES) == "lan"
    assert derived.exposure("manictime", ROUTES) == "lan"        # traefik-isolated is still local
    assert derived.exposure("cnpg-system", ROUTES) == "cluster"
    assert derived.exposure("mealie", None) is None


def test_namespace_labels_leave_out_what_could_not_be_worked_out():
    full = derived.namespace_labels("immich", pvcs=[_obj("immich")], clusters=[], ingresses=ROUTES)
    assert full == {derived.DATA_LABEL: "files", derived.EXPOSURE_LABEL: "internet"}
    partial = derived.namespace_labels("immich", pvcs=None, clusters=[], ingresses=ROUTES)
    assert partial == {derived.EXPOSURE_LABEL: "internet"}


def _setup(tmp_path, monkeypatch, *, ingress_error=False):
    from k8s_backup import capture, kube, layout, yamlio
    from k8s_backup.filetracker import FileTracker
    from k8s_backup.ownership import capture_owner
    from k8s_backup.sink import Sink

    files = {
        "kubernetes/prod/apps/immich/namespace.yaml": {"apiVersion": "v1", "kind": "Namespace", "metadata": {
            "name": "immich", "labels": {layout.CATEGORY_LABEL: "apps", derived.EXPOSURE_LABEL: "lan"}}},
        "kubernetes/prod/apps/media/namespace.yaml": {"apiVersion": "v1", "kind": "Namespace", "metadata": {
            "name": "media", "labels": {layout.CATEGORY_LABEL: "apps"}}},
        "kubernetes/prod/apps/media/sonarr/.handwritten": None,
    }
    for rel, doc in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if doc is None:
            path.write_text("", encoding="utf-8")
        else:
            yamlio.write_yaml_file(path, doc)
    cluster = {
        "persistentvolumeclaim": ([_obj("immich"), _obj("media")], None),
        "clusters.postgresql.cnpg.io": ([_obj("immich")], None),
        "ingress": (([], "could not list 'ingress': forbidden") if ingress_error
                    else ([_ingress("immich", "cloudflare-tunnel", "p.example.org")], None)),
    }
    monkeypatch.setattr(kube, "get_all", lambda context, kind: cluster[kind])
    tracker = FileTracker(tmp_path, capture_owner(tmp_path))
    sink = Sink(tmp_path, False, tracker)
    # This run wrote immich's namespace.yaml; media's is hand-written (protected).
    ns_path = tmp_path / "kubernetes/prod/apps/immich/namespace.yaml"
    sink.write_yaml(ns_path, yamlio.read_yaml_file(ns_path))
    warnings = []
    capture._apply_derived_labels(sink, None, warnings)
    read = lambda ns: yamlio.read_yaml_file(tmp_path / f"kubernetes/prod/apps/{ns}/namespace.yaml")["metadata"]["labels"]
    return read, warnings


def test_capture_labels_what_it_wrote_and_only_warns_for_hand_written(tmp_path, monkeypatch):
    read, warnings = _setup(tmp_path, monkeypatch)
    assert read("immich")[derived.DATA_LABEL] == "database"
    assert read("immich")[derived.EXPOSURE_LABEL] == "internet"
    assert derived.DATA_LABEL not in read("media")  # never rewritten
    assert any("media/namespace.yaml is hand-written: set" in w and "data: files" in w for w in warnings)


def test_capture_keeps_a_label_it_could_not_work_out(tmp_path, monkeypatch):
    read, warnings = _setup(tmp_path, monkeypatch, ingress_error=True)
    assert read("immich")[derived.EXPOSURE_LABEL] == "lan"  # the file's value, not a guess
    assert read("immich")[derived.DATA_LABEL] == "database"
    assert any("could not list Ingresses" in w for w in warnings)
