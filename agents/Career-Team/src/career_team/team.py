from javis_core.models import Capability, TaskResult

from .agent import chat_turn
from .service import Service

CAPABILITY = Capability(
    team_id="career", description="职业事实、简历、岗位、备考、面试与复盘",
    input_constraints=["职业数据仅在职业域内读取", "面试未结束不展示答案"],
    authorization=["远程模型授权", "文件、事实、记忆和导出由宿主确认"],
)


class CareerTeam:
    def __init__(self, settings, sid, *, service=None):
        self.settings, self.sid = settings, sid
        self._service = service

    @property
    def service(self):
        if self._service is None:
            self._service = Service(self.settings, self.sid)
        return self._service

    def status(self):
        interview = self.service.interviews.current()
        return {
            "team_id": "career", "session_id": self.sid,
            "interview": self.service.interviews.public(interview),
        }

    def execute(self, request):
        data = chat_turn(self.service, request.goal)
        operations = data.get("operations", [])
        status = "ok"
        if any(operation["status"] != "ok" for operation in operations):
            status = "partial" if any(o["status"] == "ok" for o in operations) else "failed"
        return TaskResult(task_id=request.task_id, status=status, data=data)

    def command(self, text):
        from .commands import command

        return command(self.service, text)

    def selected_destination(self):
        target = self.service.state().get("target")
        if not target:
            return None
        job = target["job"]
        # This is a proposal, never an automatically confirmed office location.
        return {
            "destination": job.get("location", ""), "city": job.get("city", ""),
            "reference": {"job_id": job["id"], "run_id": target["run_id"]},
        }

    def close(self):
        if self._service is not None:
            self._service.close()
