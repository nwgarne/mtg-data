-- Schema mirrors docker01:~/stacks/mtg-api/src/db.js ensureSchema().
-- The docker01 mtg-api service and this GitHub Actions pipeline must
-- produce the same logical structure so the artifact can be served by
-- either consumer interchangeably. Keep the two in lockstep.

CREATE TABLE IF NOT EXISTS cards (
  oracle_id      TEXT PRIMARY KEY,
  name           TEXT NOT NULL,
  name_lower     TEXT NOT NULL,
  mana_cost      TEXT,
  cmc            REAL,
  type_line      TEXT,
  oracle_text    TEXT,
  power          TEXT,
  toughness      TEXT,
  loyalty        TEXT,
  defense        TEXT,
  colors         TEXT,
  color_identity TEXT,
  layout         TEXT,
  card_faces     TEXT,
  keywords       TEXT,
  legalities     TEXT,
  scryfall_uri   TEXT,
  image_normal   TEXT,
  released_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_cards_name_lower   ON cards(name_lower);
CREATE INDEX IF NOT EXISTS idx_cards_oracle_text  ON cards(oracle_text);

CREATE TABLE IF NOT EXISTS rulings (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  oracle_id    TEXT NOT NULL,
  published_at TEXT,
  source       TEXT,
  comment      TEXT,
  FOREIGN KEY (oracle_id) REFERENCES cards(oracle_id)
);

CREATE INDEX IF NOT EXISTS idx_rulings_oracle ON rulings(oracle_id);

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT
);
