-- Keyword search (D-021): each chunk's text as a tsvector, stemmed in its document's language
-- with PostgreSQL's Snowball stemmers ('arabic' or 'english').
--
-- One stemmer per chunk, not both: the Arabic stemmer passes Latin words through untouched, so
-- running both would index English stopwords ("of", "the") in every English chunk, and
-- PostgreSQL's ranking has no IDF to discount them.
--
-- Only this index is stemmed. The stored text is never changed (D-004), even though the Arabic
-- stemmer also folds hamza forms and strips the article.
--
-- Triggers keep the column right whatever code writes a chunk: it is computed when a chunk is
-- inserted or its text changes, and recomputed for every chunk when a document's language
-- changes.

CREATE FUNCTION search_config(language text) RETURNS regconfig
    LANGUAGE sql IMMUTABLE STRICT
    RETURN CASE language WHEN 'ar' THEN 'arabic'::regconfig ELSE 'english'::regconfig END;

ALTER TABLE chunks ADD COLUMN search_vector tsvector;

CREATE FUNCTION chunks_set_search_vector() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.search_vector := to_tsvector(
        search_config((SELECT language FROM documents WHERE id = NEW.document_id)),
        NEW.text
    );
    RETURN NEW;
END
$$;

CREATE TRIGGER chunks_search_vector
    BEFORE INSERT OR UPDATE OF text, document_id ON chunks
    FOR EACH ROW EXECUTE FUNCTION chunks_set_search_vector();

CREATE FUNCTION documents_refresh_search_vectors() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE chunks SET search_vector = to_tsvector(search_config(NEW.language), text)
    WHERE document_id = NEW.id;
    RETURN NULL;
END
$$;

CREATE TRIGGER documents_search_language
    AFTER UPDATE OF language ON documents
    FOR EACH ROW WHEN (OLD.language IS DISTINCT FROM NEW.language)
    EXECUTE FUNCTION documents_refresh_search_vectors();

-- Chunks stored before this migration.
UPDATE chunks c SET search_vector = to_tsvector(search_config(d.language), c.text)
FROM documents d WHERE d.id = c.document_id;

ALTER TABLE chunks ALTER COLUMN search_vector SET NOT NULL;

CREATE INDEX chunks_search_vector_idx ON chunks USING gin (search_vector);
