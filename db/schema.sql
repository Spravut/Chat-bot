-- Dating Bot (Stage 1) - PostgreSQL schema (draft)
-- This file is meant for documentation/proof-of-design at Этап 1.

BEGIN;

-- 1) Users
CREATE TABLE IF NOT EXISTS users (
  id              BIGSERIAL PRIMARY KEY,
  telegram_id     BIGINT      NOT NULL UNIQUE,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2) User profile (primary data for Level 1 ranking)
CREATE TABLE IF NOT EXISTS user_profiles (
  id               BIGSERIAL PRIMARY KEY,
  user_id          BIGINT NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,

  age              INTEGER,
  gender           VARCHAR(16),
  seeking_gender  VARCHAR(16),

  city             VARCHAR(128),
  country          VARCHAR(128),

  age_min          INTEGER,
  age_max          INTEGER,

  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_profiles_city ON user_profiles(city);
CREATE INDEX IF NOT EXISTS idx_user_profiles_user_id ON user_profiles(user_id);

-- 3) Interests reference + user_interest links
CREATE TABLE IF NOT EXISTS interests (
  id          BIGSERIAL PRIMARY KEY,
  name        VARCHAR(128) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS user_interests (
  user_profile_id BIGINT NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
  interest_id     BIGINT NOT NULL REFERENCES interests(id) ON DELETE CASCADE,
  PRIMARY KEY (user_profile_id, interest_id)
);

-- 4) Photos
CREATE TABLE IF NOT EXISTS user_photos (
  id          BIGSERIAL PRIMARY KEY,
  user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,

  photo_url   TEXT NOT NULL,
  sort_order  INTEGER NOT NULL,

  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (user_id, sort_order)
);

CREATE INDEX IF NOT EXISTS idx_user_photos_user_id ON user_photos(user_id);

-- 5) Likes (directed edges)
CREATE TABLE IF NOT EXISTS likes (
  id            BIGSERIAL PRIMARY KEY,
  from_user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  to_user_id   BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT likes_no_self_like CHECK (from_user_id <> to_user_id),
  UNIQUE (from_user_id, to_user_id)
);

CREATE INDEX IF NOT EXISTS idx_likes_to_user_id ON likes(to_user_id);
CREATE INDEX IF NOT EXISTS idx_likes_from_user_id ON likes(from_user_id);

-- 6) Matches (undirected edges stored as canonical pair)
CREATE TABLE IF NOT EXISTS matches (
  id          BIGSERIAL PRIMARY KEY,
  user_a_id  BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  user_b_id  BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  -- Canonical storage rule; enforced at DB level for consistency.
  CONSTRAINT matches_canonical_order CHECK (user_a_id < user_b_id),
  UNIQUE (user_a_id, user_b_id)
);

CREATE INDEX IF NOT EXISTS idx_matches_user_a_id ON matches(user_a_id);
CREATE INDEX IF NOT EXISTS idx_matches_user_b_id ON matches(user_b_id);

-- 7) Dialogs (also undirected, stored as canonical pair)
CREATE TABLE IF NOT EXISTS dialogs (
  id         BIGSERIAL PRIMARY KEY,
  user_a_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  user_b_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT dialogs_canonical_order CHECK (user_a_id < user_b_id),
  UNIQUE (user_a_id, user_b_id)
);

CREATE INDEX IF NOT EXISTS idx_dialogs_user_a_id ON dialogs(user_a_id);
CREATE INDEX IF NOT EXISTS idx_dialogs_user_b_id ON dialogs(user_b_id);

-- 8) Messages (within a dialog)
CREATE TABLE IF NOT EXISTS messages (
  id         BIGSERIAL PRIMARY KEY,
  dialog_id BIGINT NOT NULL REFERENCES dialogs(id) ON DELETE CASCADE,
  sender_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,

  content    TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_messages_dialog_id_created_at ON messages(dialog_id, created_at);

-- 9) Ratings (cached/derived per user)
CREATE TABLE IF NOT EXISTS ratings (
  user_id       BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,

  level1_score  NUMERIC NOT NULL DEFAULT 0,
  level2_score  NUMERIC NOT NULL DEFAULT 0,
  level3_score  NUMERIC NOT NULL DEFAULT 0,

  computed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 10) Rating events (journal for Level 2)
CREATE TABLE IF NOT EXISTS rating_events (
  id              BIGSERIAL PRIMARY KEY,
  user_id         BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,

  event_type      VARCHAR(64) NOT NULL,
  target_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,

  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  payload         JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_rating_events_user_id_created_at ON rating_events(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_rating_events_target_user_id ON rating_events(target_user_id);

-- 11) Referrals (Level 3 extra factor)
CREATE TABLE IF NOT EXISTS referrals (
  id                BIGSERIAL PRIMARY KEY,
  inviter_user_id  BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  referred_user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  UNIQUE (referred_user_id)
);

CREATE INDEX IF NOT EXISTS idx_referrals_inviter_user_id ON referrals(inviter_user_id);

COMMIT;

