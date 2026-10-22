# NetBox with this stack's plugins (plugin_requirements.txt) and settings
# (config/), built on the Docker host by Dockhand from compose.yaml; no
# registry has it. The base image reads every .py in /etc/netbox/config, so
# plugins.py and sso.py are added beside its own configuration.py, and
# logging.py replaces its commented-out one.
FROM ghcr.io/netbox-community/netbox:v4.7.2
COPY plugin_requirements.txt /opt/netbox/
RUN VIRTUAL_ENV=/opt/netbox/venv /usr/local/bin/uv pip install --no-cache -r /opt/netbox/plugin_requirements.txt
# Topology Views' role-icon serializer breaks the change-log API; the script says why, and
# fails the build when a plugin release changes it (then re-check, or remove it if fixed).
COPY patch_topology_views.py /tmp/
RUN /opt/netbox/venv/bin/python -I /tmp/patch_topology_views.py && rm /tmp/patch_topology_views.py
COPY config/plugins.py config/sso.py config/logging.py /etc/netbox/config/
COPY custom_pipeline.py /opt/netbox/netbox/netbox/custom_pipeline.py
# Topology Views writes its exported images into the static files at runtime.
RUN mkdir -p /opt/netbox/netbox/static/netbox_topology_views/img && \
    chown -R netbox:root /opt/netbox/netbox/static/netbox_topology_views
# The plugins' static files, with a throwaway key: settings need one to load,
# and nothing here is signed with it.
RUN SECRET_KEY="build-only-$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')" \
    /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py collectstatic --no-input
