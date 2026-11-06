import json
import os
import shutil
import sqlite3
import uuid
from pathlib import Path


def clone_corpus(settings, output: Path, kind: str):
    """Backup a corpus read-only, strip histories, and republish its existing vectors."""
    if output.exists():
        raise ValueError("Isolation output already exists; choose a new directory")
    output.mkdir(parents=True, mode=0o700)
    source_path = settings.data_dir / "knowledge.sqlite3"
    source = sqlite3.connect(source_path.resolve().as_uri() + "?mode=ro", uri=True)
    target = sqlite3.connect(output / "knowledge.sqlite3")
    try:
        source_meta = dict(source.execute("SELECT key,value FROM meta"))
        source.backup(target)
        namespace = ("blka_" if kind == "knowledge" else "bliqb_") + uuid.uuid4().hex[:12]
        with target:
            tables = {r[0] for r in target.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ("turns", "sessions", "events", "question_records"):
                if table in tables:
                    target.execute(f"DELETE FROM {table}")
            target.execute("UPDATE meta SET value=? WHERE key='corpus_id'", (uuid.uuid4().hex,))
            target.execute("UPDATE meta SET value=? WHERE key='index_namespace'", (namespace,))
            metadata = dict(target.execute("SELECT key,value FROM meta"))
            rows = target.execute(
                "SELECT payload,vector FROM chunks JOIN active ON chunks.id=active.chunk_id"
            ).fetchall()
            sources = target.execute("SELECT coverage FROM sources").fetchall()
        (output / "snapshots").mkdir(mode=0o700)
        for (coverage,) in sources:
            snapshot = json.loads(coverage).get("snapshot")
            if snapshot:
                name = snapshot + ".json"
                shutil.copy2(settings.data_dir / "snapshots" / name, output / "snapshots" / name)
    finally:
        source.close()
        target.close()
    os.chmod(output / "knowledge.sqlite3", 0o600)
    isolated = settings.model_copy(update={
        "data_dir": output, "index_namespace": namespace,
        "model_cache_dir": settings.model_cache_dir or settings.data_dir / "models",
    })
    if kind == "knowledge":
        from knowledge_assistant.models import Chunk
        from knowledge_assistant.retrieval import Knowledge
        from knowledge_assistant.storage import Store
    else:
        from interview_question_bank.models import Chunk
        from interview_question_bank.retrieval import Knowledge
        from interview_question_bank.storage import Store
    store = Store(output)
    try:
        knowledge = Knowledge(isolated, store)
        chunks = [Chunk(**json.loads(r[0])) for r in rows]
        knowledge.index.write(chunks, [json.loads(r[1]) for r in rows])
        manifest = {
            "kind": kind, "source": str(source_path), "data_dir": str(output),
            "namespace": namespace, "chunks": len(chunks),
            "version": metadata.get("version"), "corpus_id": metadata.get("corpus_id"),
            "source_corpus_id": source_meta.get("corpus_id"),
            "source_version": source_meta.get("version"),
        }
        (output / "isolation.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        return manifest
    finally:
        store.db.close()
