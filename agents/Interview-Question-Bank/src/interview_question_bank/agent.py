import json
import re
import time
from collections import Counter
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .cache import Cache, normalize, request_id
from .chunking import Chunker
from .collectors import Collectors
from .llm import Models
from .models import Answer, Chunk, Draft, InterviewRequest, ServiceError, difficulty_targets
from .retrieval import Knowledge
from .storage import Store


class State(TypedDict, total=False):
    question: str
    history: list[dict]
    local_only: bool
    rid: str
    request: InterviewRequest
    chunks: list[Chunk]
    transient: list[Chunk]
    sufficient: bool
    queries: list[str]
    online: bool
    answer: Answer


class Assistant:
    def __init__(self, settings, *, store=None, knowledge=None, models=None,
                 collectors=None, cache=None, stage=lambda _: None, emit=lambda _: None):
        self.s = settings
        self.store = store or Store(settings.data_dir)
        self.knowledge = knowledge or Knowledge(settings, self.store)
        self.models = models or Models(settings)
        self.collectors = collectors or Collectors(settings)
        def trace(name):
            self.store.event("agent", name)
            stage(name)

        self.stage, self.emit = trace, emit
        self.warnings = []
        self.cache = cache or Cache(settings, lambda text: self.warnings.append(text))
        graph = StateGraph(State)
        for name, function in (
            ("cache", self.read_cache), ("parse", self.parse),
            ("retrieve", self.retrieve), ("assess", self.assess),
            ("online", self.online), ("generate", self.generate), ("save", self.save),
        ):
            graph.add_node(name, function)
        graph.add_edge(START, "cache")
        graph.add_conditional_edges("cache", lambda s: bool(s.get("answer")),
                                    {True: END, False: "parse"})
        graph.add_edge("parse", "retrieve")
        graph.add_edge("retrieve", "assess")
        graph.add_conditional_edges(
            "assess", lambda s: s["sufficient"] or s["local_only"],
            {True: "generate", False: "online"},
        )
        graph.add_edge("online", "generate")
        graph.add_edge("generate", "save")
        graph.add_edge("save", END)
        self.graph = graph.compile()

    def ask(self, question, history=None, local_only=False):
        question = question.strip()
        if not question or len(question) > 8000:
            raise ValueError("问题不能为空，且最多 8000 字符。")
        match = re.search(r"(?<![第\d])(\d+)\s*(?:道|个)?\s*(?:面试)?题", question)
        if match and not 1 <= int(match[1]) <= 20:
            raise ValueError("每次题量支持 1–20 题。")
        self.warnings = []
        result = self.graph.invoke({
            "question": question, "history": history or [], "local_only": local_only,
            "transient": [], "online": False,
        })
        answer = result["answer"]
        answer.warnings = list(dict.fromkeys(answer.warnings + self.warnings))
        self.emit(answer.text)
        return answer

    def read_cache(self, state):
        self.stage("cache")
        rid = request_id(state["question"], state["history"], self.s,
                         self.store.corpus_id, state["local_only"])
        answer = self.cache.get(rid, self.store.corpus_id, self.store.version)
        if answer:
            # SQLite metadata checks do not load the embedding model or call Milvus.
            active = {c.id for c in self.store.active_chunks()}
            if all(c["id"] in active for c in answer.citations):
                return {"answer": answer, "rid": rid}
        return {"rid": rid}

    def parse(self, state):
        self.stage("parse")
        request = self.models.parse(state["question"], state["history"])
        if request.mode == "explain" and request.target_number:
            previous = []
            for turn in reversed(state["history"]):
                try:
                    previous = json.loads(turn["answer"]).get("questions", [])
                except (ValueError, TypeError, AttributeError):
                    continue
                if previous:
                    break
            if request.target_number > len(previous):
                raise ValueError("该题号不存在，请先获取题单，或使用完整题目追问。")
        return {"request": request}

    def lookup(self, request, question=None, history=None):
        # Resolve anaphoric follow-ups before cross-encoding them against source text.
        rerank_query = request.query if history else question or request.query
        chunks = self.knowledge.retrieve(
            request.query, k=min(32, max(8, request.count * 3)),
            rerank_query=rerank_query,
        )
        self.store.event("retrieval", json.dumps(self.knowledge.last_trace, ensure_ascii=False))
        return chunks

    def retrieve(self, state):
        self.stage("retrieve")
        chunks = self.lookup(state["request"], state["question"], state.get("history"))
        refreshed = False
        for source in dict.fromkeys(c.source for c in chunks if c.expires_at <= time.time()):
            if state["local_only"]:
                self.warnings.append(f"本地模式使用过期资料：{source}")
                continue
            self.stage("refresh")
            try:
                old = self.store.source(source)
                paths = json.loads(old["coverage"]).get("selected") if old else None
                self.knowledge.ingest(self.collectors.collect(source, paths=paths))
                refreshed = True
            except Exception as exc:
                self.record(source, exc)
                self.warnings.append(f"刷新失败，保留并标注旧资料：{source}")
        return {"chunks": self.lookup(
            state["request"], state["question"], state.get("history"),
        ) if refreshed else chunks}

    def assess(self, state):
        self.stage("assess")
        verdict = self.models.assess(state["request"], state["chunks"])
        return {
            "sufficient": bool(verdict.sufficient and state["chunks"]),
            "queries": verdict.queries[:2] or [state["request"].query],
        }

    def record(self, source, exc):
        category = str(exc) if isinstance(exc, ServiceError) else type(exc).__name__
        self.store.event(source, category)
        self.warnings.append(f"{source}: {category}")

    def online(self, state):
        self.stage("online")
        seen, transient = set(), []
        chunks = state["chunks"]
        queries = state["queries"]
        sufficient = False
        for round_index in range(2):
            query = queries[min(round_index, len(queries) - 1)]
            urls = []
            for name, provider in (("github", self.collectors.github),
                                   ("tavily", self.collectors.web)):
                try:
                    found = provider.search(query)
                    self.store.event(name, f"search_ok:{len(found)}")
                    urls.extend(found)
                except ServiceError as exc:
                    self.record(name, exc)
            candidates = [url for url in dict.fromkeys(urls) if url not in seen][:5]
            for url in candidates:
                seen.add(url)
                try:
                    bundle = self.collectors.collect(url, file_limit=1, query=query)
                    try:
                        self.stage("ingest")
                        self.knowledge.ingest(bundle)
                    except Exception as exc:
                        self.record(url, exc)
                        self.warnings.append(f"资料未保存：{url}")
                        chunker = Chunker(
                            self.knowledge.embeddings.tokenizer, self.s.embedding_model,
                            self.s.chunk_tokens, self.s.chunk_overlap,
                        )
                        ttl = self.s.repo_ttl if bundle.repo else self.s.web_ttl
                        for document in bundle.documents:
                            transient.extend(chunker.split(document, ttl))
                except ServiceError as exc:
                    self.record(url, exc)
            try:
                local = self.lookup(state["request"], state["question"], state.get("history"))
            except ServiceError:
                if not transient:
                    raise
                local = []
            chunks = list({c.id: c for c in (transient[:8] + local)}.values())[:32]
            self.stage("assess")
            verdict = self.models.assess(state["request"], chunks)
            sufficient = bool(verdict.sufficient and chunks)
            if sufficient:
                break
            queries = verdict.queries[:2] or queries
        return {"chunks": chunks, "transient": transient, "online": True,
                "sufficient": sufficient}

    def generate(self, state):
        self.stage("generate")
        request, chunks = state["request"], state["chunks"]
        by_id = {c.id: c for c in chunks}

        def clean(draft):
            accepted = []
            titles = {normalize(t) for t in request.excluded_titles}
            for item in draft.questions[:request.count]:
                if normalize(item.title) in titles or not set(item.evidence_ids) <= by_id.keys():
                    self.warnings.append("已丢弃重复题目或引用无效的题目。")
                    continue
                if item.origin == "来源原题" and (
                    not item.source_question_quote.strip()
                    or not any(item.source_question_quote in by_id[cid].text
                               for cid in item.evidence_ids)
                ):
                    self.warnings.append("原题逐字引用未通过，已标注为基于资料生成。")
                    item.origin = "基于资料生成"
                    item.source_question_quote = ""
                titles.add(normalize(item.title))
                accepted.append(item)
            draft.questions = accepted if request.mode == "questions" else []
            if request.mode == "questions":
                draft.explanation = ""
                draft.explanation_evidence_ids = []
            elif not set(draft.explanation_evidence_ids) <= by_id.keys():
                draft.explanation = ""
                draft.explanation_evidence_ids = []
            return draft

        def completed(draft):
            return (
                Counter(q.difficulty for q in draft.questions) == Counter(difficulty_targets(request))
                if request.mode == "questions"
                else bool(draft.explanation and draft.explanation_evidence_ids)
            )

        def score(draft, verdict):
            supported = sum(
                i in verdict.supported_question_numbers for i in range(1, len(draft.questions) + 1)
            ) if request.mode == "questions" else int(
                bool(draft.explanation and draft.explanation_evidence_ids)
                and verdict.explanation_supported
            )
            expected = len(draft.questions) if request.mode == "questions" else 1
            ready = (
                verdict.sufficient and completed(draft) and not draft.gaps
                and supported == expected
            )
            return ready, supported

        draft = clean(self.models.draft(request, chunks, state["history"])) if chunks else Draft()
        self.stage("verify")
        verification = self.models.verify(request, draft, chunks) if chunks else None
        if verification and not score(draft, verification)[0]:
            self.stage("revise")
            try:
                revised = clean(self.models.draft(
                    request, chunks, state["history"],
                    feedback={"draft": draft, "verification": verification},
                ))
                self.stage("verify")
                checked = self.models.verify(request, revised, chunks)
                if score(revised, checked) >= score(draft, verification):
                    draft, verification = revised, checked
            except ServiceError as exc:
                self.record("revision", exc)
                self.warnings.append("修订失败，以下仅返回此前核验通过的内容。")
        if verification:
            draft.questions = [
                q for i, q in enumerate(draft.questions, 1)
                if i in verification.supported_question_numbers
            ]
            if not verification.explanation_supported:
                draft.explanation = ""
                draft.explanation_evidence_ids = []
            if not verification.sufficient:
                draft.gaps.append(verification.reason)
        used = set(cid for q in draft.questions for cid in q.evidence_ids)
        if draft.explanation and draft.explanation_evidence_ids:
            used.update(draft.explanation_evidence_ids)
        else:
            draft.explanation = ""
        citations = [
            dict(c.to_dict(), number=i)
            for i, c in enumerate((c for c in chunks if c.id in used), 1)
        ]
        complete = completed(draft)
        sufficient = bool(
            complete and citations and verification and verification.sufficient
            and not draft.gaps
        )
        transient_ids = {c.id for c in state["transient"]}
        persisted = bool(citations) and not bool(used & transient_ids)
        if used and not persisted:
            self.warnings.append("本次答案含未保存资料；后续不保证可复用，且不会缓存。")
        numbers = {c["id"]: c["number"] for c in citations}
        text = []
        if not sufficient:
            text.append("资料覆盖不足；以下仅列出有依据的内容。")
        for i, q in enumerate(draft.questions, 1):
            refs = " ".join(f"[{numbers[cid]}]" for cid in dict.fromkeys(q.evidence_ids))
            text.extend([
                f"\n{i}. [{q.difficulty}] {q.title}",
                f"知识点：{'、'.join(q.knowledge_points)} | {q.origin}",
                f"参考答案：{q.answer} {refs}",
                "回答要点：" + "；".join(q.key_points),
            ])
        if draft.explanation:
            refs = " ".join(
                f"[{numbers[cid]}]" for cid in dict.fromkeys(draft.explanation_evidence_ids)
            )
            text.append(draft.explanation + " " + refs)
        if not citations:
            text.append("未找到足够的可靠正文，无法提供有来源支持的题目或解释。")
        if not complete and request.mode == "questions":
            draft.gaps.append(f"请求 {request.count} 题，实际返回 {len(draft.questions)} 题；"
                              "题量或难度分配未完全满足。")
        text.extend(f"缺口：{gap}" for gap in draft.gaps if gap)
        answer = Answer(
            text="\n".join(text), citations=citations,
            route="online" if state["online"] else "local", sufficient=sufficient,
            persisted=persisted, warnings=list(self.warnings),
            questions=[q.model_dump() for q in draft.questions],
            request=request.model_dump(), explanation=draft.explanation,
            retrieval=dict(self.knowledge.last_trace, context_ids=[c.id for c in chunks]),
        )
        return {"answer": answer}

    def save(self, state):
        self.stage("save")
        answer = state["answer"]
        try:
            self.store.save_questions(answer)
        except Exception as exc:
            answer.persisted = False
            self.record("question_records", exc)
        self.cache.put(state["rid"], self.store.corpus_id, self.store.version,
                       state["question"], answer)
        return {}
