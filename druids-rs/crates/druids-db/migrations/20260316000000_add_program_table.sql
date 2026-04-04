-- Add program table and execution.program_id
-- Corresponds to Alembic migration f8a54c1a865d

-- Program table
CREATE TABLE program (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES "user"(id),
    source TEXT NOT NULL,
    source_hash VARCHAR NOT NULL,
    created_at TIMESTAMPTZ
);

CREATE INDEX ix_program_source_hash ON program (source_hash);
CREATE INDEX ix_program_user_id ON program (user_id);

-- Add program_id to execution
ALTER TABLE execution ADD COLUMN program_id UUID REFERENCES program(id);
CREATE INDEX ix_execution_program_id ON execution (program_id);
