# kea-ha-exporter's image: the script baked in, so a change to it changes the
# image, and the deploy's build recreates the container. A bind-mounted script
# would keep running its old copy: compose recreates only on a changed service,
# and a single-file mount keeps the file git replaced.
FROM python:3.14.8-alpine
COPY ha_exporter.py /app/ha_exporter.py
CMD ["python", "-I", "/app/ha_exporter.py"]
