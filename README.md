# mtg-data

Daily-refreshed SQLite database of Magic: the Gathering cards and rulings, built from [Scryfall](https://scryfall.com/docs/api/bulk-data) bulk data and published as a GitHub Release asset.

## Download

Stable URLs (always point at the newest successful build):

```
https://github.com/nwgarne/mtg-data/releases/download/latest/mtg.db.gz
https://github.com/nwgarne/mtg-data/releases/download/latest/manifest.json
```

Gzipped SQLite is typically 25 to 60 MB. The manifest is a small JSON file with the SHA-256 of the gz, the card and ruling counts, the Scryfall version stamps for both bulk files, and the build timestamp.

## Refresh schedule

A GitHub Actions workflow runs daily at **04:00 UTC**, shortly after Scryfall's nightly bulk regen, and clobber-updates the `latest` release. Failures are visible on the [Actions tab](https://github.com/nwgarne/mtg-data/actions/workflows/refresh.yml); the previous build asset stays in place if a refresh fails.

## Schema

See [`schema.sql`](./schema.sql). The schema mirrors the [`mtg-api`](https://mtg.garnersites.cloud) service so an artifact from this repo can drop into that service's data directory as `mtg.db` and be served unchanged.

Tables:

- `cards` (32k+ rows, one per `oracle_id`): `name`, `name_lower`, `mana_cost`, `cmc`, `type_line`, `oracle_text`, `power`, `toughness`, `loyalty`, `defense`, `colors`, `color_identity`, `layout`, `card_faces`, `keywords`, `legalities`, `scryfall_uri`, `image_normal`, `released_at`. JSON-shaped columns (`colors`, `color_identity`, `keywords`, `legalities`, `card_faces`) hold JSON-stringified values.
- `rulings` (75k+ rows): keyed by `oracle_id`, with `published_at`, `source`, `comment`.
- `meta` (key/value): `last_refresh`, `last_refresh_status`, `card_count`, `ruling_count`, `scryfall_version`, `scryfall_oracle_updated_at`, `scryfall_rulings_updated_at`, `source`, `schema_version`.

Two consumers share this schema:

1. The live HTTP API at https://mtg.garnersites.cloud, which is the canonical real-time service for the [decks.dirtyshoulders.com](https://decks.dirtyshoulders.com) SaaS and other clients that want up-to-the-hour data.
2. This repo's release asset, which is convenient for short-lived consumers (Claude lookups, notebooks, CI tasks) that prefer a one-time snapshot download over a network round-trip per query.

## Quick start (Python)

```python
import urllib.request, gzip, sqlite3, shutil

urllib.request.urlretrieve(
    "https://github.com/nwgarne/mtg-data/releases/download/latest/mtg.db.gz",
    "/tmp/mtg.db.gz",
)
with gzip.open("/tmp/mtg.db.gz", "rb") as fin, open("/tmp/mtg.db", "wb") as fout:
    shutil.copyfileobj(fin, fout)

con = sqlite3.connect("/tmp/mtg.db")
print(con.execute(
    "SELECT name, oracle_text FROM cards WHERE name_lower = ?",
    ("lightning bolt",),
).fetchone())
```

## Build locally

The build script uses only the Python standard library:

```
python3 scripts/build-db.py
```

Outputs land in `/tmp/`: `mtg.db`, `mtg.db.gz`, `manifest.json`.

## Credit

Card data and rulings are provided by [Scryfall](https://scryfall.com/) under their [API terms](https://scryfall.com/docs/api). Magic: the Gathering and all card names, art, and rules are property of Wizards of the Coast. This repo is a derivative pipeline; Scryfall is the canonical upstream source.

The pipeline code in this repo is MIT-licensed; see [`LICENSE`](./LICENSE).
