"""Opt-in integration checks. Synthetic JDs are explicitly NOT live job inventory."""

import json
import os
from pathlib import Path

import pytest

from job_agent.agent import build_agent, chat_turn
from job_agent.config import ROOT, Settings
from job_agent.models import AgentError, ResumeProfile
from job_agent.privacy import EMAIL, ID_NUMBER, PHONE
from job_agent.service import Service

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("JA_LIVE") != "1", reason="set JA_LIVE=1 for real services"),
]


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    source = Path(os.environ["JA_RESUME"])
    assert source.is_file(), "JA_RESUME must identify the user-authorized real PDF"
    evidence = Path(os.environ.get(
        "JA_EVIDENCE_DIR", str(tmp_path_factory.mktemp("job-agent-live"))
    )).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    settings = Settings(data_dir=evidence / "runtime")
    service = Service(settings, "real-services", emit=lambda *_: None)
    service.consent()
    assert service.cache.ping(), "Live checks require real Redis, not a fallback"
    return service, source, evidence


def test_real_text_ocr_structured_profile_and_redis(live):
    service, source, evidence = live
    approved = service.authorize_file(source, "resume")
    remote_payloads = []
    original = service.models.structured

    def observed(schema, task, payload):
        remote_payloads.append(payload)
        return original(schema, task, payload)

    service.models.structured = observed
    comparisons = []
    for force in (False, True):
        service.parse_resume(str(approved), force_ocr=force)
        profile = ResumeProfile.model_validate(service.state()["resume"])
        assert profile.education
        assert any(e.school == "东北林业大学" and "软件" in e.major and "本科" in e.degree
                   for e in profile.education)
        assert {w.company for w in profile.work} == {"脉脉", "启明合心"}
        assert sorted(w.start for w in profile.work) == ["2024-08", "2025-11"]
        assert sorted(w.end for w in profile.work) == ["2025-10", "present"]
        assert profile.work_months == 27, "As-of 2026-11-05 excludes partial current month"
        assert len(profile.projects) == 4
        skills = " ".join(profile.skills)
        assert "React" in skills and "TypeScript" in skills and "Node.js" in skills
        assert all(p.method == ("ocr" if force else "text") for p in profile.pages)
        if force:
            assert profile.pages[0].confidence > 0.9
            assert profile.pages[0].blocks
        comparisons.append({
            "mode": "ocr" if force else "text",
            "work_months": profile.work_months,
            "schools": [e.school for e in profile.education],
            "companies": [w.company for w in profile.work],
            "projects": [p.name for p in profile.projects],
            "confidence": [p.confidence for p in profile.pages],
        })
    service.models.structured = original
    assert remote_payloads, "At least one actual model invocation is required"
    for payload in remote_payloads:
        assert not PHONE.search(payload)
        assert not EMAIL.search(payload)
        assert not ID_NUMBER.search(payload)
    (evidence / "resume-checks.json").write_text(
        json.dumps(comparisons, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    assert len(list(service.cache.client.scan_iter(service.cache.prefix + "*"))) >= 4


def test_real_image_scan_and_mixed_pdf(live):
    import pypdfium2 as pdfium

    service, source, evidence = live
    with pdfium.PdfDocument(source) as doc:
        bitmap = doc[0].render(scale=3)
        try:
            image = bitmap.to_pil().copy()
        finally:
            bitmap.close()
    png, scan, mixed = (evidence / n for n in ("resume.png", "scan.pdf", "mixed.pdf"))
    image.save(png)
    image.convert("RGB").save(scan, "PDF", resolution=216)
    with pdfium.PdfDocument.new() as doc:
        with pdfium.PdfDocument(source) as original, pdfium.PdfDocument(scan) as scanned:
            doc.import_pages(original)
            doc.import_pages(scanned)
        doc.save(mixed)
    image_pages = service.parser.extract(png)
    scan_pages = service.parser.extract(scan)
    mixed_pages = service.parser.extract(mixed)
    assert image_pages[0].method == scan_pages[0].method == "ocr"
    assert [p.method for p in mixed_pages] == ["text", "ocr"]
    for pages in (image_pages, scan_pages, mixed_pages):
        assert all("东北林业大学" in p.text for p in pages)
        assert all("React" in p.text and "TypeScript" in p.text for p in pages)
    (evidence / "image-scan-checks.json").write_text(json.dumps({
        "png": [p.method for p in image_pages],
        "scanned_pdf": [p.method for p in scan_pages],
        "mixed_pdf": [p.method for p in mixed_pages],
    }, ensure_ascii=False, indent=2))


def test_real_model_matching_on_explicit_synthetic_jd(live):
    service, _, evidence = live
    assert service.state()["resume"], "Run resume checks first"
    service.update_preferences(roles=["前端工程师"], cities=["杭州"], target_monthly_k=25)
    service.snapshot(presented=True)
    service.confirm()
    fixture = Path(__file__).parent / "fixtures" / "synthetic-jobs-NOT-LIVE.json"
    authorized = service.authorize_file(fixture, "jobs")
    service.import_file(str(authorized))
    first = service.match()
    assert [r["level"] for r in first["results"]] == ["可尝试", "不符合"]
    assert first["results"][0]["resume_quotes"] and first["results"][0]["job_quotes"]
    second = service.match()
    assert second["results"][0]["cached"]
    exports = [service.export(f) for f in ("json", "csv", "md")]
    service.update_preferences(target_monthly_k=40)
    with pytest.raises(AgentError, match="重新匹配"):
        service.export("json")
    service.snapshot(presented=True)
    service.confirm()
    third = service.match()
    assert all(r["level"] == "不符合" for r in third["results"])
    (evidence / "synthetic-jd-checks.json").write_text(json.dumps({
        "notice": "真实模型与 Redis，岗位为明确标注的合成测试输入，不是实时招聘",
        "first": first, "cached_second": second, "changed_conditions": third,
        "exports": exports,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def test_real_agent_preferences_and_restore(live):
    service, _, evidence = live
    # The previous scenario raised the target to 40K. Establish an independent
    # baseline so "raise to 30K" is not a contradictory user instruction.
    service.update_preferences(target_monthly_k=25)
    agent = build_agent(service)
    answer = chat_turn(agent, service, "只看杭州，薪资提高到 30K，先不要搜索岗位。")
    prefs = service.state()["preferences"]
    assert prefs["cities"] == ["杭州"]
    assert prefs["target_monthly_k"] == 30
    assert not service.snapshot()["confirmed"]
    restored = Service(service.settings, service.sid, emit=lambda *_: None)
    assert restored.state()["preferences"] == prefs
    assert restored.storage.history(service.sid)[-1]["content"] == answer
    assert not restored.collection_used
    (evidence / "agent-restore-checks.json").write_text(json.dumps({
        "model": service.settings.model_options()["model"], "answer": answer,
        "preferences": prefs, "restored": True,
    }, ensure_ascii=False, indent=2))


def test_real_terminal_resume_session(live):
    import pexpect

    service, _, evidence = live
    env = {
        **os.environ, "DATA_DIR": str(service.settings.data_dir),
        "TERM": "dumb", "PROMPT_TOOLKIT_NO_CPR": "1",
    }
    child = pexpect.spawn(
        str(ROOT / ".venv/bin/ja"),
        ["chat", "--session", service.sid, "--allow-remote"],
        cwd=str(ROOT), env=env, encoding="utf-8", timeout=90,
        dimensions=(60, 160),
    )
    transcript = []
    try:
        child.expect("你>")
        transcript.append(child.before)
        child.sendline("/status")
        child.expect("你>")
        transcript.append(child.before)
        child.sendline("只看杭州，目标月薪改为35K，先不要搜索，也不要导出。")
        child.expect("你>")
        transcript.append(child.before)
        assert service.state()["preferences"]["target_monthly_k"] == 35
        child.sendline("/quit")
        child.expect(pexpect.EOF)
        transcript.append(child.before)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.terminate(force=True)
    (evidence / "terminal-transcript.txt").write_text(
        "\n".join(transcript), encoding="utf-8",
    )


def test_real_redis_scoped_deletion_preserves_original(live):
    import hashlib

    service, source, evidence = live
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    temporary = Service(service.settings, "delete-only-this-session", emit=lambda *_: None)
    authorized = temporary.authorize_file(source, "resume")
    key = temporary.cache.key("deletion-probe", {"synthetic": True})
    temporary.cache.set(key, {"ok": True})
    keep_key = service.cache.key("deletion-probe", {"keep_other_session": True})
    service.cache.set(keep_key, {"keep": True})
    assert temporary.cache.get(key) == {"ok": True}
    temporary.delete()
    assert not authorized.exists()
    assert not temporary.directory.exists()
    assert temporary.cache.client.get(key) is None
    assert service.cache.get(keep_key) == {"keep": True}
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
    (evidence / "deletion-checks.json").write_text(json.dumps({
        "real_redis": True, "deleted_session_cache": True,
        "other_session_preserved": True, "original_file_unchanged": True,
    }, indent=2))
