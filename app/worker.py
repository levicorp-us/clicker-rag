import logging
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import httpx
from psycopg.types.json import Jsonb
from . import pipeline
from .model_management import configure_models, install_model
from .model_settings import index_lock, load, using
from .research import research_company
from .report_jobs import analyze_template, report_tags, report_cell
from .core import connect, migrate
from .job_control import JobInterrupted, JobRun

TASKS = {
    fn.__name__: fn
    for fn in (
        install_model,
        configure_models,
        analyze_template,
        report_tags,
        report_cell,
        pipeline.parse,
        pipeline.company_tags,
        research_company,
        pipeline.classify,
        pipeline.extract,
        pipeline.index_doc,
        pipeline.index_fact,
        pipeline.retag,
        pipeline.generate,
    )
}
log = logging.getLogger("clicker.worker")


def run_one(job_id=None, management=None):
    with connect() as db:
        db.execute("""INSERT INTO heartbeat VALUES('worker',now()) ON CONFLICT(name)
            DO UPDATE SET seen=now() WHERE heartbeat.seen < now()-interval '15 seconds'""")
        job = db.execute(
            """WITH next AS (SELECT id FROM jobs WHERE (%s::uuid IS NULL OR id=%s)
            AND (%s::boolean IS NULL OR (kind IN ('install_model','configure_models'))=%s)
            AND (status='대기' OR (status='실행' AND lease<now())) ORDER BY CASE WHEN kind='generate' THEN 0 WHEN kind='parse' THEN 1 ELSE 2 END,
            created FOR UPDATE SKIP LOCKED LIMIT 1)
            UPDATE jobs SET status='실행',attempts=attempts+1,lease=now()+interval '90 seconds',updated=clock_timestamp()
            FROM next WHERE jobs.id=next.id RETURNING jobs.*""",
            (job_id, job_id, management, management),
        ).fetchone()
    if not job:
        return False
    start = time.monotonic()
    run = JobRun(job)
    try:
        with run.monitor(), connect() as db, ExitStack() as scope:
            if job["kind"] not in ("install_model", "configure_models"):
                if job["kind"] in ("index_doc", "index_fact", "parse"):
                    index_lock(db, job["owner"])
                scope.enter_context(using(load(db, job["owner"], " FOR SHARE")))
            if job["kind"] == "generate":
                result = pipeline.generate(
                    db, job["owner"], job["args"], job_id=job["id"]
                )
            else:
                result = TASKS[job["kind"]](db, job["owner"], job["args"])
            result["seconds"] = round(time.monotonic() - start, 2)
            run.lock_current(db)
            db.execute(
                "UPDATE jobs SET status='완료',error=null,lease=NULL,result=%s,updated=clock_timestamp() WHERE id=%s",
                (Jsonb(result), job["id"]),
            )
    except JobInterrupted:
        return True
    except Exception as exc:
        temporary = isinstance(exc, httpx.TransportError) and job["attempts"] < 2
        if isinstance(exc, httpx.HTTPStatusError):
            code = exc.response.status_code
            reason = f"모델 서버 응답 오류 ({code}). 설치 상태와 모델이 지원하는 기능을 확인하세요."
        elif isinstance(exc, httpx.TransportError):
            reason = "모델 서버와 통신하지 못했습니다. 연결 상태를 확인한 뒤 재시도해 주세요."
        elif isinstance(exc, ValueError) and exc.__class__ is ValueError:
            reason = str(exc)[:180]
        else:
            reason = f"{type(exc).__name__} · 원본 형식과 로컬 실행 상태를 확인하세요."
        with connect() as db:
            failed = db.execute(
                """UPDATE jobs SET status=%s,error=%s,lease=NULL,updated=clock_timestamp()
                   WHERE id=%s AND updated=%s AND status='실행' RETURNING id""",
                ("대기" if temporary else "실패", reason, *run.identity),
            ).fetchone()
            if failed and job["kind"] == "parse" and not temporary:
                db.execute(
                    "UPDATE documents SET status='파싱 실패' WHERE id=%s AND owner=%s",
                    (job["args"]["doc"], job["owner"]),
                )
        log.warning("job %s %s %s", job["id"], job["kind"], type(exc).__name__)
    return True


def serve(management):
    while True:
        if not run_one(management=management):
            time.sleep(0.7)


if __name__ == "__main__":
    migrate()
    with ThreadPoolExecutor(max_workers=1) as pool:
        manager = pool.submit(serve, True)
        while not manager.done():
            if not run_one(management=False):
                time.sleep(0.7)
        manager.result()
