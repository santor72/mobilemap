-- 001_init.sql
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS maps (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ,
    synced_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS layers (
    id UUID PRIMARY KEY,
    map_id UUID NOT NULL REFERENCES maps(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    count INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS layers_map_id_idx ON layers(map_id);

CREATE TABLE IF NOT EXISTS features (
    id UUID PRIMARY KEY,
    map_id UUID NOT NULL REFERENCES maps(id) ON DELETE CASCADE,
    layer_id UUID NOT NULL,
    kind TEXT,
    number INTEGER,
    title TEXT,
    description TEXT,
    style JSONB NOT NULL DEFAULT '{}'::jsonb,
    geom geometry(Geometry, 4326) NOT NULL,
    version INTEGER,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS features_map_id_idx ON features(map_id);
CREATE INDEX IF NOT EXISTS features_map_layer_idx ON features(map_id, layer_id);
CREATE INDEX IF NOT EXISTS features_geom_gix ON features USING GIST (geom);

CREATE TABLE IF NOT EXISTS features_staging (
    id UUID PRIMARY KEY,
    map_id UUID NOT NULL,
    layer_id UUID NOT NULL,
    kind TEXT,
    number INTEGER,
    title TEXT,
    description TEXT,
    style JSONB NOT NULL DEFAULT '{}'::jsonb,
    geom geometry(Geometry, 4326) NOT NULL,
    version INTEGER,
    sync_run_id UUID NOT NULL
);

CREATE INDEX IF NOT EXISTS features_staging_run_idx ON features_staging(sync_run_id);
CREATE INDEX IF NOT EXISTS features_staging_geom_gix ON features_staging USING GIST (geom);

CREATE TABLE IF NOT EXISTS sync_runs (
    id UUID PRIMARY KEY,
    map_id UUID,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL,
    error TEXT,
    stats JSONB NOT NULL DEFAULT '{}'::jsonb
);
