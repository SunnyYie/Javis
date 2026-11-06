"""Opt-in real providers. Synthetic career/JD inputs are NOT live recruitment data."""
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from brain_leader.agent import chat_turn
from brain_leader.config import Settings
from brain_leader.models import BrainError
from brain_leader.service import Service

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("BL_LIVE") != "1", reason="opt-in real providers"),
]
ROOT = Path(__file__).resolve().parents[1]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.fixture(scope="module")
def live():
    settings = Settings()
    for path, namespace in (
        (settings.ka_data_dir, settings.ka_namespace),
        (settings.iqb_data_dir, settings.iqb_namespace),
    ):
        assert path and namespace, "Use explicit isolated corpora for acceptance"
        manifest = json.loads((path / "isolation.json").read_text())
        assert manifest["namespace"] == namespace
        assert namespace.startswith(("blka_", "bliqb_"))
    assert settings.data_dir.resolve() != (ROOT / "data").resolve()
    assert settings.memory_dir.resolve() != (ROOT.parent / "memory").resolve()
    evidence = Path(os.environ["BL_EVIDENCE_DIR"])
    evidence.mkdir(parents=True, exist_ok=True)
    write(evidence / "environment.json", {
        "model": settings.model_fields_for_children()["llm_model"],
        "runtime": str(settings.data_dir), "memory": str(settings.memory_dir),
        "ka": str(settings.ka_data_dir), "ka_namespace": settings.ka_namespace,
        "iqb": str(settings.iqb_data_dir), "iqb_namespace": settings.iqb_namespace,
        "local_only": settings.local_only,
        "career_inputs": "synthetic, not real recruitment inventory",
    })
    service = Service(settings, "real-cycle")
    service.consent()
    yield service, evidence
    service.close()


def test_real_career_cycle(live):
    service, evidence = live
    started = time.monotonic()
    report = {"synthetic_inputs": True, "live_recruitment_inventory": False}
    facts = [
        ("summary", "合成软件验收职业样本，不代表真实候选人。"),
        ("skills", "Python、LangChain、React、TypeScript、SQL。"),
        ("work", "示例软件团队 应用开发工程师 2024-04至2026-06，负责Python接口与React页面开发。"),
        ("projects", "内部知识检索演示项目：使用LangChain实现文档分块和检索，使用React展示来源引用和错误提示；个人负责API和结果列表，用请求日志排查重复调用。"),
        ("education", "示例大学 计算机科学与技术 本科 2020-09至2024-06。"),
    ]
    try:
        for section, text in facts:
            service.resumes.add_fact(section, text, confirmed=True,
                                     source={"type": "synthetic_acceptance"})
        draft = service.resumes.generate()
        write(evidence / "draft.json", draft)
        assert draft["sections"]
        service.resumes.adopt(draft["id"])
        assert service.state()["adopted_resume"] == draft["id"]
        artifacts = service.resumes.export()
        write(evidence / "resume-artifacts.json", artifacts)
        assert {a["format"] for a in artifacts} == {"md", "docx", "pdf"}
        report["resume_from_zero"] = "passed"
        pdf = next(a["path"] for a in artifacts if a["format"] == "pdf")
        service.import_resume(pdf)
        assert service.adapters.job.state()["resume"]["pages"][0]["method"] == "text"
        report["existing_resume_parse"] = "passed"

        service.preferences(roles=["Agent应用工程师"], cities=["杭州"], target_monthly_k=20)
        service.snapshot(presented=True)
        service.adapters.job.confirm()
        jobs = [
            {"name": "合成验收岗位 非真实招聘 Agent应用工程师", "company": "合成示例公司",
             "city": "杭州", "salary": "18-28K", "experience": "1-3年",
             "description": "软件验收合成JD，不代表真实招聘。需要Python、LangChain和React，负责知识检索应用和来源引用界面。"},
            {"name": "合成异地验收岗位 非真实招聘", "company": "合成示例公司",
             "city": "北京", "salary": "25-35K",
             "description": "软件验收合成JD，需要Python接口开发。"},
        ]
        path = evidence / "synthetic-jobs-NOT-LIVE.json"
        write(path, jobs)
        approved = service.authorize(path, "jobs")
        imported = service.adapters.job.import_file(str(approved))
        result = service.adapters.job.match()
        write(evidence / "matching.json", result)
        assert len(result["results"]) == 2
        assert all(r["level"] != "评估失败" for r in result["results"])
        assert result["results"][1]["level"] == "不符合"
        target = service.select_job(imported["run"]["job_ids"][0], imported["run"]["id"])
        assert target["run_id"] == imported["run"]["id"]
        report["synthetic_job_matching"] = "passed"

        knowledge = service.explain("LangChain 如何定义并绑定工具？")
        write(evidence / "knowledge.json", knowledge)
        assert knowledge["sufficient"] and knowledge["citations"] and knowledge["persisted"]
        report["real_knowledge_rag"] = "passed"
        questions = service.questions("RAG 检索增强生成的基本流程", 1)
        write(evidence / "questions.json", questions)
        assert questions["sufficient"] and questions["persisted"] and len(questions["questions"]) == 1
        assert service.store.links(service.sid)
        report["real_question_rag"] = "passed"

        reviews = []
        for mode in ("technical", "project", "behavioral", "mixed"):
            public = service.interviews.prepare(mode, 1, 0)
            assert "reference" not in public and "answer" not in public
            service.interviews.control("pause")
            recovered = Service(service.settings, service.sid)
            assert recovered.interviews.current()["status"] == "paused"
            recovered.close()
            service.interviews.control("resume")
            text = (
                "先对文档进行分块并建立索引，查询时检索相关片段交给模型生成回答，保留来源引用；"
                "我会核对引用是否支持结论，并对无依据的问题明确返回不知道。"
                if mode in ("technical", "mixed") else
                "在演示项目中我负责API和结果列表，发现请求重复后通过日志定位触发来源，"
                "调整请求入口并验证成功、失败和加载状态；没有核实过业务收益，不编造数字。"
            )
            result = service.interviews.answer(public["token"], text)
            assert result["interview"]["status"] == "awaiting_finish"
            assert service.interviews.answer(public["token"], text)["duplicate"]
            with pytest.raises(BrainError):
                service.interviews.review()
            service.interviews.control("finish")
            review = service.review()
            assert review["rows"][0]["answers"]
            assert 0 < review["coverage"] <= 1
            reviews.append(review)
        write(evidence / "interview-reviews.json", reviews)
        report["real_four_mode_interviews"] = "passed"
        plan = service.learning_plan("改进技术回答并补充验证边界")
        write(evidence / "learning-plan.json", plan)
        candidate = service.propose_memory("preference", "验收偏好：先练技术题")
        assert not service.memory.all()
        service.confirm_memory(candidate["id"])
        other = Service(service.settings, "memory-read")
        assert any(m["id"] == candidate["id"] for m in other.memory.all())
        other.close()
        report["confirmed_memory_and_retraining"] = "passed"

        transcript = service.authorize(ROOT / "tests/fixtures/synthetic-interview.md", "transcript")
        reviewed = service.review(transcript["id"])
        write(evidence / "synthetic-record-review.json", reviewed)
        assert all(o["quote"] in transcript["text"] for o in reviewed["observations"])
        report["record_review"] = "passed"
        answer = chat_turn(service, "只把期望城市改为上海，先不要搜索，也不要做其他步骤。")
        write(evidence / "supervisor.json", answer)
        assert service.adapters.job.state()["preferences"]["cities"] == ["上海"]
        assert not service.adapters.job.snapshot()["confirmed"]
        report["real_supervisor_tool_call"] = "passed"
    finally:
        report["seconds"] = round(time.monotonic() - started, 2)
        write(evidence / "cycle-report.json", report)


def test_real_cli_json_and_terminal_restore(live):
    service, evidence = live
    # Independent setup: a failed preceding scenario must not cause a false restore failure.
    terminal = Service(service.settings, "terminal-restore")
    terminal.consent()
    terminal.preferences(roles=["Agent应用工程师"], cities=["上海"], target_monthly_k=20)
    terminal.close()
    result = subprocess.run(
        [sys.executable, "-m", "brain_leader", "chat", "-s", "terminal-restore", "-q", "/status", "--json"],
        capture_output=True, text=True, timeout=60,
    )
    (evidence / "cli-status.stdout").write_text(result.stdout)
    (evidence / "cli-status.stderr").write_text(result.stderr)
    assert result.returncode == 0
    assert json.loads(result.stdout)["data"]["session"] == "terminal-restore"
    import pexpect
    with (evidence / "terminal-transcript.txt").open("w") as log:
        child = pexpect.spawn(
            sys.executable, ["-m", "brain_leader", "chat", "-s", "terminal-restore"],
            encoding="utf-8", timeout=600, dimensions=(40, 180),
        )
        child.logfile = log
        try:
            child.expect_exact("bl> ")
            child.sendline("只查看当前状态，告诉我期望城市。不要修改，也不要搜索。")
            child.expect_exact("bl> ")
            assert "上海" in child.before
            child.sendline("/quit")
            child.expect(pexpect.EOF)
        finally:
            child.close(force=True)
    assert child.exitstatus == 0


def test_production_corpus_identity_and_version_unchanged(live):
    service, evidence = live
    results = []
    for path in (service.settings.ka_data_dir, service.settings.iqb_data_dir):
        manifest = json.loads((path / "isolation.json").read_text())
        with sqlite3.connect(Path(manifest["source"]).as_uri() + "?mode=ro", uri=True) as db:
            metadata = dict(db.execute("SELECT key,value FROM meta"))
        results.append({
            "source": manifest["source"],
            "baseline_corpus_id": manifest["source_corpus_id"],
            "current_corpus_id": metadata["corpus_id"],
            "baseline_version": manifest["source_version"],
            "current_version": metadata["version"],
            "identity_and_version_unchanged": (
                metadata["corpus_id"] == manifest["source_corpus_id"]
                and metadata["version"] == manifest["source_version"]
            ),
        })
    write(evidence / "production-corpus-check.json", results)
    assert all(row["identity_and_version_unchanged"] for row in results), results


def test_real_isolated_corpus_queries(live):
    """Exercise both real isolated services before the production invariant is checked."""
    service, evidence = live
    results = {}
    try:
        knowledge = service.explain("langchain-ai/langchain 的README列出哪些生态组件？")
        results["knowledge"] = {
            "sufficient": knowledge["sufficient"], "persisted": knowledge["persisted"],
            "citations": len(knowledge["citations"]),
        }
        assert knowledge["sufficient"] and knowledge["persisted"] and knowledge["citations"]
        questions = service.questions("RAG检索增强生成的基本流程", 1)
        results["questions"] = {
            "sufficient": questions["sufficient"], "persisted": questions["persisted"],
            "citations": len(questions["citations"]), "count": len(questions["questions"]),
        }
        assert questions["sufficient"] and questions["persisted"] and questions["citations"]
        assert len(questions["questions"]) == 1
    finally:
        write(evidence / "isolated-corpus-queries.json", results)


def test_real_terminal_full_cycle(live):
    """Real PTY drives every action; no service, model, browser, or DB test double."""
    import re

    import pexpect

    service, evidence = live
    path = evidence / "terminal-synthetic-jobs-NOT-LIVE.json"
    write(path, [{
        "name": "合成终端验收 Agent开发岗位 非真实招聘", "city": "杭州", "salary": "18-28K",
        "company": "合成示例公司", "experience": "1-3年",
        "description": "合成验收JD，需要Python、React、LangChain，负责知识检索接口和来源引用界面。",
    }])
    transcript = evidence / "terminal-full-cycle.txt"
    results = []
    with transcript.open("w") as log:
        child = pexpect.spawn(
            sys.executable,
            ["-m", "brain_leader", "chat", "--session", "terminal-cycle", "--allow-remote"],
            encoding="utf-8", timeout=900, dimensions=(45, 240),
        )
        child.logfile = log

        def ask(text):
            child.sendline(text)
            child.expect_exact("bl> ")
            raw = child.before
            assert not re.search(r"(?m)^错误：", raw), raw
            matches = list(re.finditer(r'(?m)^\{', raw))
            assert matches, f"No structured result for {text.split()[0]}"
            result, _ = json.JSONDecoder().raw_decode(raw[matches[0].start():])
            results.append({
                "command": text.split()[0], "output_keys": list(result),
                "sufficient": result.get("sufficient"),
            })
            if "sufficient" in result:
                assert result["sufficient"] and result["persisted"], result
            return result

        try:
            child.expect_exact("bl> ")
            ask("/facts add summary 合成终端软件验收样本，不代表真实候选人。")
            ask("/facts add skills Python、React、LangChain、SQL。")
            ask("/facts add projects 合成知识检索项目，使用LangChain进行文档分块和检索，使用React展示来源引用；我负责API接口和结果列表，并用请求日志排查重复调用，没有核验业务收益。")
            ask("/facts add education 示例大学 计算机相关专业 本科 2020-09至2024-06。")
            draft = ask("/draft")
            assert draft["sections"]
            ask("/adopt " + draft["id"])
            ask('/prefs {"roles":["Agent应用工程师"],"cities":["杭州"],"target_monthly_k":20}')
            ask("/status")
            assert ask("/confirm")["confirmed"]
            imported = ask("/import " + str(path))
            matched = ask("/match")
            assert matched["results"][0]["level"] != "评估失败"
            ask("/target " + imported["run"]["job_ids"][0] + " " + imported["run"]["id"])
            knowledge = ask("/knowledge langchain-ai/langchain 的README列出哪些生态组件？")
            assert knowledge["sufficient"] and knowledge["citations"]
            questions = ask("/questions RAG检索增强生成的基本流程 --count 1")
            assert len(questions["questions"]) == 1 and questions["sufficient"]
            interview = ask("/interview technical 1 0")
            assert "answer" not in interview
            ask("/pause")
            ask("/continue")
            ask("/answer " + interview["token"] + " RAG先对文档分块并建立索引，查询时检索相关片段交给模型生成回答，同时保留引用；资料不足时明确不知道，并核对引用是否支持结论。")
            ask("/finish")
            review = ask("/review")
            assert review["rows"][0]["answers"]
            assert review["coverage"] > 0
            current = ask("/interview mixed 3 0")
            for index in range(3):
                answer = (
                    "先分块并建立索引，检索相关片段后生成带引用的回答，检查引用支持性，"
                    "来源不足时明确不知道。"
                    if index == 0 else
                    "在合成知识检索项目中我负责API和列表，发现重复调用后检查请求日志，"
                    "定位重复触发并验证成功、失败和加载状态；没有核验业务收益，不编造数字。"
                )
                current = ask("/answer " + current["token"] + " " + answer)["interview"]
            assert current["status"] == "awaiting_finish"
            ask("/finish")
            review = ask("/review")
            assert [row["kind"] for row in review["rows"]] == [
                "technical", "project", "behavioral",
            ]
            plan = ask("/learn 改进回答并验证引用依据")
            assert plan["tasks"]
            memory = ask("/memory add preference 合成验收偏好：先练技术题")
            confirmed = ask("/memory confirm " + memory["id"])
            assert confirmed["confirmed_at"]
            exported = ask("/export resume")
            assert len(exported["artifacts"]) == 3
            ask("/export review " + review["id"])
            child.sendline("/quit")
            child.expect(pexpect.EOF)
        finally:
            child.close(force=True)
            write(evidence / "terminal-full-results.json", results)
    assert child.exitstatus == 0


def test_real_interview_across_cli_processes(live):
    service, evidence = live
    records = []

    def call(text):
        process = subprocess.run(
            [sys.executable, "-m", "brain_leader", "chat", "-s", "process-interview",
             "--allow-remote", "-q", text, "--json"],
            capture_output=True, text=True, timeout=600,
        )
        assert process.returncode == 0, process.stderr
        result = json.loads(process.stdout)
        assert result["status"] == "ok", result
        records.append({"command": text.split()[0], "result": result})
        write(evidence / "cross-process-interview.json", records)
        return result["data"]

    call("/facts add projects 合成进程恢复验收项目：我负责React结果列表与Python接口，通过请求日志定位重复触发，核对成功、失败和加载状态，没有核验业务收益。")
    first = call("/interview project 1 0")
    call("/pause")
    restored = call("/status")["interview"]
    assert restored["status"] == "paused"
    assert restored["token"] == first["token"]
    call("/continue")
    answer = "/answer " + first["token"] + " 我负责接口和结果列表，通过请求日志定位重复触发，修正请求入口并核对成功、失败和加载状态；没有核验业务收益，不编造数字。"
    assert call(answer)["interview"]["status"] == "awaiting_finish"
    assert call(answer)["duplicate"]
    call("/finish")
    review = call("/review")
    assert review["rows"][0]["answers"] and review["coverage"] > 0


def test_real_supervisor_knowledge_worker_lifecycle(live):
    root, evidence = live
    service = Service(root.settings, "supervisor-knowledge")
    service.consent()
    outputs = []
    try:
        for _ in range(2):
            result = chat_turn(
                service,
                "本轮请明确调用 explain_knowledge 工具，question 为"
                "“langchain-ai/langchain 的README列出哪些生态组件？”；"
                "只做这次知识查询，不修改条件，不从上轮回答推测结果。",
            )
            outputs.append(result)
            write(evidence / "supervisor-knowledge.json", outputs)
            operations = [o for o in result["operations"] if o["tool"] == "explain_knowledge"]
            assert operations and all(o["status"] == "ok" for o in operations)
            record = service.store.records(service.sid, "knowledge")[-1]
            assert record["sufficient"] and record["persisted"] and record["citations"]
    finally:
        service.close()
