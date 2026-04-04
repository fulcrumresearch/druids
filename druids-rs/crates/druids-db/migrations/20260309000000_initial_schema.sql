-- Initial schema
-- Corresponds to Alembic migration 27476bfd1225

-- User table
CREATE TABLE "user" (
    id UUID PRIMARY KEY,
    github_id INTEGER NOT NULL,
    github_login VARCHAR,
    created_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX ix_user_github_id ON "user" (github_id);

-- Execution table
CREATE TABLE execution (
    id UUID PRIMARY KEY,
    slug VARCHAR NOT NULL,
    user_id UUID NOT NULL REFERENCES "user"(id),
    spec VARCHAR NOT NULL,
    repo_full_name VARCHAR,
    metadata_ JSONB,
    status VARCHAR NOT NULL,
    started_at TIMESTAMPTZ,
    stopped_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    branch_name VARCHAR,
    pr_number INTEGER,
    pr_url VARCHAR,
    error TEXT,
    agents JSONB,
    edges JSONB,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cache_read_input_tokens INTEGER NOT NULL,
    cache_creation_input_tokens INTEGER NOT NULL
);

CREATE INDEX ix_execution_slug ON execution (slug);
CREATE INDEX ix_execution_user_id ON execution (user_id);
CREATE INDEX ix_execution_status ON execution (status);

-- Devbox table
CREATE TABLE devbox (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES "user"(id),
    name VARCHAR NOT NULL,
    repo_full_name VARCHAR NOT NULL,
    instance_id VARCHAR,
    snapshot_id VARCHAR,
    setup_slug VARCHAR,
    setup_completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ
);

CREATE INDEX ix_devbox_user_id ON devbox (user_id);
CREATE INDEX ix_devbox_name ON devbox (name);
CREATE INDEX ix_devbox_repo_full_name ON devbox (repo_full_name);

-- Secret table
CREATE TABLE secret (
    id UUID PRIMARY KEY,
    devbox_id UUID NOT NULL REFERENCES devbox(id),
    name VARCHAR NOT NULL,
    encrypted_value VARCHAR NOT NULL,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ
);

CREATE INDEX ix_secret_devbox_id ON secret (devbox_id);
CREATE INDEX ix_secret_name ON secret (name);
