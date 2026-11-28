"""Daily autopilot (docs/AUTOPILOT.md): per-user settings and the topic queue.

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
CREATE TABLE autopilot_settings (
  user_id         uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  enabled         boolean NOT NULL DEFAULT false,
  mode            text NOT NULL DEFAULT 'approve' CHECK (mode IN ('approve', 'auto')),
  run_hour_utc    smallint NOT NULL DEFAULT 17 CHECK (run_hour_utc BETWEEN 0 AND 23),
  rotation        jsonb NOT NULL DEFAULT '{}'::jsonb,
  tone            text NOT NULL DEFAULT 'storytelling',
  target_seconds  smallint NOT NULL DEFAULT 30 CHECK (target_seconds IN (15, 30, 60, 90)),
  visibility      text NOT NULL DEFAULT 'public' CHECK (visibility IN ('private', 'unlisted', 'public')),
  last_run_on     date,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER autopilot_settings_updated_at BEFORE UPDATE ON autopilot_settings
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE autopilot_topics (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  position    integer NOT NULL,
  category    text NOT NULL CHECK (char_length(category) BETWEEN 1 AND 40),
  subject     text NOT NULL CHECK (char_length(subject) BETWEEN 1 AND 80),
  angle       text NOT NULL CHECK (char_length(angle) BETWEEN 1 AND 160),
  status      text NOT NULL DEFAULT 'queued' CHECK (status IN
              ('queued', 'running', 'awaiting_approval', 'publishing', 'published', 'skipped', 'failed')),
  project_id  uuid REFERENCES projects(id) ON DELETE SET NULL,
  error       text,
  run_on      date,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX autopilot_topics_user_idx ON autopilot_topics (user_id, status, position);
CREATE TRIGGER autopilot_topics_updated_at BEFORE UPDATE ON autopilot_topics
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- The job that suggests topics (L3).
ALTER TABLE jobs DROP CONSTRAINT jobs_type_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_type_check CHECK (type IN
  ('ideas','script','rewrite_scene','hooks','caption','voice','transcribe','visual','keyframe','ai_video',
   'music','render','publish','autopilot_topics'));
"""
    )


def downgrade():
    op.execute(
        """
DROP TABLE autopilot_topics;
DROP TABLE autopilot_settings;
DELETE FROM jobs WHERE type = 'autopilot_topics';
ALTER TABLE jobs DROP CONSTRAINT jobs_type_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_type_check CHECK (type IN
  ('ideas','script','rewrite_scene','hooks','caption','voice','transcribe','visual','keyframe','ai_video',
   'music','render','publish'));
"""
    )
