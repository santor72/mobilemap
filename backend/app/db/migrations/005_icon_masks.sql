-- 005_icon_masks.sql
-- Deduped shape masks + per-UUID glyph color for local PNG compose.

CREATE TABLE IF NOT EXISTS icon_masks (
    shape_hash TEXT PRIMARY KEY,
    mask_path TEXT NOT NULL,
    width INT NOT NULL,
    height INT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE icon_uuid
    ADD COLUMN IF NOT EXISTS shape_hash TEXT REFERENCES icon_masks(shape_hash),
    ADD COLUMN IF NOT EXISTS glyph_color TEXT,
    ADD COLUMN IF NOT EXISTS source_path TEXT,
    ADD COLUMN IF NOT EXISTS composed_path TEXT;

CREATE INDEX IF NOT EXISTS icon_uuid_shape_hash_idx ON icon_uuid(shape_hash);
