# Back-up en terugzetten

Alles wat VaultX bewaart, staat op twee plaatsen:

- **De PostgreSQL-database**: gebruikers, organisaties, catalogus, auditlog,
  de versleutelde kluis, notities en hun bijlagen. Bijlagen kunnen de database
  groot maken (standaard tot 1 GB per gebruiker).
- **`VAULTX_SECRET_KEY`** (in `/etc/vaultx/vaultx.env` of `docker/.env`).
  Daarmee zijn de NPM-wachtwoorden, de client secrets van automatische login en
  de sleutel van de kluistokens versleuteld. Een database zonder die sleutel kan
  je terugzetten, maar dan moet je elke NPM-koppeling en automatische login
  opnieuw invullen en loggen alle Bitwarden-apps opnieuw in.

Kluis en notities zijn end-to-end versleuteld met het master password van elke
gebruiker. Een back-up bevat dus nooit klare tekst, en zonder dat master
password kan niemand ze openen, ook jij niet.

Bewaar back-ups en de env-file op een andere machine dan de container
(bv. Proxmox Backup Server of een NAS) en test af en toe of terugzetten lukt.

## Debian-container

### Elke nacht automatisch

```bash
cp /opt/vaultx/deploy/debian/vaultx-backup.service /opt/vaultx/deploy/debian/vaultx-backup.timer /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now vaultx-backup.timer
systemctl start vaultx-backup.service     # meteen een eerste back-up
ls -l /var/backups/vaultx
```

`deploy/debian/backup.sh` schrijft `vaultx-<tijd>.dump` (`pg_dump -Fc`) en
`vaultx-<tijd>.env` naar `/var/backups/vaultx` (enkel leesbaar voor root) en
houdt de laatste 14. Andere map of aantal: zet `VAULTX_BACKUP_DIR` of
`VAULTX_BACKUP_KEEP` met `systemctl edit vaultx-backup.service`
(`[Service]` + `Environment=VAULTX_BACKUP_KEEP=30`).

`deploy/debian/update.sh` maakt voor elke update ook een back-up in dezelfde map.

### Terugzetten

```bash
systemctl stop vaultx
runuser -u postgres -- dropdb vaultx
runuser -u postgres -- createdb -O vaultx -E UTF8 -T template0 vaultx
runuser -u postgres -- pg_restore -d vaultx --no-owner --role=vaultx /var/backups/vaultx/vaultx-<tijd>.dump
install -m 640 -g vaultx /var/backups/vaultx/vaultx-<tijd>.env /etc/vaultx/vaultx.env   # als de config weg is
systemctl start vaultx && curl -s http://127.0.0.1:8000/health/ready
```

Zet je een back-up van een oudere versie terug op nieuwere code, dan migreert
VaultX bij het starten verder. Een back-up van nieuwere code op oudere code
terugzetten werkt niet: zet dan eerst de code terug (`git checkout` van de
commit die `update.sh` toonde).

## Docker Compose

```bash
docker compose -f docker/docker-compose.yml exec -T db pg_dump -U vaultx -Fc vaultx > vaultx-$(date +%F).dump
cp docker/.env vaultx-$(date +%F).env
```

Terugzetten:

```bash
docker compose -f docker/docker-compose.yml stop backend
docker compose -f docker/docker-compose.yml exec -T db dropdb -U vaultx vaultx
docker compose -f docker/docker-compose.yml exec -T db createdb -U vaultx -E UTF8 -T template0 vaultx
docker compose -f docker/docker-compose.yml exec -T db pg_restore -U vaultx -d vaultx < vaultx-<datum>.dump
docker compose -f docker/docker-compose.yml start backend
```
