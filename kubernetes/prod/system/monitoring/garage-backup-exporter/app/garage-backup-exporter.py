"""Garage's backup buckets as Prometheus metrics: the newest object under each backup's prefix.

Each bucket in BUCKETS ("name:depth,..."; depth is how many leading folders name one
backup, prefix_of) is listed read-only every REFRESH_SECONDS, and /metrics serves the last
listing: per prefix the newest object's time and size, the object count and the bytes. A
listing that fails sets garage_backup_bucket_up 0 for that bucket and drops its prefix
series rather than serving stale ones as current.

Standard library only: S3's ListObjectsV2, signed with AWS Signature Version 4.

Environment: S3_ENDPOINT (http://host:port), S3_REGION (Garage's s3_region),
AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, BUCKETS, REFRESH_SECONDS (default 600), PORT
(default 8080).

    garage-backup-exporter.py            serve /metrics and /healthz
    garage-backup-exporter.py --once     list once, print the metrics, and exit
"""
from __future__ import annotations

import datetime
import hashlib
import hmac
import http.server
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
STAMPED = re.compile(r"(.+?)-\d{8}-\d{6}-")


class ListFailed(Exception):
    pass


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def signed_get(endpoint: str, region: str, key_id: str, secret: str, path: str, query: dict) -> bytes:
    """One SigV4-signed GET; raises ListFailed on any answer but 200."""
    url = urllib.parse.urlsplit(endpoint)
    host = url.netloc
    now = datetime.datetime.now(datetime.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    day = now.strftime("%Y%m%d")
    canonical_query = "&".join(
        f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(str(v), safe='-_.~')}"
        for k, v in sorted(query.items()))
    payload_hash = hashlib.sha256(b"").hexdigest()
    headers = {"host": host, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date}
    signed = ";".join(sorted(headers))
    canonical = "\n".join([
        "GET", urllib.parse.quote(path, safe="/-_.~"), canonical_query,
        "".join(f"{k}:{headers[k]}\n" for k in sorted(headers)), signed, payload_hash])
    scope = f"{day}/{region}/s3/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope,
                         hashlib.sha256(canonical.encode("utf-8")).hexdigest()])
    k = _sign(_sign(_sign(_sign(("AWS4" + secret).encode("utf-8"), day), region), "s3"), "aws4_request")
    signature = hmac.new(k, to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    req = urllib.request.Request(
        f"{url.scheme}://{host}{path}?{canonical_query}",
        headers={"x-amz-content-sha256": payload_hash, "x-amz-date": amz_date,
                 "Authorization": f"AWS4-HMAC-SHA256 Credential={key_id}/{scope}, "
                                  f"SignedHeaders={signed}, Signature={signature}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        code = ET.fromstring(e.read() or b"<x/>").findtext("Code") or ""
        raise ListFailed(f"{path}: HTTP {e.code} {code}".strip()) from None
    except OSError as e:
        raise ListFailed(f"{path}: {e}") from None


def list_objects(cfg: dict, bucket: str):
    """Every object in the bucket as (key, unix mtime, size)."""
    token = None
    while True:
        query = {"list-type": "2", "max-keys": "1000"}
        if token:
            query["continuation-token"] = token
        root = ET.fromstring(signed_get(cfg["endpoint"], cfg["region"], cfg["key_id"],
                                        cfg["secret"], f"/{bucket}", query))
        for c in root.iter(NS + "Contents"):
            stamp = datetime.datetime.fromisoformat(c.findtext(NS + "LastModified").replace("Z", "+00:00"))
            yield c.findtext(NS + "Key"), stamp.timestamp(), int(c.findtext(NS + "Size") or 0)
        if root.findtext(NS + "IsTruncated") != "true":
            return
        token = root.findtext(NS + "NextContinuationToken")
        if not token:
            raise ListFailed(f"/{bucket}: truncated listing without a continuation token")


def prefix_of(key: str, depth: int) -> str:
    """The backup a key belongs to: its first `depth` folders; for a file at the top, the name
    before Databasus's "-<YYYYMMDD>-<HHMMSS>-<id>" (Kea-20261019-200000-...)."""
    *folders, name = key.split("/")
    if folders:
        return "/".join(folders[:depth])
    m = STAMPED.match(name)
    return m.group(1) if m else "(top)"


def summarize(objects, depth: int) -> dict:
    """Per prefix (prefix_of): newest mtime, its size, count, bytes."""
    out: dict[str, dict] = {}
    for key, mtime, size in objects:
        prefix = prefix_of(key, depth)
        p = out.setdefault(prefix, {"newest": 0.0, "newest_size": 0, "objects": 0, "bytes": 0})
        p["objects"] += 1
        p["bytes"] += size
        if mtime > p["newest"]:
            p["newest"], p["newest_size"] = mtime, size
    return out


def parse_buckets(text: str) -> list[tuple[str, int]]:
    found = []
    for item in filter(None, (s.strip() for s in text.split(","))):
        name, _, depth = item.partition(":")
        found.append((name, int(depth or 1)))
    if not found:
        sys.exit("BUCKETS is empty: name the buckets to list, as name:depth,...")
    return found


def esc(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render(results: dict, refreshed: float) -> str:
    families = {
        "garage_backup_bucket_up": ("gauge", "1 when the bucket's last listing answered"),
        "garage_backup_newest_object_timestamp_seconds": ("gauge", "Newest object's modification time under the prefix"),
        "garage_backup_newest_object_size_bytes": ("gauge", "Newest object's size under the prefix"),
        "garage_backup_objects": ("gauge", "Objects under the prefix"),
        "garage_backup_bytes": ("gauge", "Bytes under the prefix"),
    }
    lines = {name: [] for name in families}
    for bucket, r in sorted(results.items()):
        lines["garage_backup_bucket_up"].append(f'garage_backup_bucket_up{{bucket="{esc(bucket)}"}} {1 if r["ok"] else 0}')
        for prefix, p in sorted((r.get("prefixes") or {}).items()):
            lab = f'{{bucket="{esc(bucket)}",prefix="{esc(prefix)}"}}'
            lines["garage_backup_newest_object_timestamp_seconds"].append(f"garage_backup_newest_object_timestamp_seconds{lab} {p['newest']}")
            lines["garage_backup_newest_object_size_bytes"].append(f"garage_backup_newest_object_size_bytes{lab} {p['newest_size']}")
            lines["garage_backup_objects"].append(f"garage_backup_objects{lab} {p['objects']}")
            lines["garage_backup_bytes"].append(f"garage_backup_bytes{lab} {p['bytes']}")
    out = []
    for name, (kind, text) in families.items():
        out += [f"# HELP {name} {text}", f"# TYPE {name} {kind}", *lines[name]]
    out += ["# HELP garage_backup_last_refresh_timestamp_seconds When the buckets were last listed",
            "# TYPE garage_backup_last_refresh_timestamp_seconds gauge",
            f"garage_backup_last_refresh_timestamp_seconds {refreshed}"]
    return "\n".join(out) + "\n"


def refresh(cfg: dict, buckets: list[tuple[str, int]]) -> dict:
    results = {}
    for bucket, depth in buckets:
        try:
            results[bucket] = {"ok": True, "prefixes": summarize(list_objects(cfg, bucket), depth)}
        except (ListFailed, ET.ParseError, ValueError) as e:
            print(f"garage-backup-exporter: listing {bucket} failed: {e}", file=sys.stderr, flush=True)
            results[bucket] = {"ok": False}
    return results


def main() -> None:
    cfg = {"endpoint": os.environ["S3_ENDPOINT"].rstrip("/"), "region": os.environ.get("S3_REGION", "garage"),
           "key_id": os.environ["AWS_ACCESS_KEY_ID"], "secret": os.environ["AWS_SECRET_ACCESS_KEY"]}
    buckets = parse_buckets(os.environ.get("BUCKETS", ""))
    if "--once" in sys.argv[1:]:
        sys.stdout.write(render(refresh(cfg, buckets), time.time()))
        return
    every = int(os.environ.get("REFRESH_SECONDS", "600"))
    state = {"text": None}

    def loop() -> None:
        while True:
            started = time.time()
            state["text"] = render(refresh(cfg, buckets), started)
            time.sleep(max(30, every - (time.time() - started)))

    threading.Thread(target=loop, daemon=True).start()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/healthz":
                body, code = b"ok\n", 200
            elif self.path == "/metrics" and state["text"] is not None:
                body, code = state["text"].encode("utf-8"), 200
            else:
                body, code = b"not ready\n" if self.path == "/metrics" else b"not found\n", 503 if self.path == "/metrics" else 404
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args) -> None:
            pass

    http.server.ThreadingHTTPServer(("", int(os.environ.get("PORT", "8080"))), Handler).serve_forever()


if __name__ == "__main__":
    main()
