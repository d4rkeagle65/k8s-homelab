"""The one place PLUGINS and PLUGINS_CONFIG are set: the image's collectstatic
reads it at build time and NetBox at runtime. Each module comes from a
package pinned in ../plugin_requirements.txt.
"""
from os import environ

PLUGINS = [
    "netbox_dns",
    "netbox_attachments",
    "netbox_prometheus_sd",
    "netbox_floorplan",
    "netbox_data_flows",
    "netbox_kea",
    "netbox_dhcp",
    "netbox_config_diff",
    "netbox_topology_views",
    "netbox_security",
    "netbox_custom_objects",
    "netbox_routing",
    "netbox_inventory",
    "netbox_reorder_rack",
]

PLUGINS_CONFIG = {
    # The login Config Diff uses on the switches and APs. NetBox refuses to start
    # without the keys, so an unset variable is an empty string.
    "netbox_config_diff": {
        "USERNAME": environ.get("CONFIG_DIFF_USERNAME", ""),
        "PASSWORD": environ.get("CONFIG_DIFF_PASSWORD", ""),
    },
}
