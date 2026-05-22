#!/usr/bin/env python3
"""Build mtg.db from Scryfall bulk data and write mtg.db.gz + manifest.json.

The output schema mirrors docker01:~/stacks/mtg-api/src/db.js byte-for-byte,
and the field mapping mirrors docker01:~/stacks/mtg-api/src/refresh.js, so
either consumer can serve the same artifact.

Standard library only. Designed for GitHub Actions ubuntu-latest (7 GB RAM,
14 GB SSD) under a 20-minute timeout.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "schema.sql"

TMP = Path("/tmp")
DB_NEW = TMP / "mtg.db.new"
DB = TMP / "mtg.db"
DB_GZ = TMP / "mtg.db.gz"
MANIFEST = TMP / "manifest.json"
ORACLE_JSON = TMP / "oracle-cards.json"
RULINGS_JSON = TMP / "rulings.json"

SCRYFALL_BULK_INDEX = "https://api.scryfall.com/bulk-data"
USER_AGENT = "nwgarne-mtg-data/1.0 (https://github.com/nwgarne/mtg-data)"
ACCEPT = "application/json"
SCRYFALL_DELAY = 0.1
SCHEMA_VERSION = 1


def http_get_json(url: str) -> dict:
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": ACCEPT}
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


def http_download(url: str, dest: Path) -> int:
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": ACCEPT}
    )
    if dest.exists():
        dest.unlink()
    with urllib.request.urlopen(req, timeout=600) as resp, open(dest, "wb") as out:
        shutil.copyfileobj(resp, out, length=1 << 20)
    return dest.stat().st_size


def map_card(c: dict) -> dict | None:
    """Mirror of docker01 refresh.js#mapCard."""
    oracle_id = c.get("oracle_id")
    name = c.get("name")
    if not oracle_id or not name:
        return None

    faces_raw = c.get("card_faces")
    faces = faces_raw if isinstance(faces_raw, list) else None

    oracle_text = c.get("oracle_text")
    if not oracle_text and faces and any(f.get("oracle_text") for f in faces):
        oracle_text = "\n//\n".join((f.get("oracle_text") or "") for f in faces)
    if not oracle_text:
        oracle_text = None

    mana_cost = c.get("mana_cost")
    if mana_cost is None and faces:
        mana_cost = faces[0].get("mana_cost")

    if isinstance(c.get("colors"), list):
        colors = list(c["colors"])
    elif faces:
        seen_order: list[str] = []
        seen_set: set[str] = set()
        for f in faces:
            for x in (f.get("colors") or []):
                if x not in seen_set:
                    seen_set.add(x)
                    seen_order.append(x)
        colors = seen_order
    else:
        colors = []

    image_normal = None
    if isinstance(c.get("image_uris"), dict):
        image_normal = c["image_uris"].get("normal")
    if image_normal is None and faces:
        f0 = faces[0] if faces else None
        if isinstance(f0, dict) and isinstance(f0.get("image_uris"), dict):
            image_normal = f0["image_uris"].get("normal")

    cmc_val = c.get("cmc")
    cmc = cmc_val if isinstance(cmc_val, (int, float)) and not isinstance(cmc_val, bool) else None

    color_identity_raw = c.get("color_identity")
    color_identity = color_identity_raw if isinstance(color_identity_raw, list) else []

    keywords_raw = c.get("keywords")
    keywords = keywords_raw if isinstance(keywords_raw, list) else []

    legalities = c.get("legalities") if isinstance(c.get("legalities"), dict) else None

    return {
        "oracle_id": oracle_id,
        "name": name,
        "name_lower": name.lower(),
        "mana_cost": mana_cost,
        "cmc": cmc,
        "type_line": c.get("type_line"),
        "oracle_text": oracle_text,
        "power": c.get("power"),
        "toughness": c.get("toughness"),
        "loyalty": c.get("loyalty"),
        "defense": c.get("defense"),
        "colors": json.dumps(colors),
        "color_identity": json.dumps(color_identity),
        "layout": c.get("layout"),
        "card_faces": json.dumps(faces) if faces else None,
        "keywords": json.dumps(keywords),
        "legalities": json.dumps(legalities) if legalities else None,
        "scryfall_uri": c.get("scryfall_uri"),
        "image_normal": image_normal,
        "released_at": c.get("released_at"),
    }


def set_meta(con: sqlite3.Connection, key: str, value) -> None:
    con.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def main() -> int:
    started = time.time()
    print(f"[build] cwd={Path.cwd()} schema={SCHEMA_PATH}", flush=True)

    if not SCHEMA_PATH.exists():
        print(f"[build] FATAL: schema not found at {SCHEMA_PATH}", file=sys.stderr)
        return 1

    print("[build] fetching Scryfall bulk-data manifest", flush=True)
    bulk = http_get_json(SCRYFALL_BULK_INDEX)
    oracle_entry = next((e for e in bulk["data"] if e.get("type") == "oracle_cards"), None)
    rulings_entry = next((e for e in bulk["data"] if e.get("type") == "rulings"), None)
    if not oracle_entry or not rulings_entry:
        print("[build] FATAL: manifest missing oracle_cards or rulings", file=sys.stderr)
        return 1
    print(
        f"[build] oracle_cards updated_at={oracle_entry['updated_at']} "
        f"size={oracle_entry.get('size'):,}",
        flush=True,
    )
    print(
        f"[build] rulings updated_at={rulings_entry['updated_at']} "
        f"size={rulings_entry.get('size'):,}",
        flush=True,
    )

    time.sleep(SCRYFALL_DELAY)
    print(f"[build] downloading oracle_cards to {ORACLE_JSON}", flush=True)
    oracle_bytes = http_download(oracle_entry["download_uri"], ORACLE_JSON)
    print(f"[build] downloaded {oracle_bytes:,} bytes ({oracle_bytes/1048576:.1f} MB)", flush=True)

    time.sleep(SCRYFALL_DELAY)
    print(f"[build] downloading rulings to {RULINGS_JSON}", flush=True)
    rulings_bytes = http_download(rulings_entry["download_uri"], RULINGS_JSON)
    print(f"[build] downloaded {rulings_bytes:,} bytes ({rulings_bytes/1048576:.1f} MB)", flush=True)

    for p in (DB_NEW, Path(f"{DB_NEW}-journal"), Path(f"{DB_NEW}-wal"), Path(f"{DB_NEW}-shm")):
        if p.exists():
            p.unlink()
    print(f"[build] creating {DB_NEW}", flush=True)
    con = sqlite3.connect(str(DB_NEW))
    con.execute("PRAGMA journal_mode = DELETE")
    con.execute("PRAGMA synchronous = NORMAL")
    con.execute("PRAGMA temp_store = MEMORY")
    con.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

    print(f"[build] parsing {ORACLE_JSON}", flush=True)
    with open(ORACLE_JSON, "rb") as f:
        cards = json.load(f)
    print(f"[build] parsed {len(cards):,} card entries", flush=True)

    insert_card_sql = """
        INSERT INTO cards (
          oracle_id, name, name_lower, mana_cost, cmc, type_line, oracle_text,
          power, toughness, loyalty, defense, colors, color_identity, layout,
          card_faces, keywords, legalities, scryfall_uri, image_normal, released_at
        ) VALUES (
          :oracle_id, :name, :name_lower, :mana_cost, :cmc, :type_line, :oracle_text,
          :power, :toughness, :loyalty, :defense, :colors, :color_identity, :layout,
          :card_faces, :keywords, :legalities, :scryfall_uri, :image_normal, :released_at
        )
        ON CONFLICT(oracle_id) DO NOTHING
    """
    seen: set[str] = set()
    rows: list[dict] = []
    for c in cards:
        row = map_card(c)
        if row is None:
            continue
        oid = row["oracle_id"]
        if oid in seen:
            continue
        seen.add(oid)
        rows.append(row)
    print(f"[build] inserting {len(rows):,} cards in single transaction", flush=True)
    con.execute("BEGIN")
    con.executemany(insert_card_sql, rows)
    con.execute("COMMIT")
    card_count = con.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    print(f"[build] cards inserted: {card_count:,}", flush=True)
    del cards, rows

    print(f"[build] parsing {RULINGS_JSON}", flush=True)
    with open(RULINGS_JSON, "rb") as f:
        all_rulings = json.load(f)
    print(f"[build] parsed {len(all_rulings):,} ruling entries", flush=True)

    insert_ruling_sql = (
        "INSERT INTO rulings (oracle_id, published_at, source, comment) "
        "VALUES (:oracle_id, :published_at, :source, :comment)"
    )
    rrows: list[dict] = []
    for r in all_rulings:
        oid = r.get("oracle_id")
        if not oid or oid not in seen:
            continue
        rrows.append({
            "oracle_id": oid,
            "published_at": r.get("published_at"),
            "source": r.get("source"),
            "comment": r.get("comment"),
        })
    print(f"[build] inserting {len(rrows):,} rulings in single transaction", flush=True)
    con.execute("BEGIN")
    con.executemany(insert_ruling_sql, rrows)
    con.execute("COMMIT")
    ruling_count = con.execute("SELECT COUNT(*) FROM rulings").fetchone()[0]
    print(f"[build] rulings inserted: {ruling_count:,}", flush=True)
    del all_rulings, rrows

    built_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    set_meta(con, "last_refresh", built_at)
    set_meta(con, "last_refresh_status", "success")
    set_meta(con, "card_count", card_count)
    set_meta(con, "ruling_count", ruling_count)
    set_meta(con, "scryfall_version", oracle_entry["updated_at"])
    set_meta(con, "scryfall_oracle_updated_at", oracle_entry["updated_at"])
    set_meta(con, "scryfall_rulings_updated_at", rulings_entry["updated_at"])
    set_meta(con, "source", "github-actions")
    set_meta(con, "schema_version", SCHEMA_VERSION)
    con.commit()
    con.execute("PRAGMA optimize")
    con.close()

    print("[build] verifying database", flush=True)
    vcon = sqlite3.connect(str(DB_NEW))
    vcon.row_factory = sqlite3.Row

    integ = vcon.execute("PRAGMA integrity_check").fetchone()[0]
    if integ != "ok":
        print(f"[build] FATAL: integrity_check returned {integ!r}", file=sys.stderr)
        return 1

    lb = vcon.execute(
        "SELECT name, oracle_text FROM cards WHERE name_lower = 'lightning bolt'"
    ).fetchone()
    if not lb or "damage" not in (lb["oracle_text"] or "").lower():
        print(f"[build] FATAL: Lightning Bolt missing or oracle_text suspect: {lb}", file=sys.stderr)
        return 1
    print(f"[build] verify: Lightning Bolt -> {lb['oracle_text']!r}", flush=True)

    rob = vcon.execute(
        "SELECT name, oracle_text FROM cards WHERE name_lower = 'robe of stars'"
    ).fetchone()
    if not rob or "phases out" not in (rob["oracle_text"] or "").lower():
        print(f"[build] FATAL: Robe of Stars missing or oracle_text missing 'phases out': {rob}", file=sys.stderr)
        return 1
    print("[build] verify: Robe of Stars contains 'phases out'", flush=True)

    idx_rows = vcon.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name IN ('cards','rulings')"
    ).fetchall()
    idx_names = {r["name"] for r in idx_rows}
    for want in ("idx_cards_name_lower", "idx_cards_oracle_text", "idx_rulings_oracle"):
        if want not in idx_names:
            print(f"[build] FATAL: missing index {want}", file=sys.stderr)
            return 1
    print("[build] verify: all expected indexes present", flush=True)
    vcon.close()

    if DB.exists():
        DB.unlink()
    DB_NEW.rename(DB)
    db_size = DB.stat().st_size
    print(f"[build] db size: {db_size:,} bytes ({db_size/1048576:.1f} MB)", flush=True)

    if DB_GZ.exists():
        DB_GZ.unlink()
    print(f"[build] gzipping (level 9) to {DB_GZ}", flush=True)
    with open(DB, "rb") as f_in, gzip.open(DB_GZ, "wb", compresslevel=9) as f_out:
        shutil.copyfileobj(f_in, f_out, length=1 << 20)
    gz_size = DB_GZ.stat().st_size
    print(f"[build] gz size: {gz_size:,} bytes ({gz_size/1048576:.1f} MB)", flush=True)

    h = hashlib.sha256()
    with open(DB_GZ, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    digest = h.hexdigest()
    manifest = {
        "filename": "mtg.db.gz",
        "size_bytes": gz_size,
        "sha256": digest,
        "card_count": card_count,
        "ruling_count": ruling_count,
        "scryfall_oracle_updated_at": oracle_entry["updated_at"],
        "scryfall_rulings_updated_at": rulings_entry["updated_at"],
        "built_at": built_at,
        "schema_version": SCHEMA_VERSION,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"[build] manifest written to {MANIFEST}", flush=True)

    for p in (ORACLE_JSON, RULINGS_JSON):
        if p.exists():
            p.unlink()

    elapsed = time.time() - started
    print(
        f"[build] DONE cards={card_count} rulings={ruling_count} "
        f"db={db_size/1048576:.1f}MB gz={gz_size/1048576:.1f}MB "
        f"elapsed={elapsed:.1f}s sha256={digest[:16]}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
