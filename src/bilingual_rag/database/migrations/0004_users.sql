-- Users and what each may read (D-024).
--
-- access_levels is the whole of a user's document permission: the API passes exactly this list
-- to the search, which filters in SQL (D-015). Admins are created with every level; employees
-- with the levels chosen for them (for example public, employee and hr). role decides only who
-- may upload documents. There is no self-registration: users are created by
-- scripts/create_user.py.
--
-- password_hash is "scrypt$<n>$<r>$<p>$<salt>$<hash>" (bilingual_rag.auth.passwords); the
-- password itself is never stored. active = false locks a user out at once: the API reads the
-- user on every request, so an issued token stops working too.

CREATE TABLE users (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username      text NOT NULL UNIQUE CHECK (username ~ '^[a-z0-9][a-z0-9._-]{2,63}$'),
    password_hash text NOT NULL CHECK (password_hash LIKE 'scrypt$%'),
    role          text NOT NULL CHECK (role IN ('admin', 'employee')),
    access_levels text[] NOT NULL CHECK (
        access_levels <@ ARRAY['public', 'employee', 'engineering', 'hr', 'management']::text[]
    ),
    active        boolean NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now()
);
