CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY,
    name       VARCHAR(100) NOT NULL,
    value      TEXT         NOT NULL,
    updated_at TIMESTAMP    NOT NULL DEFAULT NOW()
);

INSERT INTO users (id, name, value)
SELECT
    i,
    'user_' || i,
    'initial_' || i
FROM generate_series(1, 100) AS i
ON CONFLICT (id) DO NOTHING;
