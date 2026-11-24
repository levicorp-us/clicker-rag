ALTER TABLE passages ALTER COLUMN embedding TYPE vector;
ALTER TABLE facts ALTER COLUMN embedding TYPE vector;
CREATE TABLE model_settings (
    owner uuid PRIMARY KEY REFERENCES users,
    generation text NOT NULL, analysis text NOT NULL, embedding text NOT NULL,
    dimensions integer, embed_digest text, version integer NOT NULL DEFAULT 0
);
INSERT INTO schema_revisions(version) VALUES(7);
