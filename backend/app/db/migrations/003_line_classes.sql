-- 003_line_classes.sql
CREATE TABLE IF NOT EXISTS line_class_map (
    icon_name TEXT NOT NULL,
    class_name TEXT NOT NULL,
    CONSTRAINT line_class_map_icon_class_uidx UNIQUE (icon_name, class_name)
);

CREATE TABLE IF NOT EXISTS feature_classes (
    feature_id UUID NOT NULL REFERENCES features(id) ON DELETE CASCADE,
    class_name TEXT NOT NULL,
    PRIMARY KEY (feature_id, class_name)
);

CREATE INDEX IF NOT EXISTS feature_classes_class_idx ON feature_classes(class_name);

INSERT INTO line_class_map(icon_name, class_name) VALUES
    ('arrow_down_left', 'backbone'),
    ('camera', 'br'),
    ('atom', 'access')
ON CONFLICT ON CONSTRAINT line_class_map_icon_class_uidx DO NOTHING;
