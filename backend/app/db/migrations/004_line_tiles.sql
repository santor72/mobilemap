-- 004_line_tiles.sql
ALTER TABLE maps
    ADD COLUMN IF NOT EXISTS lines_tile_version TEXT,
    ADD COLUMN IF NOT EXISTS lines_tile_version_prev TEXT;
