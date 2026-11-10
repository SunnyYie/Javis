import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from career_team.agent import chat_turn, confirm_switch
from career_team.storage import Memory
from career_team.team import CareerTeam
from javis_core.models import BrainError
from rental_team.config import Settings as RentalSettings
from rental_team.leader import RentalLeader

from brain_leader.javis_cli import run
from brain_leader.orchestrator import REGISTRY, Javis, Route


def prepared_interview(consented):
    consented.questions("React", 1)
    return consented.interviews.prepare("technical", 1, 0)


def test_explicit_rental_switch_is_not_submitted_as_interview_answer(consented):
    question = prepared_interview(consented)
    before = len(consented.models.calls)
    result = chat_turn(consented, "帮我找房")
    assert result["pending_action"]["action"] == "confirm_interview_pause"
    current = consented.interviews.current()
    assert current["status"] == "waiting"
    assert current["token"] == question["token"]
    assert not current["answers"]
    assert len(consented.models.calls) == before
    assert "PRIVATE_REFERENCE" not in json.dumps(result)


def test_switch_rejection_and_acceptance_keep_same_question_and_resume_token(consented):
    question = prepared_interview(consented)
    chat_turn(consented, "我想租房")
    assert not confirm_switch(consented, False)["switched"]
    assert consented.interviews.current()["status"] == "waiting"
    chat_turn(consented, "帮我找房")
    confirm_switch(consented, True)
    paused = consented.interviews.current()
    assert paused["status"] == "paused"
    assert paused["token"] == question["token"]
    with pytest.raises(BrainError, match="面试期间"):
        consented.record_detail(consented.state()["latest_questions"])
    resumed = consented.interviews.control("resume")
    assert resumed["token"] == question["token"]
    assert not consented.interviews.current()["answers"]


def test_ambiguous_short_input_clarifies_without_scoring(consented):
    prepared_interview(consented)
    result = chat_turn(consented, "先换个话题")
    assert result["pending_action"]["action"] == "clarify_answer_or_switch"
    assert not consented.interviews.current()["answers"]


@pytest.mark.parametrize("text", [
    "在北京帮我找房", "帮我在上海找房", "我想在北京租房", "暂停面试，找房",
])
def test_switch_with_location_is_not_recorded_as_answer(consented, text):
    prepared_interview(consented)
    assert chat_turn(consented, text)["pending_action"]["action"] == "confirm_interview_pause"
    assert not consented.interviews.current()["answers"]


def test_pending_switch_cannot_be_confirmed_by_model_tool(consented):
    assert consented.tool("confirm_switch", accept=True)["error"] == "unknown_tool"
    prepared_interview(consented)
    chat_turn(consented, "帮我找房")
    assert consented.tool("confirm_switch", accept=True)["error"] == "interview_in_progress"
    assert consented.interviews.current()["status"] == "waiting"


def test_legacy_memory_is_career_only_and_cross_scope_delete_cannot_remove_it(tmp_path):
    memory = Memory(tmp_path)
    memory.confirm({"id": "old", "text": "私有职业事实", "kind": "fact"})
    other = Memory(tmp_path, scope="rental")
    assert not other.all()
    other.delete("old")
    assert memory.all()[0]["id"] == "old"


class Router:
    """Offline routing test double."""

    def __init__(self, team_id="rental"):
        self.team_id = team_id
        self.calls = []

    def structured(self, schema, task, payload):
        self.calls.append(payload)
        assert schema is Route
        return Route(team_id=self.team_id)


@pytest.fixture
def javis(consented, tmp_path):
    rental = RentalLeader(RentalSettings(
        _env_file=None, credentials_file=tmp_path / "absent",
        data_dir=tmp_path / "rental", quota_path=tmp_path / "quota.sqlite3",
        vigo_api_key="", amap_api_key="", ziroom_python=tmp_path / "absent-python",
    ), consented.sid)
    return Javis(
        consented.settings, consented.sid,
        career=CareerTeam(consented.settings, consented.sid, service=consented),
        rental=rental, models=Router(),
    )


def test_registry_is_static_and_main_has_no_domain_tool_registration(javis):
    assert set(REGISTRY) == {"career", "rental"}
    with pytest.raises(BrainError, match="unknown_team"):
        javis.dispatch("discovered-plugin", "do something")
    source = Path(__file__).resolve().parents[1] / "src/brain_leader/orchestrator.py"
    tree = ast.parse(source.read_text())
    calls = [node.func.id for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
    assert "create_agent" not in calls


def test_router_requires_separate_consent_and_receives_no_career_history(javis, consented):
    consented.resumes.add_fact("projects", "不要共享的职业项目", confirmed=True)
    result = run(javis, "安排一个不支持的任务")
    assert result["error"] == "consent_required"
    javis.consent()
    javis.models.team_id = "unsupported"
    result = javis.handle("安排一个不支持的任务")
    assert result["data"]["pending_action"]["action"] == "clarify_team"
    assert "不要共享" not in json.dumps(javis.models.calls, ensure_ascii=False)
    assert not javis.rental.store.tasks(javis.sid)


def test_javis_interview_switch_creates_no_rental_task_until_confirmed(javis, consented):
    question = prepared_interview(consented)
    javis.consent()
    result = javis.handle("帮我找房")
    assert result["data"]["pending_action"]["action"] == "confirm_interview_pause"
    assert not javis.rental.store.tasks(javis.sid)
    javis.handle("/team-switch confirm")
    assert consented.interviews.current()["status"] == "paused"
    assert not javis.rental.store.tasks(javis.sid)
    resumed = javis.handle("/career resume")
    assert resumed["data"]["token"] == question["token"]
    assert not consented.interviews.current()["answers"]


def test_rental_namespaces_cannot_bypass_active_interview(javis, consented):
    prepared_interview(consented)
    result = javis.handle('/rental requirements {"city":"北京"}')
    assert result["data"]["pending_action"]
    assert not javis.rental.summary()["requirements"]


def test_cross_domain_summary_requires_selected_job_and_explicit_reconfirmation(javis, consented):
    assert run(javis, "/share career-destination")["error"] == "selected_destination_required"
    state = consented.state()
    state["target"] = {"run_id": "selected-run", "job": {
        "id": "selected-job", "city": "北京", "location": "用户选定办公楼",
        "salary": "private", "description": "not-shared",
    }}
    consented.store.save(consented.sid, state)
    javis.handle('/rental requirements {"city":"北京","rent_max":3000,'
                 '"rent_type":"entire","move_in":"2026-12-01"}')
    javis.handle("/rental confirm 1")
    proposal = javis.handle("/share career-destination")
    text = json.dumps(proposal, ensure_ascii=False)
    assert "private" not in text and "not-shared" not in text
    assert not javis.rental.summary()["requirements"]["destination"]
    javis.handle("/share confirm")
    assert javis.rental.summary()["requirements"]["destination"] == "用户选定办公楼"
    assert javis.rental.summary()["confirmed_version"] is None
    assert javis.rental.search()["status"] == "blocked"
    assert javis.store.tasks(javis.sid)[0]["dependencies"]


def test_changed_selected_job_invalidates_pending_share(javis, consented):
    state = consented.state()
    state["target"] = {"run_id": "r", "job": {"id": "j", "location": "地点1"}}
    consented.store.save(consented.sid, state)
    javis.handle("/share career-destination")
    state["target"]["job"]["location"] = "地点2"
    consented.store.save(consented.sid, state)
    assert run(javis, "/share confirm")["error"] == "share_scope_changed"


def test_waiting_task_can_be_cancelled_not_reexecuted(javis):
    javis.store.task(javis.sid, {"task_id": "pending", "progress": "waiting_user"})
    result = javis.handle("/javis task pending cancel")
    assert result["data"]["progress"] == "cancelled"
    assert run(javis, "/javis task pending pause")["error"] == "task_already_terminal"


def test_no_team_imports_controller_and_no_neutral_module_imports_teams():
    root = Path(__file__).resolve().parents[2]
    for folder in ("agents/Career-Team/src", "agents/Rental-Team/src", "tools/src/javis_core"):
        for path in (root / folder).rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    assert not (node.module or "").startswith("brain_leader"), path
                    if folder.endswith("javis_core"):
                        assert not (node.module or "").startswith(
                            ("career_team", "rental_team", "job_agent"),
                        ), path


def test_real_terminal_binary_offline_gates_persist_across_processes(tmp_path):
    """Real CLI subprocesses, offline boundaries; NOT live provider acceptance."""
    root = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "BL_DATA_DIR": str(tmp_path / "brain"), "BL_MEMORY_DIR": str(tmp_path / "memory"),
        "RENTAL_DATA_DIR": str(tmp_path / "rental"),
        "RENTAL_QUOTA_PATH": str(tmp_path / "quota.sqlite3"),
        "RENTAL_CREDENTIALS_FILE": str(tmp_path / "absent"),
        "RENTAL_VIGO_API_KEY": "", "RENTAL_ZIROOM_PYTHON": str(tmp_path / "absent-python"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    binary = Path(sys.executable).parent / "javis"

    def invoke(question):
        process = subprocess.run(
            [str(binary), "chat", "-s", "offline-terminal", "-q", question, "--json"],
            cwd=root, env=env, capture_output=True, text=True, timeout=30,
        )
        return process, json.loads(process.stdout)

    process, result = invoke('/rental requirements {"city":"北京","rent_max":3000,'
                             '"rent_type":"entire","move_in":"2026-12-01"}')
    assert process.returncode == 0
    assert result["data"]["version"] == 1
    process, result = invoke("/rental search")
    assert process.returncode == 1
    assert result["pending_action"]["action"] == "confirm_requirements"
    assert invoke("/rental confirm 1")[0].returncode == 0
    process, result = invoke("/rental search")
    assert process.returncode == 1
    assert result["status"] == "blocked"
    assert result["coverage"]["vigolive"]["error"] == "missing_key"
    assert result["coverage"]["ziroom"]["error"] == "not_configured"
