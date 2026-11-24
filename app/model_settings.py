"""Model choices for a single user's consistent retrieval/generation transaction."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from .core import EMBED_MODEL, GEN_MODEL, TAG_MODEL


@dataclass(frozen=True)
class Choices:
    generation: str = GEN_MODEL
    analysis: str = TAG_MODEL
    embedding: str = EMBED_MODEL
    dimensions: int | None = None
    embed_digest: str | None = None
    version: int = 0


current = ContextVar("model_choices", default=Choices())


@contextmanager
def using(choices):
    token = current.set(choices)
    try:
        yield
    finally:
        current.reset(token)


def load(db, owner, lock=""):
    query = "SELECT generation,analysis,embedding,dimensions,embed_digest,version FROM model_settings WHERE owner=%s"
    row = db.execute(query + lock, (owner,)).fetchone()
    if row is None:
        db.execute(
            """INSERT INTO model_settings(owner,generation,analysis,embedding)
            VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
            (owner, GEN_MODEL, TAG_MODEL, EMBED_MODEL),
        )
        row = db.execute(query + lock, (owner,)).fetchone()
    if row["dimensions"] is None:
        # Existing databases already have searchable vectors; preserve their space on first use.
        found = db.execute(
            """SELECT vector_dims(p.embedding) AS n FROM passages p JOIN documents d ON d.id=p.doc
            WHERE d.owner=%s AND d.active AND p.model=%s AND p.embedding IS NOT NULL
            UNION ALL SELECT vector_dims(embedding) FROM facts WHERE owner=%s AND model=%s AND embedding IS NOT NULL
            LIMIT 1""",
            (owner, row["embedding"], owner, row["embedding"]),
        ).fetchone()
        if found:
            row["dimensions"] = found["n"]
    return Choices(**row)


def index_lock(db, owner, exclusive=False):
    fn = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
    db.execute(f"SELECT {fn}(hashtextextended(%s,0))", ("model-index:" + str(owner),))
