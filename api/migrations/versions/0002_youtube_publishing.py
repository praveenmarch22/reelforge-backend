"""Publishing to YouTube Shorts (milestone B6; docs/PUBLISHING_YOUTUBE.md, "Data").

Adds the youtube_accounts table, a platform and YouTube fields on posts, and the post title on
projects. The one-live-post rule becomes one per render and platform.

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
CREATE TABLE youtube_accounts (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id            uuid NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
  channel_id         text NOT NULL,
  channel_title      text NOT NULL DEFAULT '',
  picture_url        text NOT NULL DEFAULT '',
  access_token_enc   bytea NOT NULL,
  refresh_token_enc  bytea NOT NULL,
  token_expires_at   timestamptz NOT NULL,
  scopes             text[] NOT NULL DEFAULT '{}',
  status             text NOT NULL DEFAULT 'connected'
                     CHECK (status IN ('connected', 'reconnect_required')),
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER youtube_accounts_updated_at BEFORE UPDATE ON youtube_accounts
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

ALTER TABLE posts
  ADD COLUMN platform text NOT NULL DEFAULT 'instagram' CHECK (platform IN ('youtube', 'instagram')),
  ADD COLUMN youtube_account_id uuid REFERENCES youtube_accounts(id) ON DELETE SET NULL,
  ADD COLUMN title text NOT NULL DEFAULT '' CHECK (char_length(title) <= 100),
  ADD COLUMN visibility text NOT NULL DEFAULT 'public' CHECK (visibility IN ('private', 'unlisted', 'public')),
  ADD COLUMN progress smallint NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
  ADD COLUMN error_code text;
DROP INDEX posts_one_per_render;
CREATE UNIQUE INDEX posts_one_per_render_platform ON posts (render_id, platform) WHERE state <> 'failed';

ALTER TABLE projects
  ADD COLUMN post_title text NOT NULL DEFAULT '' CHECK (char_length(post_title) <= 100);
"""
    )


def downgrade():
    op.execute(
        """
DELETE FROM posts WHERE platform = 'youtube';
ALTER TABLE projects DROP COLUMN post_title;
DROP INDEX posts_one_per_render_platform;
CREATE UNIQUE INDEX posts_one_per_render ON posts (render_id) WHERE state <> 'failed';
ALTER TABLE posts DROP COLUMN error_code, DROP COLUMN progress, DROP COLUMN visibility, DROP COLUMN title,
  DROP COLUMN youtube_account_id, DROP COLUMN platform;
DROP TABLE youtube_accounts;
"""
    )
