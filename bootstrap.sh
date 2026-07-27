#!/usr/bin/env bash
# Bootstraps this repo onto a fresh Ubuntu VPS as root:
#   PASSWORD='yourpassword' EMAIL='you@example.com' ./bootstrap.sh
#
# The web server itself, and the `claude` subprocess it spawns, both run as
# an unprivileged system user (daemon_user in config.yaml, default
# "claudeweb") -- not root. This script is the only part that needs root,
# to create that user, install packages, and register the systemd unit.
set -euo pipefail

: "${PASSWORD:?Set PASSWORD env var to the site login password}"
DOMAIN="${DOMAIN:-$(curl -s -4 ifconfig.me | tr '.' '-').sslip.io}"
EMAIL="${EMAIL:-admin@example.com}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DAEMON_USER="$(python3 -c "import sys; sys.path.insert(0,'$DIR'); from core import config; print(config.get('daemon_user'))")"

echo "==> domain: $DOMAIN"
echo "==> daemon user: $DAEMON_USER"

apt-get update -y
apt-get install -y python3 python3-yaml certbot acl

echo "==> creating unprivileged daemon user"
if ! id "$DAEMON_USER" &>/dev/null; then
  useradd --system --create-home --shell /bin/bash "$DAEMON_USER"
fi
# Explicitly not added to sudo/adm/docker/any privileged group. No sudo
# rights at all -- see README's "root vs non-root" note for why this
# boundary actually matters even on a single-purpose box (recoverability:
# this user cannot rewrite authorized_keys, disable sshd, flush firewall
# rules, or touch anything outside its own home + this repo's var/ dirs).

echo "==> creating var/state and var/log, owned by $DAEMON_USER"
mkdir -p "$DIR/var/state" "$DIR/var/log"
chown -R "$DAEMON_USER:$DAEMON_USER" "$DIR/var"

echo "==> creating secrets dir for daemon_env_file, owned by $DAEMON_USER only"
DAEMON_HOME="$(getent passwd "$DAEMON_USER" | cut -d: -f6)"
mkdir -p "$DAEMON_HOME/env"
chown "$DAEMON_USER:$DAEMON_USER" "$DAEMON_HOME/env"
chmod 700 "$DAEMON_HOME/env"
echo "    (drop VPS/GitHub credentials into \$DAEMON_HOME/env/.env yourself -- not written by this script)"

echo "==> generating password hash"
python3 - <<PY
import json, secrets, hashlib, os
password = os.environ["PASSWORD"]
salt = secrets.token_hex(16)
h = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 200_000).hex()
json.dump({'salt': salt, 'password_hash': h}, open('$DIR/var/state/config.json', 'w'))
if not os.path.exists('$DIR/var/state/sessions.json'):
    open('$DIR/var/state/sessions.json', 'w').write('{}')
PY
chown "$DAEMON_USER:$DAEMON_USER" "$DIR/var/state/config.json" "$DIR/var/state/sessions.json"
chmod 600 "$DIR/var/state/config.json" "$DIR/var/state/sessions.json"

if [ ! -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]; then
  echo "==> requesting TLS certificate for $DOMAIN"
  # --standalone needs port 80 for the ACME HTTP-01 challenge, which may
  # already be held by another service on this box (e.g. a separately
  # deployed nginx site) -- free it for just the issuance window rather
  # than assuming this VPS is single-purpose. Same hooks apply on renewal
  # via certbot's own systemd timer, so this isn't a one-off special case.
  certbot certonly --standalone -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" --no-eff-email \
    --pre-hook "systemctl stop nginx 2>/dev/null || true" \
    --post-hook "systemctl start nginx 2>/dev/null || true"
fi

if [ -d "/etc/letsencrypt/live/$DOMAIN" ]; then
  echo "==> granting $DAEMON_USER read access to the cert via ACL (not chown -- stays root-owned)"
  setfacl -R -m "u:$DAEMON_USER:rX" "/etc/letsencrypt/live/$DOMAIN" "/etc/letsencrypt/archive/$DOMAIN"
  setfacl -R -m "d:u:$DAEMON_USER:rX" "/etc/letsencrypt/archive/$DOMAIN"
  # Certbot recreates archive/ files on every renewal, which drops the ACL
  # above -- reapply it as a deploy-hook so renewals don't silently lock the
  # daemon user out of the new cert.
  mkdir -p /etc/letsencrypt/renewal-hooks/deploy
  cat > /etc/letsencrypt/renewal-hooks/deploy/claude-web-acl.sh <<EOF
#!/bin/sh
setfacl -R -m u:$DAEMON_USER:rX "/etc/letsencrypt/live/$DOMAIN" "/etc/letsencrypt/archive/$DOMAIN"
EOF
  chmod +x /etc/letsencrypt/renewal-hooks/deploy/claude-web-acl.sh
fi

echo "==> installing systemd service (runs as $DAEMON_USER, not root)"
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

User=$DAEMON_USER
Group=$DAEMON_USER

# Only extra privilege granted: bind to port 443 as a non-root user.
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=true

ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$DIR/var /home/$DAEMON_USER
PrivateTmp=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable claude-web
# restart, not start -- this script is re-run on redeploys, and `start` on
# an already-running unit is a no-op that leaves the old process (old code,
# old env vars) running instead of picking up what this run just changed.
systemctl restart claude-web

echo "==> done. Listening on https://$DOMAIN/"
echo "==> NOTE: for tool use (--dangerously-skip-permissions) the daemon"
echo "    user needs its own Claude Code login. Run once, interactively:"
echo "      su - $DAEMON_USER -c 'claude setup-token'"
