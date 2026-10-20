#!/usr/bin/env bash
# VaultX bijwerken op een Debian-container (zie docs/debian-ct.md, "Updaten").
#
#   sudo /opt/vaultx/deploy/debian/update.sh                 # huidige branch bijwerken
#   sudo /opt/vaultx/deploy/debian/update.sh phase-7/notes-ts9df7   # naar een andere branch
#
# Stappen: back-up van database en config, code ophalen, Python- en frontend-build,
# systemd-unit en nginx-config vernieuwen (oude versie blijft als .bak), eenmalig een
# zelfondertekend certificaat voor https op de container zelf (tls-cert.sh), herstarten
# (de service migreert zelf) en controleren of /health/ready antwoordt.
#
# Faalt er iets vóór het herstarten (pip, frontend-build, nginx -t), dan zet het script de
# vorige code en Python-pakketten terug. De draaiende VaultX en de vorige UI blijven werken.
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

  OLD_BRANCH=$(git symbolic-ref -q --short HEAD || true)
  OLD_SHA=$(git rev-parse HEAD)
  if [ -z "$REF" ] && [ -z "$OLD_BRANCH" ]; then
    die "$APP_DIR staat niet op een branch (losse commit); geef de branch mee, bv.: $0 main"
  fi

  log "Back-up naar $BACKUP_DIR"
  install -d -m 700 "$BACKUP_DIR"
  # Eerst als .part: een afgebroken dump telt anders als echte back-up.
  runuser -u postgres -- pg_dump -Fc "$DB_NAME" > "$BACKUP_DIR/vaultx-$STAMP.dump.part"
  mv "$BACKUP_DIR/vaultx-$STAMP.dump.part" "$BACKUP_DIR/vaultx-$STAMP.dump"
  install -m 600 "$ENV_FILE" "$BACKUP_DIR/vaultx-$STAMP.env"
  echo "database: $BACKUP_DIR/vaultx-$STAMP.dump"
  echo "code was: $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)"

  # Vanaf hier: bij een fout de vorige code en pakketten terug. VaultX is nog niet herstart.
  # shellcheck disable=SC2317,SC2329  # via trap ERR
  rollback() {
    trap - ERR
    printf '\nFOUT bij het updaten: de vorige versie (%s) wordt teruggezet.\n' "$(git rev-parse --short "$OLD_SHA")" >&2
    if [ -n "$OLD_BRANCH" ]; then git checkout -q -B "$OLD_BRANCH" "$OLD_SHA"; else git checkout -q "$OLD_SHA"; fi
    "$APP_DIR/venv/bin/pip" install --quiet -r backend/requirements.txt || true
    rm -rf frontend/dist.new
    printf 'Teruggezet. VaultX draait verder op de vorige versie; zie de fout hierboven.\n' >&2
    exit 1
  }
  trap rollback ERR

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
  # In een aparte map en daarna omwisselen: nginx serveert de oude UI tot de nieuwe klaar is.
  rm -rf frontend/dist.new
  (cd frontend && npm ci --no-audit --no-fund && npm run build -- --outDir dist.new --emptyOutDir)
  rm -rf frontend/dist.old
  [ -d frontend/dist ] && mv frontend/dist frontend/dist.old
  mv frontend/dist.new frontend/dist
  rm -rf frontend/dist.old

  log "systemd-unit en nginx-config"
  replace() { # replace <nieuw> <doel>: kopieert enkel als het verschilt, bewaart het oude als .bak-<tijd>
    if ! cmp -s "$1" "$2"; then
      [ -e "$2" ] && cp -a "$2" "$2.bak-$STAMP" && echo "oud bewaard: $2.bak-$STAMP"
      install -m 644 "$1" "$2"
      echo "vernieuwd: $2"
    fi
  }
  replace deploy/debian/vaultx.service "$UNIT"
  replace deploy/debian/vaultx-backup.service /etc/systemd/system/vaultx-backup.service
  replace deploy/debian/vaultx-backup.timer /etc/systemd/system/vaultx-backup.timer
  systemctl daemon-reload
  systemctl enable --quiet --now vaultx-backup.timer
  # Welke proxy het echte client-IP mag doorgeven. Staat in een eigen bestand, zodat jouw
  # aanpassing (enkel het IP van NPM) een update overleeft. Enkel aangemaakt als het ontbreekt.
  if [ ! -e /etc/vaultx/nginx-realip.conf ]; then
    install -m 644 deploy/debian/nginx-realip.conf /etc/vaultx/nginx-realip.conf
    echo "aangemaakt: /etc/vaultx/nginx-realip.conf (zet hier het IP van je NPM-host)"
  fi
  tls_snippet=/etc/vaultx/tls/nginx-tls.conf
  [ -e "$tls_snippet" ] && cp -a "$tls_snippet" "$tls_snippet.bak-$STAMP"
  VAULTX_TLS_NO_RELOAD=1 bash deploy/debian/tls-cert.sh
  replace deploy/debian/nginx-vaultx.conf "$NGINX_SITE"
  if ! nginx -t; then
    [ -e "$NGINX_SITE.bak-$STAMP" ] && cp -a "$NGINX_SITE.bak-$STAMP" "$NGINX_SITE"
    [ -e "$tls_snippet.bak-$STAMP" ] && cp -a "$tls_snippet.bak-$STAMP" "$tls_snippet"
    echo "nieuwe nginx-config geweigerd; de vorige staat terug" >&2
    false
  fi
  rm -f "$tls_snippet.bak-$STAMP"
  systemctl reload nginx
  trap - ERR

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
