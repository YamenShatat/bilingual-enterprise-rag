-- Documents and their chunks, plus the pgvector extension.
--
-- documents carries the metadata from data/manifest.json. The allowed values below mirror
-- bilingual_rag.ingestion.manifest; a test keeps the two in step. access_level is what the
-- search filters on, so a typo must fail loudly here instead of quietly hiding or exposing text.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE documents (
    id           text PRIMARY KEY CHECK (id ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
    path         text NOT NULL UNIQUE CHECK (btrim(path) <> ''),  -- equals the chunks' filename
    title        text NOT NULL CHECK (btrim(title) <> ''),
    department   text NOT NULL CHECK (btrim(department) <> ''),
    language     text NOT NULL CHECK (language IN ('en', 'ar')),
    format       text NOT NULL CHECK (format IN ('md', 'txt', 'docx', 'pdf')),
    access_level text NOT NULL CHECK (
        access_level IN ('public', 'employee', 'engineering', 'hr', 'management')
    ),
    topic        text NOT NULL CHECK (btrim(topic) <> ''),
    pair         text,  -- path of the same document in the other language, if any
    digits       text CHECK (digits IN ('arabic-indic', 'western')),
    -- sha256 of the document's chunks (page, index, offsets, text). Re-ingesting a document
    -- whose hash is unchanged writes nothing, so ingestion is idempotent.
    content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

-- The id is a readable natural key, "<document id>-p<page>-c<index>", and not a serial number,
-- so evaluation data and citations can refer to a chunk and still find it after re-ingestion.
CREATE TABLE chunks (
    id           text PRIMARY KEY,
    document_id  text NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    page         integer NOT NULL CHECK (page >= 1),
    chunk_index  integer NOT NULL CHECK (chunk_index >= 0),
    text         text NOT NULL CHECK (text <> ''),
    start_offset integer NOT NULL CHECK (start_offset >= 0),
    end_offset   integer NOT NULL,
    -- Implied by the derived id below; kept because its index serves lookups and cascade
    -- deletes by document_id, which PostgreSQL does not index on its own for a foreign key.
    UNIQUE (document_id, page, chunk_index),
    CHECK (id = document_id || '-p' || page::text || '-c' || chunk_index::text),
    -- text is exactly page_text[start:end], so its length must equal the span.
    CHECK (end_offset - start_offset = char_length(text))
);
