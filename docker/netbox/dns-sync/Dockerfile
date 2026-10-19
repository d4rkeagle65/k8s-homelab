# NetBox DNS to the Pi-holes (netbox_pihole_sync.py, QUEUE-netbox-dns.md): the
# standard library only, so nothing is installed. Built on the Docker host by
# Dockhand from compose.yaml; no registry has it.
FROM python:3.14.8-alpine
COPY netbox_pihole_sync.py /app/netbox_pihole_sync.py
USER nobody
ENTRYPOINT ["python", "-u", "/app/netbox_pihole_sync.py"]
