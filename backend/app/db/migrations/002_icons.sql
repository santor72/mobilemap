-- 002_icons.sql
-- Mapping GIS asset UUID -> vision label + local PNG path.
-- Rows are append-only: existing UUIDs are never updated or deleted by sync.
CREATE TABLE IF NOT EXISTS icon_uuid (
    id UUID PRIMARY KEY,
    icon_name TEXT NOT NULL,
    icon_path TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS icon_uuid_name_idx ON icon_uuid(icon_name);
