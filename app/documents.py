import hashlib
import os
import uuid
from pathlib import Path

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from .core import FILES, enqueue, one
from .imports import EXTENSIONS


def store_doc(
    db,
    owner,
    name,
    filename,
    data,
    kind,
    app_id,
    meta,
    analyze=True,
    previous=None,
    version=1,
):
    owner = str(owner)
    if app_id:
        one(db, "SELECT id FROM applications WHERE owner=%s AND id=%s", (owner, app_id))
    if Path(filename).suffix.lower() not in EXTENSIONS:
        raise HTTPException(422, "지원하지 않는 파일 형식입니다.")
    digest = hashlib.sha256(data).hexdigest()
    db.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (owner + digest,)
    )
    folder = FILES / owner
    folder.mkdir(exist_ok=True)
    dest = folder / digest
    if not dest.exists():
        tmp = folder / (uuid.uuid4().hex + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, dest)
    doc = one(
        db,
        """INSERT INTO documents(owner,app,name,kind,filename,digest,metadata,previous,version)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (owner, app_id, name, kind, filename, digest, Jsonb(meta), previous, version),
    )["id"]
    job = enqueue(
        db,
        owner,
        "parse",
        {"doc": str(doc), "version": version, "analyze": analyze},
        f"parse:{doc}:{version}",
    )
    return {"id": doc, "job": job}
