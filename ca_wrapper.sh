#!/bin/bash
# Append DGK CA cert to certifi bundle on every container startup
cat /tmp/dgk_ca.pem >> /app/.venv/lib/python3.12/site-packages/certifi/cacert.pem 2>/dev/null || true
# Append the self-signed cert also used by Caddy and MinIO, so internal
# HTTPS calls to https://minio:9000 verify cleanly instead of needing
# verify=False.
cat /tmp/minio_ca.pem >> /app/.venv/lib/python3.12/site-packages/certifi/cacert.pem 2>/dev/null || true
# The custom registry install (/home/apiuser/.local) ships its own certifi that shadows the
# venv one, so the lines above are not enough once a registry has been synced. Build one
# bundle (the certifi file Python resolves right now + both certs) and point the TLS
# libraries at it.
python - <<'PY' 2>/dev/null || cp /etc/ssl/certs/ca-certificates.crt /tmp/ca-bundle.pem 2>/dev/null || true
import certifi, shutil
shutil.copy(certifi.where(), "/tmp/ca-bundle.pem")
PY
cat /tmp/dgk_ca.pem /tmp/minio_ca.pem >> /tmp/ca-bundle.pem 2>/dev/null || true
if [ -s /tmp/ca-bundle.pem ]; then
  export AWS_CA_BUNDLE=/tmp/ca-bundle.pem SSL_CERT_FILE=/tmp/ca-bundle.pem REQUESTS_CA_BUNDLE=/tmp/ca-bundle.pem
fi
# Upstream 1.2.1 images have no /app/entrypoint.sh (migrations run in the
# dedicated `migrations` service), so start the original command directly.
exec "$@"
