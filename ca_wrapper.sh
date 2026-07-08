#!/bin/bash
# Append DGK CA cert to certifi bundle on every container startup
cat /tmp/dgk_ca.pem >> /app/.venv/lib/python3.12/site-packages/certifi/cacert.pem 2>/dev/null || true
exec /app/entrypoint.sh "$@"
