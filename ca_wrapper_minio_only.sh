#!/bin/bash
# Minimal variant of ca_wrapper.sh for worker/executor: these services only
# need to trust MinIO's self-signed cert (via https://minio:9000), not the
# full DGK CA (they never talk to PMG/Trellix/etc directly).
cat /tmp/minio_ca.pem >> /app/.venv/lib/python3.12/site-packages/certifi/cacert.pem 2>/dev/null || true
# The custom registry install (/home/apiuser/.local) ships its own certifi that shadows the
# venv one, so the line above is not enough once a registry has been synced. Build one bundle
# (the certifi file Python resolves right now + MinIO's cert) and point the TLS libraries at it.
python - <<'PY' 2>/dev/null || cp /etc/ssl/certs/ca-certificates.crt /tmp/ca-bundle.pem 2>/dev/null || true
import certifi, shutil
shutil.copy(certifi.where(), "/tmp/ca-bundle.pem")
PY
cat /tmp/minio_ca.pem >> /tmp/ca-bundle.pem 2>/dev/null || true
if [ -s /tmp/ca-bundle.pem ]; then
  export AWS_CA_BUNDLE=/tmp/ca-bundle.pem SSL_CERT_FILE=/tmp/ca-bundle.pem REQUESTS_CA_BUNDLE=/tmp/ca-bundle.pem
fi
# Upstream 1.2.1 images have no /app/entrypoint.sh, so start the original
# command directly.
exec "$@"
