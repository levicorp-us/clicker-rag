import hashlib
import hmac
import json
import os
import secrets
import unicodedata
import uuid
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.getenv("CLICKER_DATA", ROOT / ".runtime"))
FILES = DATA / "files"
FILES.mkdir(parents=True, exist_ok=True)
profile_path = ROOT / ".runtime/model-profile.json"
default_model = (
    json.loads(profile_path.read_text(encoding="utf-8-sig"))["model"]
    if profile_path.exists() and not os.getenv("CLICKER_OLLAMA_URL")
    else "qwen3.8:27b"
)
TAG_MODEL = os.getenv("CLICKER_TAG_MODEL", default_model)
GEN_MODEL = os.getenv("CLICKER_GEN_MODEL", default_model)
EMBED_MODEL = os.getenv("CLICKER_EMBED_MODEL", "bge-m3")
PROMPT_VERSION = "ko-3.2"


def connect():
    return psycopg.connect(
        host="127.0.0.1",
        port=55438,
        dbname=os.getenv("CLICKER_DB", "postgres"),
        user="clicker",
        password=(ROOT / ".runtime/db.secret").read_text().strip(),
        row_factory=dict_row,
    )


def migrate():
    with connect() as db:
        db.execute("SELECT pg_advisory_xact_lock(328621)")
        if db.execute("SELECT to_regclass('schema_revisions') AS present").fetchone()[
            "present"
        ]:
            if db.execute("SELECT 1 FROM schema_revisions WHERE version=7").fetchone():
                return
        else:
            db.execute((ROOT / "app/schema.sql").read_text(encoding="utf-8"))
        if not db.execute("SELECT 1 FROM schema_revisions WHERE version=4").fetchone():
            db.execute((ROOT / "app/rounds.sql").read_text(encoding="utf-8"))
            db.execute("UPDATE facts SET role='' WHERE status='제안' AND experience IS NULL AND role<>''")
            for f in db.execute("""UPDATE facts SET embedding=NULL,model=NULL
                WHERE status='확정' AND role<>'' AND embedding IS NOT NULL
                RETURNING id,owner,version""").fetchall():
                enqueue(db, f["owner"], "index_fact", {"id": str(f["id"]), "version": f["version"]},
                        f"activity-index:{f['id']}:{f['version']}:{EMBED_MODEL}")
            db.execute("INSERT INTO schema_revisions(version) VALUES(4)")
        db.execute("ALTER TABLE paragraphs ALTER COLUMN experience DROP NOT NULL")
        db.execute("INSERT INTO schema_revisions(version) VALUES(5) ON CONFLICT DO NOTHING")
        db.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS cleared boolean NOT NULL DEFAULT false")
        db.execute("INSERT INTO schema_revisions(version) VALUES(6) ON CONFLICT DO NOTHING")
        db.execute((ROOT / "app/model_settings.sql").read_text(encoding="utf-8-sig"))


def profile():
    path = DATA / "profile.json"
    if not path.exists():
        candidate = path.with_suffix("." + uuid.uuid4().hex)
        candidate.write_text(json.dumps([str(uuid.uuid4()), secrets.token_hex(32)]))
        try:
            os.link(candidate, path)
        except FileExistsError:
            pass
        finally:
            candidate.unlink()
    return json.loads(path.read_text())


def session_for(owner):
    secret = profile()[1].encode()
    return owner + "." + hmac.new(secret, owner.encode(), hashlib.sha256).hexdigest()


def owner_for(token):
    owner = (token or "").split(".")[0]
    if not token or not hmac.compare_digest(token, session_for(owner)):
        raise HTTPException(401, "작성 화면을 다시 열어 주세요.")
    return str(uuid.UUID(owner))


def one(db, sql, args=()):
    row = db.execute(sql, args).fetchone()
    if row is None:
        raise HTTPException(404, "자료를 찾을 수 없습니다.")
    return row


def enqueue(db, owner, kind, args, key=None):
    return one(
        db,
        """INSERT INTO jobs(owner,kind,args,key) VALUES(%s,%s,%s,%s)
        ON CONFLICT(owner,key) DO UPDATE SET key=excluded.key RETURNING id""",
        (owner, kind, Jsonb(args), key),
    )["id"]


def count_chars(body, spaces=True):
    body = unicodedata.normalize("NFC", body)
    return len(body if spaces else "".join(body.split()))


def add_tag(db, owner, name):
    return one(
        db,
        """INSERT INTO tags(owner,name) VALUES(%s,%s) ON CONFLICT(owner,name)
        DO UPDATE SET name=excluded.name RETURNING id""",
        (owner, name.strip()),
    )["id"]


def link(db, tag, kind, target, state):
    suffix = "DO NOTHING" if state == "제안" else "DO UPDATE SET state=excluded.state"
    db.execute(
        "INSERT INTO links VALUES(%s,%s,%s,%s) ON CONFLICT(tag,target_type,target) "
        + suffix,
        (tag, kind, target, state),
    )


def question(db, owner, qid):
    return one(
        db,
        """SELECT q.*,a.company,a.role FROM questions q JOIN applications a ON a.id=q.app
        WHERE a.owner=%s AND q.id=%s""",
        (owner, qid),
    )
