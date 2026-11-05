"""garage-backup-exporter.py: the newest object under each backup prefix in Garage, for
Prometheus. Against a stand-in for S3 on the loaded module; no Garage is reached."""

from __future__ import annotations

import datetime
import importlib.util
import io
import sys
import urllib.error
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / (
    "kubernetes/prod/system/monitoring/garage-backup-exporter/app/garage-backup-exporter.py")
CFG = {"endpoint": "http://garage.example.invalid:3900", "region": "garage", "key_id": "GKtest",
       "secret": "not-" + "a-real-one"}


def load():
    spec = importlib.util.spec_from_file_location("garage_backup_exporter_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    spec.loader.exec_module(module)
    return module


m = load()


def _page(objects, token=None, truncated=None):
    items = "".join(f"<Contents><Key>{k}</Key><LastModified>{t}</LastModified><Size>{s}</Size></Contents>"
                    for k, t, s in objects)
    trunc = "true" if (token or truncated) else "false"
    nxt = f"<NextContinuationToken>{token}</NextContinuationToken>" if token else ""
    return (f'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">{items}'
            f"<IsTruncated>{trunc}</IsTruncated>{nxt}</ListBucketResult>").encode()


def test_prefix_of_takes_the_leading_folders_or_databasus_s_name():
    assert m.prefix_of("cnpg/babybuddy-db/base/2026/x.tar", 2) == "cnpg/babybuddy-db"
    assert m.prefix_of("cnpg/babybuddy-db/x.tar", 1) == "cnpg"
    assert m.prefix_of("Kea-20261019-200000-abc.sql.gz", 1) == "Kea"
    assert m.prefix_of("notes.txt", 1) == "(top)"


def test_summarize_keeps_the_newest_object_per_prefix_and_totals():
    got = m.summarize([("a/1", 100.0, 10), ("a/2", 300.0, 30), ("a/3", 200.0, 20), ("b/1", 50.0, 5)], 1)
    assert got == {"a": {"newest": 300.0, "newest_size": 30, "objects": 3, "bytes": 60},
                   "b": {"newest": 50.0, "newest_size": 5, "objects": 1, "bytes": 5}}


def test_parse_buckets_reads_names_and_depths_and_refuses_none():
    assert m.parse_buckets("cnpg:2, databasus ,") == [("cnpg", 2), ("databasus", 1)]
    with pytest.raises(SystemExit):
        m.parse_buckets(" , ")


def test_list_objects_follows_continuation_tokens(monkeypatch):
    pages = {None: _page([("a/1", "2026-11-01T00:00:00Z", 1)], token="t2"),
             "t2": _page([("a/2", "2026-11-02T00:00:00.000Z", 2)])}
    asked = []

    def get(endpoint, region, key_id, secret, path, query):
        asked.append((path, query.get("continuation-token")))
        return pages[query.get("continuation-token")]

    monkeypatch.setattr(m, "signed_get", get)
    got = list(m.list_objects(CFG, "cnpg"))
    assert [(k, s) for k, _, s in got] == [("a/1", 1), ("a/2", 2)]
    assert got[1][1] == datetime.datetime(2026, 11, 2, tzinfo=datetime.timezone.utc).timestamp()
    assert asked == [("/cnpg", None), ("/cnpg", "t2")]


def test_a_truncated_listing_without_a_token_fails(monkeypatch):
    monkeypatch.setattr(m, "signed_get", lambda *a: _page([], truncated=True))
    with pytest.raises(m.ListFailed, match="without a continuation token"):
        list(m.list_objects(CFG, "cnpg"))


def test_a_failed_bucket_is_down_with_no_stale_prefixes(monkeypatch, capsys):
    def get(endpoint, region, key_id, secret, path, query):
        if path == "/broken":
            raise m.ListFailed("/broken: HTTP 403 AccessDenied")
        return _page([("a/1", "2026-11-01T00:00:00Z", 7)])

    monkeypatch.setattr(m, "signed_get", get)
    results = m.refresh(CFG, [("cnpg", 1), ("broken", 1)])
    assert results["broken"] == {"ok": False} and results["cnpg"]["ok"] is True
    text = m.render(results, 1234.0)
    assert 'garage_backup_bucket_up{bucket="broken"} 0' in text and 'garage_backup_bucket_up{bucket="cnpg"} 1' in text
    assert 'garage_backup_objects{bucket="cnpg",prefix="a"} 1' in text
    assert 'bucket="broken",prefix' not in text
    assert "garage_backup_last_refresh_timestamp_seconds 1234.0" in text
    assert "listing broken failed" in capsys.readouterr().err


def test_render_escapes_label_values():
    text = m.render({'b"\\': {"ok": True, "prefixes": {"p\n": {"newest": 1.0, "newest_size": 2, "objects": 3, "bytes": 4}}}}, 0)
    assert 'garage_backup_bytes{bucket="b\\"\\\\",prefix="p\\n"} 4' in text


OTHER_SIGNING_VALUE = "another-" + "one"
SIGNATURE = "e824f31acf93cbdc6434ad73bb4a0bc7" "1db915e625647e9491b5328db9158949"


class _FixedClock(datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime.datetime(2026, 11, 4, 12, 0, 0, tzinfo=datetime.timezone.utc)


def _signed_request(monkeypatch, secret=CFG["secret"], query=None):
    sent = []

    class Answer(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(m.datetime, "datetime", _FixedClock)
    monkeypatch.setattr(m.urllib.request, "urlopen", lambda req, timeout: sent.append(req) or Answer(b"<ok/>"))
    assert m.signed_get(CFG["endpoint"], CFG["region"], CFG["key_id"], secret, "/cnpg",
                        query or {"list-type": "2", "max-keys": "1000"}) == b"<ok/>"
    return sent[0]


def test_a_request_is_signed_for_the_bucket_scope_and_the_secret(monkeypatch):
    req = _signed_request(monkeypatch)
    auth = req.get_header("Authorization")
    assert auth.startswith("AWS4-HMAC-SHA256 Credential=GKtest/20261104/garage/s3/aws4_request, "
                           "SignedHeaders=host;x-amz-content-sha256;x-amz-date, Signature=")
    assert req.get_header("X-amz-date") == "20261104T120000Z"
    assert req.full_url == "http://garage.example.invalid:3900/cnpg?list-type=2&max-keys=1000"
    signature = auth.rsplit("=", 1)[1]
    # What today's signing gives for these inputs, which Garage accepts: any change to how a
    # request is signed changes it.
    assert signature == SIGNATURE
    assert _signed_request(monkeypatch).get_header("Authorization") == auth, "the same request signs the same"
    other = _signed_request(monkeypatch, secret=OTHER_SIGNING_VALUE).get_header("Authorization")
    assert other.rsplit("=", 1)[1] != signature, "the signature depends on the secret"
    assert CFG["secret"] not in auth


def test_a_refused_request_names_s3_s_code(monkeypatch):
    def refuse(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {},
                                     io.BytesIO(b"<Error><Code>AccessDenied</Code></Error>"))

    monkeypatch.setattr(m.urllib.request, "urlopen", refuse)
    with pytest.raises(m.ListFailed, match="/cnpg: HTTP 403 AccessDenied"):
        m.signed_get(CFG["endpoint"], CFG["region"], CFG["key_id"], CFG["secret"], "/cnpg", {})


def test_once_prints_the_metrics_and_exits(monkeypatch, capsys):
    for k, v in {"S3_ENDPOINT": CFG["endpoint"] + "/", "AWS_ACCESS_KEY_ID": "GKtest",
                 "AWS_SECRET_ACCESS_KEY": CFG["secret"], "BUCKETS": "cnpg:1"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(sys, "argv", ["garage-backup-exporter.py", "--once"])
    seen = []
    monkeypatch.setattr(m, "signed_get", lambda endpoint, *a: seen.append(endpoint) or _page([("a/1", "2026-11-01T00:00:00Z", 7)]))
    m.main()
    assert 'garage_backup_bucket_up{bucket="cnpg"} 1' in capsys.readouterr().out
    assert seen == [CFG["endpoint"]], "the endpoint without its trailing slash"


def test_the_query_is_signed_and_sent_in_sorted_order(monkeypatch):
    req = _signed_request(monkeypatch, query={"list-type": "2", "max-keys": "1000", "continuation-token": "a/b c"})
    assert req.full_url.endswith("/cnpg?continuation-token=a%2Fb%20c&list-type=2&max-keys=1000")
