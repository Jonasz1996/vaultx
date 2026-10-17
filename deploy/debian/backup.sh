#!/usr/bin/env bash
# Nachtelijke back-up van VaultX (database + config), gestart door vaultx-backup.timer.
# Bewaart de laatste VAULTX_BACKUP_KEEP (standaard 14) back-ups. Zie docs/backup.md.
set -euo pipefail
BACKUP_DIR=${VAULTX_BACKUP_DIR:-/var/backups/vaultx}
DB_NAME=${VAULTX_DB_NAME:-vaultx}
ENV_FILE=${VAULTX_ENV_FILE:-/etc/vaultx/vaultx.env}
KEEP=${VAULTX_BACKUP_KEEP:-14}
STAMP=$(date +%Y%m%d-%H%M%S)

install -d -m 700 "$BACKUP_DIR"
runuser -u postgres -- pg_dump -Fc "$DB_NAME" > "$BACKUP_DIR/vaultx-$STAMP.dump.part"
mv "$BACKUP_DIR/vaultx-$STAMP.dump.part" "$BACKUP_DIR/vaultx-$STAMP.dump"
install -m 600 "$ENV_FILE" "$BACKUP_DIR/vaultx-$STAMP.env"
# Oudste weg, per soort (dump en env); de namen bevatten de tijd, dus sorteren op naam volstaat.
for ext in dump env; do
  mapfile -t old < <(printf '%s\n' "$BACKUP_DIR"/vaultx-*."$ext" | sort -r | tail -n +"$((KEEP + 1))")
  [ "${#old[@]}" -eq 0 ] || rm -f -- "${old[@]}"
done
echo "back-up klaar: $BACKUP_DIR/vaultx-$STAMP.dump"
