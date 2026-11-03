# Views: Key Vault values with structure, built from NetBox

Each `NAME.py` here builds one Key Vault value for `scripts/keyvault_facts.py`, which a line
`SECRET-NAME: {view: NAME}` in `scripts/keyvault_facts.yaml` maps. It defines `build(site)` and
returns a string, or data that is written as JSON. `site` (the `Site` class there) reads NetBox:

| Call | Gives |
|---|---|
| `site.usable(kind)` | the IP addresses (`"ip"`), ranges (`"range"`) or prefixes (`"prefix"`) the status table lets a job use |
| `site.claiming(name)` | the usable objects whose `keyvault_secrets` field names `name` |
| `site.form(kind, obj)` | an object's `address`, `cidr`, `dns_name` or `range` |
| `site.all(path)` | every object at any API path (`ipam/vlans`, `dcim/devices`, `plugins/netbox-dns/records`); checking its status is the view's job, by README.md's table |
| `site.secret(name)` | another Key Vault secret, for what NetBox doesn't hold (a key, a domain) |

A view selects by what NetBox records (a VLAN's name, a role, a tag, a `keyvault_secrets`
claim), never by a host name or address, which stay out of git. A value is never printed:
when a view's output differs from Key Vault's, the run names the JSON paths. Its test goes in
`scripts/tests/test_keyvault_facts.py` (or a file of its own) against a fake NetBox.
