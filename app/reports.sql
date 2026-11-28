CREATE TABLE report_templates (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), owner uuid NOT NULL REFERENCES users,
    name text NOT NULL, package bytea NOT NULL, fields jsonb NOT NULL DEFAULT '[]',
    tags text[] NOT NULL DEFAULT '{}', ready boolean NOT NULL DEFAULT false,
    source_doc uuid REFERENCES documents ON DELETE SET NULL, created timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE reports (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), owner uuid NOT NULL REFERENCES users,
    template uuid NOT NULL REFERENCES report_templates, title text NOT NULL, created timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE report_cells (
    report uuid NOT NULL REFERENCES reports ON DELETE CASCADE, key text NOT NULL,
    body text NOT NULL DEFAULT '', instruction text NOT NULL DEFAULT '', refs jsonb NOT NULL DEFAULT '[]',
    version integer NOT NULL DEFAULT 1, PRIMARY KEY(report,key)
);
INSERT INTO schema_revisions(version) VALUES(8);
