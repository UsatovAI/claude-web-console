#!/usr/bin/env bash
# Bootstraps this repo onto a fresh Ubuntu VPS as root:
#   PASSWORD='yourpassword' EMAIL='you@example.com' ./bootstrap.sh
set -euo pipefail

: "${PASSWORD:?Set PASSWORD env var to the site login password}"
DOMAIN="${DOMAIN:-$(curl -s -4 ifconfig.me | tr '.' '-').sslip.io}"
EMAIL="${EMAIL:-admin@example.com}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> domain: $DOMAIN"

apt-get update -y
apt-get install -y python3 certbot

echo "==> generating password hash"
python3 - <<PY
import json, secrets, hashlib, os
password = os.environ["PASSWORD"]
salt = secrets.token_hex(16)
h = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 200_000).hex()
json.dump({'salt': salt, 'password_hash': h}, open('$DIR/config.json', 'w'))
if not os.path.exists('$DIR/sessions.json'):
    open('$DIR/sessions.json', 'w').write('{}')
PY
chmod 600 "$DIR/config.json" "$DIR/sessions.json"

if [ ! -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]; then
  echo "==> requesting TLS certificate for $DOMAIN"
  certbot certonly --standalone -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" --no-eff-email
fi

echo "==> installing systemd service"
cat > /etc/systemd/system/claude-web.service <<EOF
[Unit]
Description=Password-gated Claude chat console
After=network.target

[Service]
Type=simple
WorkingDirectory=$DIR
Environment=CERT_DOMAIN=$DOMAIN
ExecStart=/usr/bin/python3 $DIR/app.py
Restart=on-failure
RestartSec=3
User=root

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now claude-web

echo "==> done. Listening on https://$DOMAIN/"
