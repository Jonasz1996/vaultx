#!/usr/bin/env bash
# Zelfondertekend TLS-certificaat voor de lokale nginx, zodat VaultX ook rechtstreeks op de
# container via https werkt (bv. https://192.168.0.50 op het LAN, zonder NPM ervoor). De
# webkluis heeft dat nodig: de browser geeft WebCrypto enkel in een beveiligde context (https).
#
#   /opt/vaultx/deploy/debian/tls-cert.sh            # aanmaken als er nog geen is (update.sh doet dit)
#   /opt/vaultx/deploy/debian/tls-cert.sh --renew    # opnieuw aanmaken, bv. na een nieuw IP-adres
#
# Een bestaand certificaat blijft staan: zet je zelf een ander in $TLS_DIR (vaultx.crt en
# vaultx.key), dan raakt dit script het niet aan. Geldig 9999 dagen, met de hostnaam en alle
# IP-adressen van de container als SAN. Daarna schrijft het de nginx-snippet die de site op
# poort 443 laat luisteren; poort 80 (voor NPM) blijft ongewijzigd.
set -euo pipefail

TLS_DIR=${VAULTX_TLS_DIR:-/etc/vaultx/tls}
DAYS=${VAULTX_TLS_DAYS:-9999}
CRT="$TLS_DIR/vaultx.crt"
KEY="$TLS_DIR/vaultx.key"
SNIPPET="$TLS_DIR/nginx-tls.conf"

die() { printf 'FOUT: %s\n' "$*" >&2; exit 1; }

renew=false
case "${1:-}" in
  "") ;;
  --renew) renew=true ;;
  *) die "onbekende optie: $1 (enkel --renew)" ;;
esac

command -v openssl >/dev/null || die "openssl ontbreekt (apt install openssl)"
install -d -m 750 "$TLS_DIR"

if $renew || [ ! -s "$CRT" ] || [ ! -s "$KEY" ]; then
  host=$(hostname -s 2>/dev/null || hostname)
  fqdn=$(hostname -f 2>/dev/null || echo "$host")
  san="DNS:$host,DNS:localhost,IP:127.0.0.1,IP:::1"
  [ "$fqdn" != "$host" ] && san="$san,DNS:$fqdn"
  for ip in $(hostname -I 2>/dev/null || true); do
    case ",$san," in *",IP:$ip,"*) ;; *) san="$san,IP:$ip" ;; esac
  done

  tmp_key=$(mktemp "$TLS_DIR/.key.XXXXXX")
  tmp_crt=$(mktemp "$TLS_DIR/.crt.XXXXXX")
  trap 'rm -f "$tmp_key" "$tmp_crt"' EXIT
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -sha256 \
    -days "$DAYS" -subj "/CN=$host" -addext "subjectAltName=$san" \
    -addext "basicConstraints=critical,CA:FALSE" -addext "extendedKeyUsage=serverAuth" \
    -keyout "$tmp_key" -out "$tmp_crt" 2>/dev/null
  chmod 600 "$tmp_key"
  chmod 644 "$tmp_crt"
  mv -f "$tmp_key" "$KEY"
  mv -f "$tmp_crt" "$CRT"
  trap - EXIT
  echo "certificaat aangemaakt: $CRT ($DAYS dagen, $san)"
else
  echo "certificaat bestaat al: $CRT (opnieuw aanmaken: $0 --renew)"
fi
openssl x509 -in "$CRT" -noout -enddate -fingerprint -sha256

# nginx-vaultx.conf neemt deze snippet op met een glob: zonder certificaat luistert de site
# gewoon niet op 443, en nginx -t faalt niet.
cat > "$SNIPPET.new" <<CONF
# Geschreven door deploy/debian/tls-cert.sh; wordt bij elke update.sh overschreven.
listen 443 ssl;
ssl_certificate $CRT;
ssl_certificate_key $KEY;
ssl_protocols TLSv1.2 TLSv1.3;
ssl_session_cache shared:vaultx_tls:1m;
ssl_session_timeout 1d;
CONF
mv -f "$SNIPPET.new" "$SNIPPET"

if [ "${VAULTX_TLS_NO_RELOAD:-}" != "1" ] && command -v nginx >/dev/null && systemctl is-active --quiet nginx; then
  if nginx -t 2>/dev/null; then
    systemctl reload nginx
    echo "nginx herladen: VaultX luistert nu ook op https (poort 443)"
  else
    nginx -t || true
    die "nginx weigert de config met TLS; zie de fout hierboven"
  fi
fi
