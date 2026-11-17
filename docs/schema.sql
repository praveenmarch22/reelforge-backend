-- ReelForge database schema (PostgreSQL 16)
-- Reference schema for version 1. In the product, Alembic migrations create these tables;
-- this file is the agreed target they must match. See DB_DESIGN.md for explanations.

CREATE EXTENSION IF NOT EXISTS citext;
-- gen_random_uuid() is built into PostgreSQL 13+.

-- Keeps updated_at current on every UPDATE.
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------- users and auth

CREATE TABLE users (
  id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email                  citext NOT NULL UNIQUE,
  password_hash          text NOT NULL,
  email_verified_at      timestamptz,
  name                   text NOT NULL DEFAULT '',
  niche                  text NOT NULL DEFAULT '',
  default_voice_id       text NOT NULL DEFAULT '',
  default_caption_style  text NOT NULL DEFAULT 'bold-pop'
                         CHECK (default_caption_style IN ('bold-pop', 'clean', 'neon')),
  default_mode           text NOT NULL DEFAULT 'faceless'
                         CHECK (default_mode IN ('faceless', 'full_ai')),
  onboarding_completed_at timestamptz,
  is_admin               boolean NOT NULL DEFAULT false,
  is_tester              boolean NOT NULL DEFAULT false,
  disabled_at            timestamptz,
  last_login_at          timestamptz,
  deleted_at             timestamptz,
  created_at             timestamptz NOT NULL DEFAULT now(),
  updated_at             timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE auth_tokens (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind        text NOT NULL CHECK (kind IN ('verify_email', 'reset_password')),
  token_hash  text NOT NULL UNIQUE,
  expires_at  timestamptz NOT NULL,
  used_at     timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX auth_tokens_user_kind_idx ON auth_tokens (user_id, kind);

CREATE TABLE refresh_tokens (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  jti         text NOT NULL UNIQUE,
  family_id   uuid NOT NULL,          -- all rotations of one login; reuse of an old token revokes the family
  user_agent  text NOT NULL DEFAULT '',
  ip_address  inet,
  expires_at  timestamptz NOT NULL,
  revoked_at  timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX refresh_tokens_user_idx ON refresh_tokens (user_id) WHERE revoked_at IS NULL;
CREATE INDEX refresh_tokens_family_idx ON refresh_tokens (family_id);

CREATE TABLE instagram_accounts (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id              uuid NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
  ig_user_id           text NOT NULL,
  username             text NOT NULL,
  profile_picture_url  text NOT NULL DEFAULT '',
  account_type         text NOT NULL CHECK (account_type IN ('BUSINESS', 'MEDIA_CREATOR')),
  access_token_enc     bytea NOT NULL,
  token_expires_at     timestamptz NOT NULL,
  scopes               text[] NOT NULL DEFAULT '{}',
  status               text NOT NULL DEFAULT 'connected'
                       CHECK (status IN ('connected', 'expired', 'revoked')),
  last_refreshed_at    timestamptz,
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX instagram_accounts_expiry_idx ON instagram_accounts (token_expires_at) WHERE status = 'connected';

-- ---------------------------------------------------------------- credits

CREATE TABLE credit_accounts (
  user_id            uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  balance            integer NOT NULL DEFAULT 0 CHECK (balance >= 0),
  monthly_allowance  integer NOT NULL DEFAULT 100 CHECK (monthly_allowance >= 0),
  resets_at          timestamptz NOT NULL,
  updated_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX credit_accounts_resets_idx ON credit_accounts (resets_at);

-- ---------------------------------------------------------------- music library

CREATE TABLE music_tracks (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  title             text NOT NULL,
  s3_key            text NOT NULL UNIQUE,
  duration_seconds  numeric(8,3) NOT NULL CHECK (duration_seconds > 0),
  moods             text[] NOT NULL DEFAULT '{}',
  license           text NOT NULL,
  source_url        text NOT NULL DEFAULT '',
  is_active         boolean NOT NULL DEFAULT true,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX music_tracks_moods_idx ON music_tracks USING gin (moods) WHERE is_active;

-- ---------------------------------------------------------------- projects

CREATE TABLE projects (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title               text NOT NULL DEFAULT 'Untitled Reel',
  topic               text NOT NULL DEFAULT '',
  source_script       text,             -- set when the creator pasted their own script
  tone                text NOT NULL DEFAULT 'educational'
                      CHECK (tone IN ('educational', 'funny', 'motivational', 'storytelling', 'dramatic')),
  mode                text NOT NULL DEFAULT 'faceless' CHECK (mode IN ('faceless', 'full_ai')),
  target_seconds      integer NOT NULL DEFAULT 30 CHECK (target_seconds IN (15, 30, 60, 90)),
  style               text NOT NULL DEFAULT 'cinematic',
  character           text NOT NULL DEFAULT '',
  voice_id            text NOT NULL DEFAULT '',
  music_mood          text NOT NULL DEFAULT 'calm'
                      CHECK (music_mood IN ('calm','joyful','excited','funny','inspiring','triumphant',
                                            'serious','tense','mysterious','sad')),
  music_track_id      uuid REFERENCES music_tracks(id) ON DELETE SET NULL,
  music_asset_id      uuid,             -- uploaded music; FK added below (assets is created later)
  settings            jsonb NOT NULL DEFAULT
                      '{"captionStyle":"bold-pop","showCaptions":true,"voiceVolume":1.0,
                        "musicVolume":0.25,"duckedVolume":0.08,"ducking":true,"transition":"cut"}',
  status              text NOT NULL DEFAULT 'scripting'
                      CHECK (status IN ('scripting','draft','generating','needs_attention','ready',
                                        'rendering','render_failed','rendered','posting','post_failed','posted')),
  timeline            jsonb,
  timeline_version    integer NOT NULL DEFAULT 0,
  post_caption        text NOT NULL DEFAULT '' CHECK (char_length(post_caption) <= 2200),
  hashtags            text[] NOT NULL DEFAULT '{}' CHECK (cardinality(hashtags) <= 30),
  thumbnail_asset_id  uuid,             -- FK added below
  deleted_at          timestamptz,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX projects_user_list_idx ON projects (user_id, updated_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX projects_user_status_idx ON projects (user_id, status) WHERE deleted_at IS NULL;
CREATE INDEX projects_deleted_idx ON projects (deleted_at) WHERE deleted_at IS NOT NULL;

CREATE TABLE assets (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id           uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  project_id        uuid REFERENCES projects(id) ON DELETE CASCADE,
  kind              text NOT NULL CHECK (kind IN ('voice','image','video_clip','keyframe','music',
                                                  'upload','render','thumbnail')),
  status            text NOT NULL DEFAULT 'ready' CHECK (status IN ('pending_upload','ready','rejected')),
  s3_key            text NOT NULL UNIQUE,
  content_type      text NOT NULL,
  size_bytes        bigint CHECK (size_bytes >= 0),
  duration_seconds  numeric(8,3),
  width             integer,
  height            integer,
  provider          text NOT NULL,
  input_hash        text,
  meta              jsonb NOT NULL DEFAULT '{}',
  replaced_at       timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX assets_cache_idx ON assets (project_id, kind, input_hash) WHERE input_hash IS NOT NULL;
CREATE INDEX assets_replaced_idx ON assets (replaced_at) WHERE replaced_at IS NOT NULL;
CREATE INDEX assets_pending_idx ON assets (created_at) WHERE status = 'pending_upload';
CREATE INDEX assets_user_idx ON assets (user_id);

ALTER TABLE projects
  ADD CONSTRAINT projects_thumbnail_fk FOREIGN KEY (thumbnail_asset_id) REFERENCES assets(id) ON DELETE SET NULL,
  ADD CONSTRAINT projects_music_asset_fk FOREIGN KEY (music_asset_id) REFERENCES assets(id) ON DELETE SET NULL;

CREATE TABLE scenes (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id         uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  position           integer NOT NULL CHECK (position >= 1),
  narration          text NOT NULL CHECK (char_length(narration) BETWEEN 1 AND 1500),
  on_screen_text     text NOT NULL DEFAULT '' CHECK (char_length(on_screen_text) <= 80),
  visual_prompt      text NOT NULL DEFAULT '',
  stock_query        text NOT NULL DEFAULT '',
  mood               text NOT NULL DEFAULT 'calm'
                     CHECK (mood IN ('calm','joyful','excited','funny','inspiring','triumphant',
                                     'serious','tense','mysterious','sad')),
  visual_source      text NOT NULL DEFAULT 'stock'
                     CHECK (visual_source IN ('stock', 'ai_image', 'ai_video', 'upload')),
  voice_asset_id     uuid REFERENCES assets(id) ON DELETE SET NULL,
  visual_asset_id    uuid REFERENCES assets(id) ON DELETE SET NULL,
  keyframe_asset_id  uuid REFERENCES assets(id) ON DELETE SET NULL,
  words              jsonb NOT NULL DEFAULT '[]',
  words_edited       boolean NOT NULL DEFAULT false,   -- creator fixed caption words by hand (CA-4)
  trim_start         numeric(6,3) NOT NULL DEFAULT 0 CHECK (trim_start >= 0),
  trim_end           numeric(6,3) CHECK (trim_end IS NULL OR trim_end > trim_start),
  voice_status       text NOT NULL DEFAULT 'missing'
                     CHECK (voice_status IN ('missing','outdated','queued','running','ready','failed')),
  visual_status      text NOT NULL DEFAULT 'missing'
                     CHECK (visual_status IN ('missing','outdated','queued','running','ready','failed')),
  last_error         text,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT scenes_position_unique UNIQUE (project_id, position) DEFERRABLE INITIALLY IMMEDIATE
);

-- ---------------------------------------------------------------- jobs, renders, posts

CREATE TABLE jobs (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id            uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  project_id         uuid REFERENCES projects(id) ON DELETE CASCADE,
  scene_id           uuid REFERENCES scenes(id) ON DELETE CASCADE,
  type               text NOT NULL CHECK (type IN ('ideas','script','rewrite_scene','hooks','caption',
                                                   'voice','transcribe','visual','keyframe','ai_video',
                                                   'music','render','publish')),
  state              text NOT NULL DEFAULT 'queued'
                     CHECK (state IN ('queued','running','succeeded','failed','cancelled')),
  progress           smallint NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
  provider           text,
  attempts           smallint NOT NULL DEFAULT 0,
  celery_task_id     text,
  credits_reserved   integer NOT NULL DEFAULT 0 CHECK (credits_reserved >= 0),
  cost_estimate_usd  numeric(10,5) NOT NULL DEFAULT 0,
  input              jsonb NOT NULL DEFAULT '{}',
  output             jsonb,
  error_code         text,
  error_message      text,
  queued_at          timestamptz NOT NULL DEFAULT now(),
  started_at         timestamptz,
  finished_at        timestamptz
);
CREATE UNIQUE INDEX jobs_one_active_per_scene_type ON jobs (scene_id, type)
  WHERE scene_id IS NOT NULL AND state IN ('queued', 'running');
CREATE UNIQUE INDEX jobs_one_active_project_job ON jobs (project_id, type)
  WHERE scene_id IS NULL AND project_id IS NOT NULL AND type IN ('script','music','render')
    AND state IN ('queued', 'running');
CREATE INDEX jobs_project_state_idx ON jobs (project_id, state);
CREATE INDEX jobs_stuck_idx ON jobs (state, started_at) WHERE state = 'running';
CREATE INDEX jobs_user_recent_idx ON jobs (user_id, queued_at DESC);
CREATE INDEX jobs_failed_idx ON jobs (finished_at DESC) WHERE state = 'failed';

CREATE TABLE credit_ledger (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  amount      integer NOT NULL CHECK (amount <> 0),
  kind        text NOT NULL CHECK (kind IN ('grant', 'reserve', 'refund', 'adjust')),
  job_id      uuid REFERENCES jobs(id) ON DELETE SET NULL,
  note        text NOT NULL DEFAULT '',
  created_by  uuid REFERENCES users(id) ON DELETE SET NULL,   -- admin for 'adjust'
  created_at  timestamptz NOT NULL DEFAULT now(),
  CHECK ((kind = 'reserve' AND amount < 0) OR (kind IN ('grant', 'refund') AND amount > 0) OR kind = 'adjust')
);
CREATE UNIQUE INDEX credit_ledger_once_per_job ON credit_ledger (job_id, kind)
  WHERE job_id IS NOT NULL AND kind IN ('reserve', 'refund');
CREATE INDEX credit_ledger_user_idx ON credit_ledger (user_id, created_at DESC);

CREATE TABLE renders (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id        uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  job_id            uuid REFERENCES jobs(id) ON DELETE SET NULL,
  timeline_version  integer NOT NULL,
  timeline          jsonb NOT NULL,          -- exact snapshot rendered (R-3)
  asset_id          uuid REFERENCES assets(id) ON DELETE SET NULL,
  thumbnail_asset_id uuid REFERENCES assets(id) ON DELETE SET NULL,
  state             text NOT NULL DEFAULT 'queued'
                    CHECK (state IN ('queued','running','succeeded','failed','cancelled')),
  progress          smallint NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
  error_message     text,
  created_at        timestamptz NOT NULL DEFAULT now(),
  finished_at       timestamptz
);
CREATE INDEX renders_project_idx ON renders (project_id, created_at DESC);

CREATE TABLE posts (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id            uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  render_id             uuid NOT NULL REFERENCES renders(id) ON DELETE CASCADE,
  instagram_account_id  uuid REFERENCES instagram_accounts(id) ON DELETE SET NULL,
  job_id                uuid REFERENCES jobs(id) ON DELETE SET NULL,
  caption               text NOT NULL CHECK (char_length(caption) <= 2200),
  share_to_feed         boolean NOT NULL DEFAULT true,
  container_id          text,
  media_id              text,
  permalink             text,
  state                 text NOT NULL DEFAULT 'queued'
                        CHECK (state IN ('queued','uploading','processing','publishing','published','failed')),
  error_message         text,
  published_at          timestamptz,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX posts_one_per_render ON posts (render_id) WHERE state <> 'failed';
CREATE INDEX posts_project_idx ON posts (project_id, created_at DESC);

-- ---------------------------------------------------------------- platform

CREATE TABLE provider_switches (
  provider    text PRIMARY KEY,
  enabled     boolean NOT NULL DEFAULT true,
  reason      text NOT NULL DEFAULT '',
  updated_by  uuid REFERENCES users(id) ON DELETE SET NULL,
  updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE idempotency_keys (
  user_id        uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  key            text NOT NULL,
  request_hash   text NOT NULL,      -- same key with a different body is rejected
  status_code    smallint,
  response_body  jsonb,
  created_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, key)
);
CREATE INDEX idempotency_keys_created_idx ON idempotency_keys (created_at);

CREATE TABLE usage_daily (
  day                date NOT NULL,
  provider           text NOT NULL,
  job_type           text NOT NULL,
  calls              integer NOT NULL DEFAULT 0,
  failures           integer NOT NULL DEFAULT 0,
  credits_charged    integer NOT NULL DEFAULT 0,
  cost_estimate_usd  numeric(12,5) NOT NULL DEFAULT 0,
  PRIMARY KEY (day, provider, job_type)
);

-- ---------------------------------------------------------------- triggers

CREATE TRIGGER users_updated_at BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER instagram_accounts_updated_at BEFORE UPDATE ON instagram_accounts FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER credit_accounts_updated_at BEFORE UPDATE ON credit_accounts FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER music_tracks_updated_at BEFORE UPDATE ON music_tracks FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER projects_updated_at BEFORE UPDATE ON projects FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER scenes_updated_at BEFORE UPDATE ON scenes FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER posts_updated_at BEFORE UPDATE ON posts FOR EACH ROW EXECUTE FUNCTION set_updated_at();
