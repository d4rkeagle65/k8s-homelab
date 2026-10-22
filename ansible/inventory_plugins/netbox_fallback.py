"""NetBox inventory that survives NetBox being down (ansible/inventory/netbox.yml).

netbox.netbox.nb_inventory as it is, plus a last-good copy: every answer NetBox gives is
kept, per request URL, in a JSON file that outlives the run (NETBOX_INVENTORY_FALLBACK,
default /var/lib/semaphore/netbox-inventory-last-good.json, Semaphore's data volume). When
NetBox can't answer - no connection, a timeout, or a 5xx such as Traefik's 502 while NetBox
rebuilds - a request is answered from that copy, with a warning naming its age. Anything
else NetBox says (401 for a bad token, 403, a 404) fails or degrades exactly as the
collection's plugin does, so a misconfiguration never hides behind a stale copy. A request
the copy doesn't hold fails as before. The plugin's five-minute cache still answers first.

The collection's plugin wraps every NetBox error in AnsibleError, losing the status, so the
outage is told apart where the request is made: this module wraps the open_url the
collection's module calls.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error

from ansible.utils.display import Display

import ansible_collections.netbox.netbox.plugins.inventory.nb_inventory as nb

DOCUMENTATION = nb.DOCUMENTATION.replace("name: nb_inventory", "name: netbox_fallback", 1).replace(
    "choices: ['netbox.netbox.nb_inventory']", "choices: ['netbox_fallback']", 1)

display = Display()

DEFAULT_PATH = "/var/lib/semaphore/netbox-inventory-last-good.json"


class NetBoxUnreachable(Exception):
    """NetBox didn't answer: the connection failed, timed out, or got a 5xx."""


_collection_open_url = nb.open_url


def _open_url(*args, **kwargs):
    try:
        return _collection_open_url(*args, **kwargs)
    except urllib.error.HTTPError as e:
        if e.code >= 500:
            raise NetBoxUnreachable(f"HTTP {e.code}") from e
        raise
    except (urllib.error.URLError, OSError) as e:  # refused, DNS, TLS, timeout
        raise NetBoxUnreachable(str(getattr(e, "reason", e))) from e


nb.open_url = _open_url


class InventoryModule(nb.InventoryModule):
    NAME = "netbox_fallback"

    def parse(self, inventory, loader, path, cache=True):
        self._fallback_path = os.environ.get("NETBOX_INVENTORY_FALLBACK", DEFAULT_PATH)
        self._fallback_lock = threading.Lock()
        self._fallback_used = []
        try:
            with open(self._fallback_path, encoding="utf-8") as f:
                saved = json.load(f)
            self._fallback = saved.get("answers", {})
            self._fallback_saved_at = saved.get("saved_at", 0)
        except (OSError, ValueError):
            self._fallback, self._fallback_saved_at = {}, 0
        super().parse(inventory, loader, path, cache=cache)
        if self._fallback_used:
            age = time.time() - self._fallback_saved_at
            display.warning(
                f"NetBox didn't answer ({self._fallback_used[0]}): {len(self._fallback_used)} request(s) "
                f"answered from the last-good copy, {age / 3600:.1f} h old ({self._fallback_path}). "
                "Hosts or variables changed in NetBox since then aren't seen.")
        else:
            self._save_fallback()

    def _fetch_information(self, url):
        try:
            results = super()._fetch_information(url)
        except NetBoxUnreachable as e:
            with self._fallback_lock:
                if url not in self._fallback:
                    raise nb.AnsibleError(
                        f"NetBox didn't answer ({e}) and the last-good copy ({self._fallback_path}) "
                        f"has no answer for {url}") from e
                self._fallback_used.append(str(e))
                return self._fallback[url]
        with self._fallback_lock:
            self._fallback[url] = results
        return results

    def _save_fallback(self):
        """Write the copy atomically; a failure to write warns but doesn't fail the run."""
        tmp = f"{self._fallback_path}.tmp"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"saved_at": time.time(), "answers": self._fallback}, f)
            os.replace(tmp, self._fallback_path)
        except OSError as e:
            display.warning(f"Couldn't keep the last-good NetBox copy at {self._fallback_path}: {e}")
