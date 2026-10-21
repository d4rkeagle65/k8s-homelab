"""The one place PLUGINS and PLUGINS_CONFIG are set: the image's collectstatic
reads it at build time and NetBox at runtime. Each module comes from a
package pinned in ../plugin_requirements.txt.
"""
PLUGINS = [
    "netbox_dns",
    "netbox_attachments",
    "netbox_prometheus_sd",
    "netbox_floorplan",
    "netbox_data_flows",
    "netbox_kea",
    "netbox_dhcp",
    "netbox_topology_views",
    "netbox_security",
    "netbox_custom_objects",
    "netbox_routing",
    "netbox_inventory",
    "netbox_reorder_rack",
]

PLUGINS_CONFIG = {}
