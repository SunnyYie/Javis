import json
import shlex

from .agent import chat_turn, confirm_switch
from .models import BrainError


def command(service, text, help_text=""):
    if not text.startswith("/"):
        return chat_turn(service, text)
    parts = shlex.split(text)
    name, args = parts[0], parts[1:]
    if service.state().get("pending_team_switch") and name == "/answer":
        raise BrainError("switch_confirmation_pending", "请先确认或拒绝暂停面试")
    interview = service.interviews.current()
    if interview and interview["status"] != "finished" and name not in {
        "/answer", "/pause", "/continue", "/finish", "/status", "/help", "/quit", "/team-switch",
    }:
        raise BrainError("interview_in_progress", "面试期间请先作答、暂停、恢复或结束")
    if name == "/help":
        return {"help": help_text}
    if name == "/team-switch" and args in (["confirm"], ["reject"]):
        return confirm_switch(service, args[0] == "confirm")
    if name == "/consent":
        return service.consent()
    if name == "/status":
        return service.snapshot(presented=True)
    if name == "/confirm":
        service.adapters.job.confirm()
        return service.adapters.job.snapshot()
    if name == "/resume" and args:
        return service.import_resume(args[0], "--force-ocr" in args)
    if name == "/import" and args:
        approved = service.authorize(args[0], "jobs")
        return service.adapters.job.import_file(str(approved))
    if name == "/prefs" and args:
        return service.preferences(**json.loads(text.split(maxsplit=1)[1]))
    if name == "/contact" and args:
        contacts = json.loads(text.split(maxsplit=1)[1])
        if not isinstance(contacts, dict) or not set(contacts) <= {
            "name", "phone", "email", "city", "link",
        } or any(not isinstance(v, str) or len(v) > 300 for v in contacts.values()):
            raise BrainError("invalid_contacts")
        state = service.state()
        state["contacts"] = contacts
        service.store.save(service.sid, state)
        return {"contacts_saved_locally": list(contacts)}
    if name == "/facts":
        if not args:
            return {"facts": service.store.records(service.sid, "fact")}
        if args[0] == "add" and len(args) >= 3:
            return service.resumes.add_fact(
                args[1], " ".join(args[2:]), confirmed=True, source={"type": "explicit_user_command"},
            )
        if args[0] == "confirm" and len(args) == 2:
            return service.resumes.confirm(args[1])
        if args[0] == "reject" and len(args) == 2:
            fact = service.store.get(service.sid, args[1], "fact")
            if fact["confirmed"]:
                raise BrainError("confirmed_fact_immutable", "已确认事实请新增修订，不覆盖旧版本")
            fact["rejected"] = True
            return service.store.put(
                service.sid, "fact", fact, record_id=fact["id"], mutable=True,
            )
    if name == "/draft":
        return service.resumes.generate()
    if name == "/adopt" and len(args) == 1:
        return service.resumes.adopt(args[0])
    if name == "/search":
        service.adapters.job.collection_used = False
        return service.adapters.job.search()
    if name == "/match":
        return service.adapters.job.match(args[0] if args else None)
    if name == "/jobs":
        return service.list_jobs()
    if name == "/detail" and len(args) == 1:
        return service.adapters.job.detail(args[0])
    if name == "/show" and len(args) == 1:
        return service.record_detail(args[0])
    if name == "/target" and args:
        return service.select_job(args[0], args[1] if len(args) > 1 else None)
    if name == "/questions" and args:
        count = 5
        if "--count" in args:
            index = args.index("--count")
            count = int(args[index + 1])
            args = args[:index] + args[index + 2:]
        return service.questions(" ".join(args), count)
    if name == "/knowledge" and args:
        return service.explain(" ".join(args))
    if name == "/links":
        return {"links": service.store.links(service.sid, " ".join(args))}
    if name == "/learn":
        return service.learning_plan(" ".join(args))
    if name == "/interview":
        return service.interviews.prepare(
            args[0] if args else "mixed", int(args[1]) if len(args) > 1 else 5,
            int(args[2]) if len(args) > 2 else 2,
        )
    if name == "/answer" and len(args) >= 2:
        return service.interviews.answer(args[0], " ".join(args[1:]))
    if name in ("/pause", "/continue", "/finish"):
        result = service.interviews.control(
            {"/pause": "pause", "/continue": "resume", "/finish": "finish"}[name],
        )
        if name == "/finish":
            state = service.state()
            state["pending_team_switch"] = None
            service.store.save(service.sid, state)
        return result
    if name == "/transcript" and len(args) == 1:
        record = service.authorize(args[0], "transcript")
        return {"id": record["id"], "imported": True}
    if name == "/review":
        return service.review(args[0] if args else None)
    if name == "/memory":
        if not args:
            return {"confirmed": service.memory.all(), "candidates": service.state()["candidate_memory"]}
        if args[0] == "add" and len(args) >= 3:
            return service.propose_memory(args[1], " ".join(args[2:]))
        if args[0] == "confirm" and len(args) == 2:
            return service.confirm_memory(args[1])
        if args[0] in ("delete", "correct") and len(args) >= 2:
            if args[0] == "correct" and len(args) < 3:
                raise BrainError("missing_correction")
            all_memories = service.memory.all() + service.state()["candidate_memory"]
            item = next((m for m in all_memories if m["id"] == args[1]), None)
            if item is None:
                raise BrainError("unknown_memory")
            service.memory.delete(args[1])
            state = service.state()
            state["candidate_memory"] = [m for m in state["candidate_memory"] if m["id"] != args[1]]
            service.store.save(service.sid, state)
            if args[0] == "correct":
                return service.propose_memory(item["kind"], " ".join(args[2:]))
            return {"deleted": args[1]}
    if name == "/tasks":
        return {"tasks": service.state()["tasks"]}
    if name == "/done" and len(args) == 1:
        state = service.state()
        task = next((t for t in state["tasks"] if t["id"] == args[0]), None)
        if not task:
            raise BrainError("unknown_task")
        task["status"] = "done"
        service.store.save(service.sid, state)
        return task
    if name == "/export" and args:
        if args[0] == "resume":
            draft_id = next((a for a in args[1:] if a != "preview"), None)
            return {"artifacts": service.resumes.export(draft_id, "preview" in args)}
        if args[0] == "jobs":
            return {"artifacts": [{"path": service.adapters.job.export(args[1] if len(args) > 1 else "json")}]}
        if args[0] == "review":
            review = service.review(args[1] if len(args) > 1 else None)
            directory = service.directory / "exports"
            directory.mkdir(exist_ok=True, mode=0o700)
            path = directory / f"{review['id']}.json"
            service.write_private(path, json.dumps(review, ensure_ascii=False, indent=2))
            md = path.with_suffix(".md")
            service.write_private(md, "# 面试复盘\n\n" + "\n\n".join(
                f"## {key}\n\n{json.dumps(value, ensure_ascii=False, indent=2)}"
                for key, value in review.items()
            ))
            return {"artifacts": [{"path": str(path)}, {"path": str(md)}]}
    raise BrainError("invalid_command", "命令或参数无效；/help 查看格式")
