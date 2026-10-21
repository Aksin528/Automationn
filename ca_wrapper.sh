#!/bin/bash
# Append DGK CA cert to certifi bundle on every container startup
cat /tmp/dgk_ca.pem >> /app/.venv/lib/python3.12/site-packages/certifi/cacert.pem 2>/dev/null || true
# Append the self-signed cert also used by Caddy and MinIO, so internal
# HTTPS calls to https://minio:9000 verify cleanly instead of needing
# verify=False.
cat /tmp/minio_ca.pem >> /app/.venv/lib/python3.12/site-packages/certifi/cacert.pem 2>/dev/null || true
exec /app/entrypoint.sh "$@"
