"""Real CLI acceptance and RAGAs evaluation against an isolated copy of the corpus."""

import asyncio
import importlib.metadata
import json
import math
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from .config import ROOT
from .models import InterviewQuestion, digest
from .retrieval import Knowledge
from .storage import Store


def write_json(path, value):
    Path(path).write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8",
    )


def load_cases(path):
    cases = json.loads(Path(path).read_text())
    if not isinstance(cases, list) or not cases:
        raise ValueError("Evaluation dataset must be a non-empty list.")
    seen = set()
    for case in cases:
        if (
            not isinstance(case, dict)
            or not isinstance(case.get("id"), str)
            or not case["id"].isascii()
            or not case["id"].replace("-", "").replace("_", "").isalnum()
            or case["id"] in seen
            or not case.get("question")
            or not case.get("reference")
            or not case.get("evidence")
        ):
            raise ValueError("Invalid evaluation case or duplicate ID.")
        seen.add(case["id"])
    return cases


def gold_ids(case, chunks):
    found = set()
    for evidence in case["evidence"]:
        matches = {c.id for c in chunks
                   if c.path == evidence["path"] and evidence["quote"] in c.text}
        if not matches:
            raise ValueError(f"Reference evidence missing for case {case['id']}.")
        found.update(matches)
    return found


def retrieval_metrics(ids, gold):
    return {
        "gold_chunk_recall": len(set(ids) & gold) / len(gold),
        "reciprocal_rank": next((1 / i for i, cid in enumerate(ids, 1) if cid in gold), 0),
    }


def isolate(settings, output, cases):
    source = Store(settings.data_dir)
    chunks = source.active_chunks()
    if not chunks:
        raise ValueError("Import real source documents before evaluation.")
    for case in cases:
        gold_ids(case, chunks)
    target = Store(output / "knowledge")
    source.db.backup(target.db)
    namespace = "iqbeval_" + uuid.uuid4().hex[:12]
    with target.db:
        for table in ("turns", "sessions", "events", "question_records"):
            target.db.execute(f"DELETE FROM {table}")
        target.db.execute("UPDATE meta SET value=? WHERE key='corpus_id'", (uuid.uuid4().hex,))
        target.db.execute("UPDATE meta SET value=? WHERE key='index_namespace'", (namespace,))
    for row in target.sources():
        snapshot = json.loads(row["coverage"])["snapshot"] + ".json"
        shutil.copy2(settings.data_dir / "snapshots" / snapshot,
                     target.root / "snapshots" / snapshot)
    isolated = settings.model_copy(update={
        "data_dir": target.root, "index_namespace": namespace,
        "model_cache_dir": settings.model_cache_dir or settings.data_dir / "models",
    })
    knowledge = Knowledge(isolated, target)
    vectors = {
        row["id"]: json.loads(row["vector"])
        for row in target.db.execute("SELECT id,vector FROM chunks")
    }
    knowledge.index.write(chunks, [vectors[c.id] for c in chunks])
    manifest = {
        "model": settings.llm_model, "base_url": settings.llm_base_url,
        "embedding_model": settings.embedding_model,
        "retrieval_settings": isolated.retrieval_settings(),
        "data_dir": str(isolated.data_dir), "index_namespace": namespace,
        "model_cache_dir": str(isolated.model_cache_dir),
        "source_corpus_id": source.corpus_id, "source_version": source.version,
        "source_chunks": len(chunks), "collection": knowledge.index._collection,
        "dataset_hash": digest(cases), "started_at": time.time(),
    }
    write_json(output / "environment.json", manifest)
    write_json(output / "dataset.json", cases)
    return isolated


def cli_environment(settings):
    return dict(
        os.environ, DATA_DIR=str(settings.data_dir), INDEX_NAMESPACE=settings.index_namespace,
        MODEL_CACHE_DIR=str(settings.model_cache_dir), LLM_MODEL=settings.llm_model,
        RERANKER_ENABLED=str(settings.reranker_enabled).lower(),
        RERANKER_MODEL=settings.reranker_model,
        RETRIEVAL_CANDIDATES=str(settings.retrieval_candidates),
        RERANKER_BATCH_SIZE=str(settings.reranker_batch_size),
    )


def cli_answer(settings, output, label, question, *, session=None, cache_probe=False):
    command = [str(ROOT / ".venv/bin/iqb"), "chat", "-q", question, "--local-only", "--json"]
    if session:
        command += ["--session", session]
    env = cli_environment(settings)
    if cache_probe:
        env.update(LLM_API_KEY="", MILVUS_URI="http://127.0.0.1:1")
    started = time.monotonic()
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=1200)
    (output / f"{label}.stdout").write_text(result.stdout)
    (output / f"{label}.stderr").write_text(result.stderr)
    with (output / "commands.jsonl").open("a") as stream:
        stream.write(json.dumps({
            "label": label, "command": command, "exit_code": result.returncode,
            "seconds": round(time.monotonic() - started, 3), "cache_probe": cache_probe,
        }, ensure_ascii=False) + "\n")
    if result.returncode:
        raise RuntimeError(f"CLI failed; see {label}.stderr")
    return json.loads(result.stdout)


def validate_answer(answer, expected_count=1, *, explanation=False):
    if not (answer["sufficient"] and answer["persisted"] and answer["citations"]):
        raise ValueError("Real answer is insufficient, unsaved, or has no citations.")
    cited = {c["id"] for c in answer["citations"]}
    trace = answer.get("retrieval", {})
    if answer["route"] != "cache":
        if not (trace.get("reranker_enabled") and trace.get("scores")
                and trace.get("dense_ids") and trace.get("lexical_ids")):
            raise ValueError("Missing real hybrid retrieval and reranker trace.")
        if not cited <= set(trace["context_ids"]):
            raise ValueError("Citations do not belong to actual generation contexts.")
    if explanation:
        if not answer["explanation"] or answer["questions"]:
            raise ValueError("Context follow-up did not produce a grounded explanation.")
    else:
        if len(answer["questions"]) != expected_count:
            raise ValueError("Unexpected question count.")
        for question in answer["questions"]:
            item = InterviewQuestion.model_validate(question)
            if not set(item.evidence_ids) <= cited:
                raise ValueError("Invalid question citations.")
    if expected_count == 5 and not explanation:
        from collections import Counter

        if Counter(q["difficulty"] for q in answer["questions"]) != {
            "基础": 2, "进阶": 2, "高阶": 1,
        }:
            raise ValueError("Default difficulty distribution is incorrect.")


def run_cases(settings, output, cases, exercises):
    store = Store(settings.data_dir)
    by_id = {c.id: c for c in store.active_chunks()}
    baseline = Knowledge(settings.model_copy(update={"reranker_enabled": False}), store)
    rows = []
    write_json(output / "samples.json", rows)
    for case in cases:
        row = {"id": case["id"], "question": case["question"], "reference": case["reference"]}
        try:
            answer = cli_answer(settings, output, case["id"], case["question"])
            validate_answer(answer)
            if answer["route"] != "local":
                raise ValueError("Evaluation must execute real retrieval, not a cache hit.")
            trace = answer["retrieval"]
            original = baseline.retrieve(
                answer["request"]["query"], k=trace["k"], rerank_query=case["question"],
            )
            if baseline.last_trace["fused_ids"] != trace["fused_ids"]:
                raise ValueError("Baseline and reranker candidate pools differ.")
            gold = gold_ids(case, list(by_id.values()))
            row.update(
                status="passed", answer=answer,
                response="\n".join(
                    f"{q['title']}\n{q['answer']}\n" + "\n".join(q["key_points"])
                    for q in answer["questions"]
                ),
                retrieved_contexts=[by_id[cid].text for cid in trace["context_ids"]],
                baseline=retrieval_metrics([c.id for c in original], gold),
                reranked=retrieval_metrics(trace["final_ids"], gold),
            )
        except Exception as exc:
            row.update(status="failed", error_type=type(exc).__name__)
        rows.append(row)
        write_json(output / "samples.json", rows)
        print(f"E2E {case['id']}: {row['status']}", flush=True)

    checks = []
    if exercises:
        try:
            for count in (2, 3):
                answer = cli_answer(settings, output, f"hot-{count}", cases[0]["question"])
                validate_answer(answer)
                if answer["hot_count"] != count or answer["route"] != "local":
                    raise ValueError("Hot counter did not advance after sufficient real answer.")
            hit = cli_answer(settings, output, "hot-4", cases[0]["question"], cache_probe=True)
            validate_answer(hit)
            if hit["route"] != "cache" or hit["hot_count"] != 4:
                raise ValueError("Fourth request did not hit Redis without model/Milvus.")
            checks.append({"name": "real_hot_cache", "status": "passed"})
        except Exception as exc:
            checks.append({"name": "real_hot_cache", "status": "failed",
                           "error_type": type(exc).__name__})
        try:
            answer = cli_answer(
                settings, output, "default-five",
                "给我 ReAct 和 Agent 工具调用相关的面试题，仅围绕循环执行、终止条件和工具治理。",
            )
            validate_answer(answer, 5)
            follow = cli_answer(settings, output, "followup", "解释第二题",
                                session=answer["session"])
            validate_answer(follow, explanation=True)
            if follow["request"]["target_number"] != 2:
                raise ValueError("Follow-up lost the question number.")
            checks.append({"name": "default_five_and_context", "status": "passed"})
        except Exception as exc:
            checks.append({"name": "default_five_and_context", "status": "failed",
                           "error_type": type(exc).__name__})
    write_json(output / "checks.json", checks)


def local_ragas_embeddings(embeddings):
    from ragas.embeddings.base import BaseRagasEmbedding

    class LocalMPNet(BaseRagasEmbedding):
        def embed_text(self, text, **kwargs):
            return embeddings.query(text)

        async def aembed_text(self, text, **kwargs):
            return await asyncio.to_thread(self.embed_text, text)

        async def aembed_texts(self, texts, **kwargs):
            # Avoid concurrent ONNX model initialization on the first batch.
            return await asyncio.to_thread(lambda: [self.embed_text(t) for t in texts])

    return LocalMPNet()


async def score_samples(settings, output):
    os.environ.setdefault("RAGAS_DO_NOT_TRACK", "true")
    from openai import AsyncOpenAI
    from ragas.llms import llm_factory
    from ragas.metrics.collections import (
        AnswerRelevancy,
        ContextPrecision,
        ContextRecall,
        Faithfulness,
    )

    from .retrieval import Embeddings

    client = AsyncOpenAI(
        api_key=settings.llm_api_key.get_secret_value(),
        base_url=settings.llm_base_url or None, timeout=settings.llm_timeout, max_retries=1,
    )
    llm = llm_factory(
        # Faithfulness emits a verdict and reason for every extracted statement.
        settings.llm_model, client=client, temperature=0, max_tokens=16384,
    )
    original = llm.agenerate
    attempt = uuid.uuid4().hex[:8]
    audit = output / f"ragas-calls-{attempt}.jsonl"

    async def recorded(prompt, response_model, **kwargs):
        started = time.monotonic()
        event = {"prompt": prompt, "schema": response_model.__name__, "model": settings.llm_model}
        try:
            value = await original(prompt, response_model, **kwargs)
            event.update(status="passed", response=value.model_dump())
            return value
        except BaseException as exc:
            event.update(status="failed", error_type=type(exc).__name__)
            raise
        finally:
            event["seconds"] = round(time.monotonic() - started, 3)
            with audit.open("a") as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")

    llm.agenerate = recorded
    metrics = {
        "faithfulness": Faithfulness(llm=llm),
        "answer_relevancy": AnswerRelevancy(
            llm=llm, embeddings=local_ragas_embeddings(Embeddings(settings)),
        ),
        "context_precision": ContextPrecision(llm=llm),
        "context_recall": ContextRecall(llm=llm),
    }
    rows = json.loads((output / "samples.json").read_text())
    scores = []
    semaphore = asyncio.Semaphore(2)

    async def score(row, name, metric):
        async with semaphore:
            record = {"id": row["id"], "metric": name}
            payload = {
                "user_input": row["question"], "response": row["response"],
                "retrieved_contexts": row["retrieved_contexts"], "reference": row["reference"],
            }
            keys = {
                "faithfulness": ("user_input", "response", "retrieved_contexts"),
                "answer_relevancy": ("user_input", "response"),
                "context_precision": ("user_input", "reference", "retrieved_contexts"),
                "context_recall": ("user_input", "reference", "retrieved_contexts"),
            }[name]
            try:
                value = await asyncio.wait_for(
                    metric.ascore(**{key: payload[key] for key in keys}), timeout=900,
                )
                if not math.isfinite(value.value):
                    raise ValueError("Non-finite RAGAs result")
                record.update(status="passed", value=float(value.value))
            except Exception as exc:
                record.update(status="failed", value=None, error_type=type(exc).__name__)
            scores.append(record)
            write_json(output / f"ragas-scores-{attempt}.json", scores)
            print(f"RAGAs {row['id']} {name}: {record.get('value')} ({record['status']})",
                  flush=True)

    try:
        await asyncio.gather(*[
            score(row, name, metric) for row in rows if row["status"] == "passed"
            for name, metric in metrics.items()
        ])
    finally:
        await client.close()
    means = {
        name: sum(r["value"] for r in scores
                  if r["metric"] == name and r["status"] == "passed") / len(valid)
        for name in metrics
        if (valid := [r for r in scores if r["metric"] == name and r["status"] == "passed"])
    }
    checks = json.loads((output / "checks.json").read_text())
    report = {
        "ragas_version": importlib.metadata.version("ragas"),
        "judge_model": settings.llm_model, "attempt": attempt,
        "e2e_passed": sum(r["status"] == "passed" for r in rows), "e2e_total": len(rows),
        "checks": checks, "metrics": scores, "means_over_successful_samples": means,
        "metric_expected": len(rows) * len(metrics),
        "metric_passed": sum(s["status"] == "passed" for s in scores),
        "complete": (
            all(r["status"] == "passed" for r in rows)
            and all(c["status"] == "passed" for c in checks)
            and len(scores) == len(rows) * len(metrics)
            and all(s["status"] == "passed" for s in scores)
        ),
        "limitations": [
            "Small curated dataset; no claim of statistical significance.",
            "Generator and judge use the same GLM model; scores may share model bias.",
            "Retrieval gold labels cover specific source chunks, not all equivalent sources.",
            "Real local-corpus E2E; this run does not retest external web collection.",
        ],
    }
    write_json(output / f"report-{attempt}.json", report)
    write_json(output / "report.json", report)
    return report


def evaluate(settings, dataset, output, *, score_only=False, exercises=True):
    try:
        importlib.metadata.version("ragas")
    except importlib.metadata.PackageNotFoundError as exc:
        raise ValueError("Install evaluation dependencies: uv sync --extra eval --extra test") from exc
    output = Path(output).resolve()
    if score_only:
        manifest = json.loads((output / "environment.json").read_text())
        if settings.llm_model != manifest["model"]:
            raise ValueError("Use the same configured model as the original E2E run.")
        settings = settings.model_copy(update={
            "data_dir": Path(manifest["data_dir"]),
            "index_namespace": manifest["index_namespace"],
            "model_cache_dir": Path(manifest["model_cache_dir"]),
        })
    else:
        cases = load_cases(dataset)
        output.mkdir(parents=True, exist_ok=False)
        settings = isolate(settings, output, cases)
        run_cases(settings, output, cases, exercises)
    return asyncio.run(score_samples(settings, output))
