"""Curate source questions and fill explicit curriculum gaps without indexing answers as facts."""

import json
import math
import re
import time
import unicodedata
from collections import Counter, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from markdown_it import MarkdownIt

from .config import ROOT
from .llm import Models
from .models import Chunk, InterviewQuestion, InterviewRequest, digest
from .retrieval import Embeddings, Knowledge, MilvusIndex
from .storage import Store

PLAN = ROOT / "config/ai_agent_curriculum.json"


def title_key(text):
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"^(?:q\s*\d+|第[一二三四五六七八九十\d]+题)[.、:：\s]*", "", text)
    text = re.sub(r"^(?:请简述|请解释|请说明|请描述|请问|简述|请)", "", text)
    return "".join(c for c in text if c.isalnum())


def load_plan(path=PLAN, target=1000):
    data = json.loads(Path(path).read_text())
    topics = data["topics"]
    if not topics or len({t["id"] for t in topics}) != len(topics):
        raise ValueError("Curriculum topic IDs must be unique.")
    if any(t["count"] <= 0 or not t["keywords"] or not t["facets"] for t in topics):
        raise ValueError("Curriculum counts and evidence queries must be non-empty.")
    if not 1 <= target <= 10000 or not 0.5 <= data["semantic_duplicate_threshold"] <= 1:
        raise ValueError("Invalid target or semantic duplicate threshold.")
    total = sum(t["count"] for t in topics)
    exact = [t["count"] * target / total for t in topics]
    quotas = [math.floor(n) for n in exact]
    for i in sorted(range(len(topics)), key=lambda i: exact[i] - quotas[i], reverse=True)[
        :target - sum(quotas)
    ]:
        quotas[i] += 1
    for topic, quota in zip(topics, quotas, strict=True):
        topic["quota"] = quota
    return data


def classify(title, parent, answer, topics):
    for topic in topics:
        if any(pattern.lower() in title.lower() for pattern in topic.get("title_patterns", [])):
            return topic
    fields = ((title.lower(), 8), (parent.lower(), 3), (answer[:400].lower(), 1))
    scored = []
    for topic in topics:
        score = 0
        for word in topic["keywords"]:
            pattern = re.escape(word.lower())
            if word.isascii():
                pattern = r"(?<![a-z0-9])" + pattern + r"(?![a-z0-9])"
            score += sum(weight for text, weight in fields if re.search(pattern, text))
        scored.append((score, topic))
    score, topic = max(scored, key=lambda pair: pair[0])
    return topic if score else None


def difficulty(title):
    if re.search(r"设计|优化|故障|生产|分布式|一致性|权衡|治理|调优|高并发|攻击|漂移", title, re.I):
        return "高阶"
    if re.search(r"如何|怎么|为什么|区别|比较|选择|实现|流程|机制|原理|边界", title, re.I):
        return "进阶"
    return "基础"


def key_points(answer):
    points = []
    for token in MarkdownIt().parse(answer):
        if token.type == "inline" and token.content and not token.content.startswith("#"):
            text = token.content.strip()
            # Retain complete original sentences, including their qualifications.
            sentence = re.split(r"(?<=[。！？])\s*", text)[0]
            if 12 <= len(sentence) <= 350 and sentence not in points:
                points.append(sentence)
            if len(points) == 4:
                break
    return points or [answer]


def nested_answer(answer):
    lines = answer.splitlines(keepends=True)
    tokens = MarkdownIt().parse(answer)
    for i in reversed(range(len(tokens))):
        token = tokens[i]
        if token.type == "heading_open":
            start, end = token.map
            level = min(6, int(token.tag[1:]) + 2)
            lines[start:end] = ["#" * level + " " + tokens[i + 1].content + "\n"]
    return "".join(lines)


@dataclass
class Candidate:
    question: InterviewQuestion
    chunks: list[Chunk]
    topic: dict
    document: str
    review: str


def extract_candidates(store, plan):
    grouped = defaultdict(list)
    for chunk in store.active_chunks():
        grouped[(chunk.source, chunk.path)].append(chunk)
    result, seen = [], set()
    for source in store.sources():
        snapshot = json.loads(source["coverage"])["snapshot"]
        documents = json.loads((store.root / "snapshots" / f"{snapshot}.json").read_text())
        for doc in documents:
            if doc["path"] == "LICENSE":
                continue
            chunks = grouped[(doc["source"], doc["path"])]
            lines = doc["text"].splitlines(keepends=True)
            tokens = MarkdownIt().parse(doc["text"])
            headings = [
                (token.map[0], token.map[1], int(token.tag[1:]), tokens[i + 1].content)
                for i, token in enumerate(tokens) if token.type == "heading_open"
            ]
            parent = headings[0][3].lstrip("# ").strip() if headings else ""

            def add(quote, start, end, answer, *, followup=False):
                quote, answer = quote.strip(), answer.strip()
                if not 6 <= len(quote) <= 200 or not 70 <= len(answer) <= 6000:
                    return
                support = [c for c in chunks if c.start <= end and c.end >= start]
                if not support or not any(quote in c.text for c in support):
                    return
                title = quote.lstrip("# ")
                if followup and parent and title not in parent:
                    title = parent.rstrip("？?") + "——" + title
                topic = classify(title, parent, answer, plan["topics"])
                key = title_key(title)
                if topic is None or key in seen:
                    return
                seen.add(key)
                result.append(Candidate(
                    InterviewQuestion(
                        title=title, difficulty=difficulty(title),
                        knowledge_points=[topic["name"]], answer=answer,
                        key_points=key_points(answer), origin="来源原题",
                        evidence_ids=[c.id for c in support], source_question_quote=quote,
                    ),
                    support, topic, doc["source"] + "|" + doc["path"], "source_exact",
                ))

            for i, (start, after, level, heading) in enumerate(headings):
                if not re.search(r"[？?]|^(?:#+\s*)?Q\s*\d+\b", heading, re.I):
                    continue
                end = next((h[0] for h in headings[i + 1:] if h[2] <= level), len(lines))
                if "agentalpha.top/" in doc["source"] and level == 1:
                    end = next((h[0] for h in headings[i + 1:]
                                if any(s in h[3] for s in ("追问", "回答的坑"))), end)
                answer = "".join(lines[after:end]).strip()
                add(heading, start + 1, end, answer)
            for n, line in enumerate(lines):
                match = re.match(r"^\s*[-*]\s+\*\*(.+?[？?])\*\*[：:\s]*(.+?)\s*$", line)
                if match:
                    add(match[1], n + 1, n + 1, match[2], followup=True)
    return result


class QuestionBank:
    def __init__(self, settings, *, store=None, embeddings=None, index=None):
        self.s = settings
        self.store = store or Store(settings.data_dir)
        self.embeddings = embeddings or Embeddings(settings)
        self.index = index or MilvusIndex(
            settings.model_copy(update={"index_namespace": settings.index_namespace + "_questions"}),
            self.embeddings,
        )
        self.store.db.executescript("""
            CREATE TABLE IF NOT EXISTS question_catalog(
                question_id TEXT PRIMARY KEY REFERENCES question_records(id),
                title_key TEXT NOT NULL UNIQUE, topic TEXT NOT NULL, topic_name TEXT NOT NULL,
                hot INTEGER NOT NULL, review TEXT NOT NULL, vector TEXT NOT NULL,
                embedding_model TEXT NOT NULL, plan_version TEXT NOT NULL
            );
        """)
        registered = {r[0] for r in self.store.db.execute(
            "SELECT DISTINCT embedding_model FROM question_catalog"
        )}
        if registered and registered != {settings.embedding_model}:
            raise ValueError("Question bank embedding changed; rebuild in a new DATA_DIR.")
        self.keys = {
            title_key(json.loads(r[0])["title"])
            for r in self.store.db.execute("SELECT payload FROM question_records")
        }
        self.vectors = [
            json.loads(r[0]) for r in self.store.db.execute(
                "SELECT vector FROM question_catalog ORDER BY question_id"
            )
        ]
        self.counts = Counter(dict(self.store.db.execute(
            "SELECT topic,COUNT(*) FROM question_catalog GROUP BY topic"
        )))
        self.rejected = Counter()

    def adopt_topics(self, plan):
        changes = []
        counts = self.counts.copy()
        previous_plan_ids = {r[0] for r in self.store.db.execute(
            "SELECT question_id FROM question_catalog WHERE plan_version<>?", (plan["version"],),
        )}
        for row in self.rows():
            if row["id"] not in previous_plan_ids:
                continue
            topic = next((t for t in plan["topics"] if any(
                pattern.lower() in row["title"].lower()
                for pattern in t.get("title_patterns", [])
            )), None)
            if topic and topic["id"] != row["topic"]:
                counts[row["topic"]] -= 1
                counts[topic["id"]] += 1
                changes.append((row["id"], topic))
        if any(counts[t["id"]] > t["quota"] for t in plan["topics"]):
            raise ValueError("New quota is below saved topic counts; no questions were removed.")
        with self.store.db:
            for qid, topic in changes:
                payload = json.loads(self.store.db.execute(
                    "SELECT payload FROM question_records WHERE id=?", (qid,),
                ).fetchone()[0])
                payload["knowledge_points"] = list(dict.fromkeys(
                    payload["knowledge_points"] + [topic["name"]]
                ))
                self.store.db.execute(
                    "UPDATE question_records SET payload=? WHERE id=?",
                    (json.dumps(payload, ensure_ascii=False), qid),
                )
                self.store.db.execute("""
                    UPDATE question_catalog SET topic=?,topic_name=?,hot=?,plan_version=?
                    WHERE question_id=?
                """, (topic["id"], topic["name"], int(topic["hot"]), plan["version"], qid))
            self.store.db.execute(
                "UPDATE question_catalog SET plan_version=? WHERE plan_version<>?",
                (plan["version"], plan["version"]),
            )
        self.counts = counts
        return len(changes)

    def add(self, candidates, plan, *, vectors=None):
        if not candidates:
            return 0
        vectors = vectors if vectors is not None else self.embeddings.documents(
            [c.question.title for c in candidates]
        )
        if len(vectors) != len(candidates):
            raise ValueError("Question embedding count mismatch.")
        accepted, pending, pending_keys, increments = [], [], set(), Counter()
        for candidate, vector in zip(candidates, vectors, strict=True):
            q, topic = candidate.question, candidate.topic
            vector = np.asarray(vector, dtype=np.float64)
            if (vector.ndim != 1 or not np.isfinite(vector).all()
                    or np.linalg.norm(vector) <= 0):
                raise ValueError("Invalid question embedding.")
            vector = (vector / np.linalg.norm(vector)).tolist()
            key = title_key(q.title)
            if self.counts[topic["id"]] + increments[topic["id"]] >= topic["quota"]:
                continue
            if key in self.keys or key in pending_keys:
                self.rejected["title_duplicate"] += 1
                continue
            known = self.vectors + pending
            if known and np.max(np.asarray(known) @ vector) >= plan["semantic_duplicate_threshold"]:
                self.rejected["semantic_duplicate"] += 1
                continue
            by_id = {c.id: c for c in candidate.chunks}
            if not set(q.evidence_ids) <= by_id.keys():
                raise ValueError("Question has unsupported evidence IDs.")
            if q.origin == "来源原题" and (
                not q.source_question_quote
                or not any(q.source_question_quote in by_id[cid].text for cid in q.evidence_ids)
            ):
                raise ValueError("Original source question quote is invalid.")
            qid = digest(["curated-question-v1", key])
            accepted.append((qid, candidate, vector, key))
            pending.append(vector)
            pending_keys.add(key)
            increments[topic["id"]] += 1
        # Derived questions have their own collection and never enter the factual RAG index.
        derived = [
            Chunk(qid, "derived://question-bank", c.question.title, c.chunks[0].url,
                  "derived_questions", 0, 0, time.time(), c.chunks[0].expires_at)
            for qid, c, _, _ in accepted
        ]
        if not accepted:
            return 0
        self.index.write(derived, [v for _, _, v, _ in accepted])
        with self.store.db:
            for qid, c, vector, key in accepted:
                by_id = {chunk.id: chunk for chunk in c.chunks}
                citations = [
                    dict(by_id[cid].to_dict(), number=i)
                    for i, cid in enumerate(dict.fromkeys(c.question.evidence_ids), 1)
                ]
                self.store.db.execute("INSERT INTO question_records VALUES(?,?,?,?,?)", (
                    qid, c.question.model_dump_json(), json.dumps(citations, ensure_ascii=False),
                    self.store.version, time.time(),
                ))
                self.store.db.execute("INSERT INTO question_catalog VALUES(?,?,?,?,?,?,?,?,?)", (
                    qid, key, c.topic["id"], c.topic["name"], int(c.topic["hot"]), c.review,
                    json.dumps(vector), self.s.embedding_model, plan["version"],
                ))
        self.keys.update(pending_keys)
        self.vectors.extend(pending)
        self.counts.update(increments)
        return len(accepted)

    def rows(self, topic=None, difficulty_filter=None):
        active = {c.id for c in self.store.active_chunks()}
        rows = []
        for row in self.store.db.execute("""
            SELECT r.id,r.payload,r.citations,c.topic,c.topic_name,c.hot,c.review
            FROM question_records r JOIN question_catalog c ON c.question_id=r.id
            ORDER BY c.topic,r.id
        """):
            question = json.loads(row[1])
            if (topic and topic not in (row[3], row[4])) or (
                difficulty_filter and difficulty_filter != question["difficulty"]
            ):
                continue
            rows.append(dict(
                id=row[0], **question, citations=json.loads(row[2]), topic=row[3],
                topic_name=row[4], hot=bool(row[5]), review=row[6],
                citations_active=set(question["evidence_ids"]) <= active,
            ))
        return rows

    def stats(self):
        rows = self.rows()
        return dict(
            total=len(rows), hot=sum(r["hot"] for r in rows),
            citations_active=sum(r["citations_active"] for r in rows),
            topics=dict(Counter(r["topic_name"] for r in rows)),
            difficulty=dict(Counter(r["difficulty"] for r in rows)),
            origin=dict(Counter(r["origin"] for r in rows)),
            review=dict(Counter(r["review"] for r in rows)),
        )

    def search(self, query, limit=5, topic=None):
        rows = {r["id"]: r for r in self.rows(topic) if r["citations_active"]}
        if not rows:
            return []
        ids = self.index.search(self.embeddings.query(query), list(rows), limit)
        return [rows[qid] for qid in ids]

    def export(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = self.rows()
        if path.suffix == ".jsonl":
            path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        elif path.suffix == ".md":
            parts = ["# AI Agent 面试题库\n", "题目与答案整理自引用资料；第三方公司标签未独立核实。\n"]
            for i, row in enumerate(rows, 1):
                parts.extend([
                    f"## {i}. {row['title']}\n",
                    f"分类：{row['topic_name']} | 难度：{row['difficulty']} | {row['origin']}\n",
                    nested_answer(row["answer"]) + "\n",
                    "回答要点：\n" + "\n".join(
                        "- " + nested_answer(p).strip().replace("\n", "\n  ")
                        for p in row["key_points"]
                    ) + "\n",
                    "来源：\n" + "\n".join(
                        "- " + url for url in dict.fromkeys(c["url"] for c in row["citations"])
                    ) + "\n",
                ])
            path.write_text("\n".join(parts))
        else:
            raise ValueError("Export path must end with .jsonl or .md.")
        return len(rows)


def generate_batch(settings, request, chunks):
    started = time.monotonic()
    models = Models(settings)
    draft = models.draft(request, chunks, [])
    verification = models.verify(request, draft, chunks)
    supported = [
        q for i, q in enumerate(draft.questions, 1)
        if i in verification.supported_question_numbers
        and set(q.evidence_ids) <= {c.id for c in chunks}
        and (q.origin != "来源原题" or (
            q.source_question_quote and any(
                q.source_question_quote in c.text for c in chunks if c.id in q.evidence_ids
            )
        ))
    ]
    return dict(questions=supported, draft_count=len(draft.questions),
                verification=verification.model_dump(), seconds=time.monotonic() - started)


def build_bank(settings, output, *, target=1000, workers=3, plan_path=PLAN, rounds=30):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    plan = load_plan(plan_path, target)
    bank = QuestionBank(settings)
    if len(bank.vectors) > target:
        raise ValueError("Existing bank exceeds target; no questions were deleted.")
    report = dict(
        model=settings.llm_model, plan=plan, started_at=time.time(),
        source_version=bank.store.version, source_count=len(bank.store.sources()),
        native_candidates=0, generation_batches=0, errors=[], reclassified=0,
        generation_log=[],
    )
    previous_path = output / "report.json"
    if previous_path.exists():
        previous = json.loads(previous_path.read_text())
        if previous.get("plan") != plan or previous.get("model") != settings.llm_model:
            raise ValueError("Existing build report uses another plan or model; use a new output.")
        report = previous
        report.setdefault("resumes", []).append(dict(
            at=time.time(), source_version=bank.store.version, total=len(bank.vectors),
        ))
        bank.rejected.update(previous.get("rejected", {}))
    report["reclassified"] = report.get("reclassified", 0) + bank.adopt_topics(plan)

    def checkpoint():
        report.update(stats=bank.stats(), rejected=dict(bank.rejected),
                      question_collection=bank.index._collection or report.get("question_collection"),
                      updated_at=time.time())
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(dict(total=report["stats"]["total"], topics=dict(bank.counts),
                              generated_batches=report["generation_batches"]), ensure_ascii=False),
              flush=True)

    if all(bank.counts[t["id"]] == t["quota"] for t in plan["topics"]):
        report["complete"] = True
        bank.export(output / "questions.jsonl")
        bank.export(output / "questions.md")
        checkpoint()
        return report

    candidates = extract_candidates(bank.store, plan)
    report["native_candidates"] = len(candidates)
    # Interleave source documents rather than letting a long README monopolize the bank.
    buckets = defaultdict(deque)
    for candidate in candidates:
        buckets[candidate.document].append(candidate)
    ordered = []
    while buckets:
        for key in list(buckets):
            ordered.append(buckets[key].popleft())
            if not buckets[key]:
                del buckets[key]
    checkpoint()
    for start in range(0, len(ordered), 24):
        batch = [c for c in ordered[start:start + 24]
                 if bank.counts[c.topic["id"]] < c.topic["quota"]
                 and title_key(c.question.title) not in bank.keys]
        if batch:
            bank.add(batch, plan)
            checkpoint()
    knowledge, pools = Knowledge(settings, bank.store, embeddings=bank.embeddings), {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for round_index in range(rounds):
            missing = [t for t in plan["topics"] if bank.counts[t["id"]] < t["quota"]]
            if not missing:
                break
            futures = {}

            def consume(future):
                topic, chunks, facet = futures.pop(future)
                report["generation_batches"] += 1
                try:
                    result = future.result()
                    questions = result.pop("questions")
                    batch = [Candidate(q, chunks, topic, "generated", "llm_verified")
                             for q in questions]
                    added = bank.add(batch, plan)
                    report.setdefault("generation_log", []).append(dict(
                        topic=topic["id"], facet=facet, round=round_index, added=added,
                        supported_titles=[q.title for q in questions],
                        evidence_ids=[c.id for c in chunks], **result,
                    ))
                except Exception as exc:
                    report["errors"].append(dict(
                        topic=topic["id"], error_type=type(exc).__name__, round=round_index,
                        category=getattr(exc, "category", None),
                    ))
                checkpoint()

            for topic in missing:
                gap = topic["quota"] - bank.counts[topic["id"]]
                parallel = min(workers, math.ceil(gap / 5))
                for slot in range(parallel):
                    facet = topic["facets"][(round_index * workers + slot) % len(topic["facets"])]
                    pool_key = (topic["id"], facet)
                    if pool_key not in pools:
                        pools[pool_key] = knowledge.retrieve(
                            topic["name"] + " " + facet, k=32,
                        )
                    for future in list(futures):
                        if future.done():
                            consume(future)
                    pool = pools[pool_key]
                    gap = topic["quota"] - bank.counts[topic["id"]]
                    if not pool or gap <= 0:
                        continue
                    start = round_index * 5 % len(pool)
                    chunks = (pool[start:] + pool[:start])[:16]
                    existing = [r["title"] for r in bank.rows(topic["id"])]
                    request = InterviewRequest(
                        topic=topic["name"], query=topic["name"] + " " + facet,
                        knowledge_points=[facet], count=min(5, gap), excluded_titles=existing,
                    )
                    future = executor.submit(generate_batch, settings, request, chunks)
                    futures[future] = (topic, chunks, facet)
            for future in as_completed(list(futures)):
                consume(future)
    report["complete"] = all(bank.counts[t["id"]] == t["quota"] for t in plan["topics"])
    report["finished_at"] = time.time()
    bank.export(output / "questions.jsonl")
    bank.export(output / "questions.md")
    checkpoint()
    return report
