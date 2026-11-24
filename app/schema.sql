CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS users(id uuid PRIMARY KEY);
CREATE TABLE IF NOT EXISTS experiences(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), owner uuid NOT NULL REFERENCES users, name text NOT NULL);
CREATE TABLE IF NOT EXISTS applications(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), owner uuid NOT NULL REFERENCES users, company text NOT NULL, role text NOT NULL,
 created timestamptz DEFAULT now());
CREATE TABLE IF NOT EXISTS questions(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), app uuid NOT NULL REFERENCES applications, title text NOT NULL,
 max_chars int NOT NULL CHECK(max_chars>0), spaces boolean NOT NULL DEFAULT true,
 blocks jsonb NOT NULL DEFAULT '[]', rev int NOT NULL DEFAULT 1, created timestamptz DEFAULT clock_timestamp());
CREATE TABLE IF NOT EXISTS documents(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), owner uuid NOT NULL REFERENCES users, app uuid REFERENCES applications,
 name text NOT NULL, kind text NOT NULL, filename text NOT NULL, digest text NOT NULL, metadata jsonb NOT NULL,
 version int NOT NULL DEFAULT 1, status text NOT NULL DEFAULT '대기', created timestamptz DEFAULT now());
CREATE TABLE IF NOT EXISTS passages(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), doc uuid NOT NULL REFERENCES documents ON DELETE CASCADE,
 pos int NOT NULL, location text NOT NULL, body text NOT NULL, embedding vector, model text, UNIQUE(doc,pos));
CREATE TABLE IF NOT EXISTS facts(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), owner uuid NOT NULL REFERENCES users, experience uuid REFERENCES experiences,
 title text NOT NULL, body text NOT NULL, role text NOT NULL DEFAULT '', style text NOT NULL DEFAULT '',
 status text NOT NULL DEFAULT '제안', version int NOT NULL DEFAULT 1, embedding vector, model text);
CREATE TABLE IF NOT EXISTS citations(
 fact uuid REFERENCES facts ON DELETE CASCADE, passage uuid REFERENCES passages ON DELETE CASCADE,
 PRIMARY KEY(fact,passage));
CREATE TABLE IF NOT EXISTS tags(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), owner uuid NOT NULL REFERENCES users, name text NOT NULL,
 description text NOT NULL DEFAULT '', aliases text[] NOT NULL DEFAULT '{}', parent uuid REFERENCES tags ON DELETE SET NULL,
 UNIQUE(owner,name));
CREATE TABLE IF NOT EXISTS links(
 tag uuid REFERENCES tags ON DELETE CASCADE, target_type text NOT NULL, target uuid NOT NULL,
 state text NOT NULL, PRIMARY KEY(tag,target_type,target));
CREATE TABLE IF NOT EXISTS paragraphs(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), owner uuid NOT NULL REFERENCES users, question uuid NOT NULL REFERENCES questions,
 experience uuid NOT NULL REFERENCES experiences, previous uuid REFERENCES paragraphs, body text NOT NULL,
 details jsonb NOT NULL, saved boolean NOT NULL DEFAULT false, created timestamptz DEFAULT clock_timestamp());
CREATE TABLE IF NOT EXISTS jobs(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), owner uuid NOT NULL REFERENCES users, kind text NOT NULL,
 args jsonb NOT NULL, key text, status text NOT NULL DEFAULT '대기', attempts int NOT NULL DEFAULT 0,
 lease timestamptz, result jsonb, error text, created timestamptz DEFAULT clock_timestamp(), updated timestamptz DEFAULT now(),
 UNIQUE(owner,key));
CREATE INDEX IF NOT EXISTS queue_idx ON jobs(status,created);
CREATE INDEX IF NOT EXISTS citation_passage_idx ON citations(passage);
CREATE INDEX IF NOT EXISTS facts_owner_idx ON facts(owner,experience,status);
CREATE INDEX IF NOT EXISTS docs_owner_idx ON documents(owner,app);
CREATE TABLE IF NOT EXISTS schema_revisions(version int PRIMARY KEY, installed timestamptz DEFAULT now());
ALTER TABLE tags ADD COLUMN IF NOT EXISTS proposed boolean NOT NULL DEFAULT false;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS previous uuid REFERENCES documents ON DELETE SET NULL;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS active boolean NOT NULL DEFAULT true;
CREATE TABLE IF NOT EXISTS heartbeat(name text PRIMARY KEY, seen timestamptz NOT NULL);
INSERT INTO schema_revisions(version) VALUES(1),(2) ON CONFLICT DO NOTHING;

