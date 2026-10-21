#!/bin/bash
# Minimal variant of ca_wrapper.sh for worker/executor: these services only
# need to trust MinIO's self-signed cert (via https://minio:9000), not the
# full DGK CA (they never talk to PMG/Trellix/etc directly).
cat /tmp/minio_ca.pem >> /app/.venv/lib/python3.12/site-packages/certifi/cacert.pem 2>/dev/null || true
exec /app/entrypoint.sh "$@"
