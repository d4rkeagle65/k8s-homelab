#!/bin/sh
# Runs once, on the first start with an empty data directory: a database and
# owner each for Kea's host reservations and for Stork, with passwords from
# the stack's environment. Use passwords without quotes (e.g. openssl rand
# -hex 24). The kea-db-init service then creates Kea's tables.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<EOSQL
CREATE ROLE kea LOGIN PASSWORD '${KEA_DB_PASSWORD}';
CREATE DATABASE kea OWNER kea;
CREATE ROLE stork LOGIN PASSWORD '${STORK_DB_PASSWORD}';
CREATE DATABASE stork OWNER stork;
EOSQL
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname stork \
    -c 'CREATE EXTENSION IF NOT EXISTS pgcrypto;'
