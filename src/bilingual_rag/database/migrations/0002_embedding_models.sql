-- Registry of embedding models.
--
-- A pgvector column has a fixed dimension, and Week 3 compares models with different ones, so
-- each model gets its own table, embeddings_<key>, holding one vector per chunk. The tables are
-- created by bilingual_rag.database.embedding_store.ensure_embedding_model, which records the
-- model here first so a key can never silently be reused for a different model or dimension.

CREATE TABLE embedding_models (
    key        text PRIMARY KEY CHECK (key ~ '^[a-z][a-z0-9_]{0,39}$'),
    model_name text NOT NULL CHECK (btrim(model_name) <> ''),
    dimension  integer NOT NULL CHECK (dimension BETWEEN 1 AND 16000),  -- pgvector's limit
    table_name text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (table_name = 'embeddings_' || key)
);
