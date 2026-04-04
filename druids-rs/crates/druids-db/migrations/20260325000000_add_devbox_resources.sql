-- Add devbox vcpus, memory_mb, disk_mb columns
-- Corresponds to Alembic migration 24af03683ef2

ALTER TABLE devbox ADD COLUMN vcpus INTEGER;
ALTER TABLE devbox ADD COLUMN memory_mb INTEGER;
ALTER TABLE devbox ADD COLUMN disk_mb INTEGER;
