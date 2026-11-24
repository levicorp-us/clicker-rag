CREATE TABLE IF NOT EXISTS generation_rounds(
  job uuid NOT NULL REFERENCES jobs ON DELETE CASCADE,
  owner uuid NOT NULL REFERENCES users,
  input_hash text NOT NULL,
  round integer NOT NULL CHECK(round BETWEEN 1 AND 3),
  draft jsonb NOT NULL,
  assessment jsonb,
  created timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(job,input_hash,round)
);
INSERT INTO schema_revisions(version) VALUES(3) ON CONFLICT DO NOTHING;
