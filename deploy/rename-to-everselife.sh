#!/usr/bin/env bash
# Renaming the live alpha from `octoverse` to `everselife` without losing the
# world. Run it on the server, from any directory:
#
#   bash /opt/octoverse/rename-to-everselife.sh
#
# Why a script and not a few commands by hand: the compose project name is what
# Docker derives volume names from. Bringing the renamed compose up as it is
# would create `everselife_pgdata` -- an empty database next to the old one --
# and the alpha would greet its players with a world built from scratch. The
# world is eternal, there are no wipes (D-007), so the data moves first and the
# stack starts second.
#
# Everything is reversible until the last step: the old volumes stay in place
# and are only deleted by hand, later, when the new ones have proven themselves.

set -euo pipefail

OLD_DIR=${OLD_DIR:-/opt/octoverse}
NEW_DIR=${NEW_DIR:-/opt/everselife}
OLD=${OLD_PROJECT:-octoverse}
NEW=${NEW_PROJECT:-everselife}

say() { printf '\n== %s\n' "$1"; }

[ -f "$OLD_DIR/compose.yaml" ] || { echo "no compose in $OLD_DIR -- nothing to rename"; exit 1; }
cd "$OLD_DIR"

say "0. A dump first: the net under everything below"
mkdir -p ~/backup
DUMP=~/backup/before-rename-$(date +%F-%H%M).dump
docker compose exec -T postgres pg_dump -U "$OLD" -Fc "$OLD" > "$DUMP"
[ -s "$DUMP" ] || { echo "the dump is empty -- stopping"; exit 1; }
echo "dump: $DUMP ($(du -h "$DUMP" | cut -f1))"

say "1. The database and its role take the new name"
# A database cannot be renamed while someone is connected to it, so the
# services that hold connections stop first. Postgres itself keeps running.
docker compose stop backend worker > /dev/null
docker compose exec -T postgres psql -U "$OLD" -d postgres -v ON_ERROR_STOP=1 <<SQL
ALTER DATABASE $OLD RENAME TO $NEW;
ALTER USER $OLD RENAME TO $NEW;
SQL

say "2. The old stack stops, volumes stay"
docker compose down > /dev/null

say "3. Volumes are copied under the new project's names"
# Docker cannot rename a volume, so each is copied. The old ones remain: if
# anything goes wrong, the previous stack still has everything it had.
for volume in pgdata landing_data caddy_data caddy_config; do
    if ! docker volume inspect "${OLD}_$volume" > /dev/null 2>&1; then
        echo "  ${OLD}_$volume -- none, skipping"
        continue
    fi
    docker volume create "${NEW}_$volume" > /dev/null
    docker run --rm -v "${OLD}_$volume":/from -v "${NEW}_$volume":/to alpine \
        sh -c 'cd /from && cp -a . /to' > /dev/null
    echo "  ${OLD}_$volume -> ${NEW}_$volume"
done

say "4. The directory moves and `.env` takes the new names"
sudo mv "$OLD_DIR" "$NEW_DIR"
sudo chown -R "$(id -un):$(id -gn)" "$NEW_DIR"
sed -i "s/^OCTOVERSE_/EVERSELIFE_/" "$NEW_DIR/.env"
sed -i "s/^GHCR_OWNER=.*/GHCR_OWNER=everselife/" "$NEW_DIR/.env"

say "5. The new stack comes up"
cd "$NEW_DIR"
docker compose pull
docker compose up -d
docker compose ps

say "Done. Check that the world is the old one, not a fresh one:"
echo "  curl -s https://\$DOMAIN/api/public/map | head -c 120"
echo
echo "When it has proven itself, the old volumes go:"
for volume in pgdata landing_data caddy_data caddy_config; do
    echo "  docker volume rm ${OLD}_$volume"
done
