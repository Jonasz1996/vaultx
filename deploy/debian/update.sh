#!/usr/bin/env bash
# VaultX bijwerken op een Debian-container (zie docs/debian-ct.md, "Updaten").
#
#   sudo /opt/vaultx/deploy/debian/update.sh                 # huidige branch bijwerken
#   sudo /opt/vaultx/deploy/debian/update.sh phase-7/notes-ts9df7   # naar een andere branch
#
# Stappen: back-up van database en config, code ophalen, Python- en frontend-build,
# systemd-unit en nginx-config vernieuwen (oude versie blijft als .bak), herstarten
# (de service migreert zelf) en controleren of /health/ready antwoordt.
set -euo pipefail

# Alles zit in main(): bash leest het script zo volledig in voor "git checkout" het vervangt.
main() {

  APP_DIR=${VAULTX_APP_DIR:-/opt/vaultx}
  ENV_FILE=${VAULTX_ENV_FILE:-/etc/vaultx/vaultx.env}
  BACKUP_DIR=${VAULTX_BACKUP_DIR:-/var/backups/vaultx}
  DB_NAME=${VAULTX_DB_NAME:-vaultx}
  NGINX_SITE=${VAULTX_NGINX_SITE:-/etc/nginx/sites-available/vaultx}
  UNIT=/etc/systemd/system/vaultx.service
  REF=${1:-}
  STAMP=$(date +%Y%m%d-%H%M%S)

  log() { printf '\n==> %s\n' "$*"; }
  die() { printf 'FOUT: %s\n' "$*" >&2; exit 1; }

  [ "$(id -u)" -eq 0 ] || die "start als root (sudo)"
  cd "$APP_DIR"
  [ -z "$(git status --porcelain --untracked-files=no)" ] || die "lokale wijzigingen in $APP_DIR; eerst opruimen (git status)"

  node_major=$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)
  if [ "$node_major" -lt 22 ]; then
    echo "LET OP: Node $(node -v 2>/dev/null || echo '?') gevonden; de frontend vraagt Node 22 of nieuwer (zie docs/debian-ct.md)."
  fi

  log "Back-up naar $BACKUP_DIR"
  install -d -m 700 "$BACKUP_DIR"
  runuser -u postgres -- pg_dump -Fc "$DB_NAME" > "$BACKUP_DIR/vaultx-$STAMP.dump"
  install -m 600 "$ENV_FILE" "$BACKUP_DIR/vaultx-$STAMP.env"
  echo "database: $BACKUP_DIR/vaultx-$STAMP.dump"
  echo "code was: $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)"

  log "Code ophalen"
  git fetch --prune origin
  if [ -n "$REF" ]; then
    git checkout -B "$REF" "origin/$REF"
  else
    git pull --ff-only
  fi
  echo "code is nu: $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)"

  log "Python-afhankelijkheden"
  "$APP_DIR/venv/bin/pip" install --quiet -r backend/requirements.txt

  log "Frontend bouwen"
  (cd frontend && npm ci --no-audit --no-fund && npm run build)

  log "systemd-unit en nginx-config"
  replace() { # replace <nieuw> <doel>: kopieert enkel als het verschilt, bewaart het oude als .bak-<tijd>
    if ! cmp -s "$1" "$2"; then
      [ -e "$2" ] && cp -a "$2" "$2.bak-$STAMP" && echo "oud bewaard: $2.bak-$STAMP"
      install -m 644 "$1" "$2"
      echo "vernieuwd: $2"
    fi
  }
  replace deploy/debian/vaultx.service "$UNIT"
  systemctl daemon-reload
  replace deploy/debian/nginx-vaultx.conf "$NGINX_SITE"
  if ! nginx -t; then
    [ -e "$NGINX_SITE.bak-$STAMP" ] && cp -a "$NGINX_SITE.bak-$STAMP" "$NGINX_SITE"
    die "nieuwe nginx-config geweigerd; de vorige staat terug. Pas $NGINX_SITE aan en start opnieuw."
  fi
  systemctl reload nginx

  log "Herstarten (migraties lopen bij het starten)"
  systemctl restart vaultx
  for _ in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:8000/health/ready >/dev/null 2>&1; then
      log "Klaar: VaultX draait op $(git rev-parse --short HEAD)"
      exit 0
    fi
    sleep 2
  done
  journalctl -u vaultx -n 60 --no-pager || true
  cat >&2 <<MSG

  VaultX komt niet op. Terugzetten (zie docs/backup.md):
    systemctl stop vaultx
    cd $APP_DIR && git checkout <vorige branch of commit hierboven>
    runuser -u postgres -- dropdb $DB_NAME
    runuser -u postgres -- createdb -O vaultx -E UTF8 -T template0 $DB_NAME
    runuser -u postgres -- pg_restore -d $DB_NAME $BACKUP_DIR/vaultx-$STAMP.dump
    systemctl start vaultx
MSG
  exit 1
}

main "$@"
