-- Runs once on first database initialisation.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Separate database for integration tests (never touches dev data).
SELECT 'CREATE DATABASE wta_test'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'wta_test')\gexec
