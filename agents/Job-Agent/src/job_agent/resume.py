from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path

from .models import (
    AgentError,
    ExtractedProfile,
    ResumePage,
    ResumeProfile,
    WorkExperience,
    digest,
    now,
)
from .privacy import redact

PARSER_VERSION = "resume-v2"
MAX_BYTES = 10 * 1024 * 1024
MAX_PAGES = 20


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def months_worked(work: list[WorkExperience], as_of: date) -> int:
    current = as_of.year * 12 + as_of.month - 1
    intervals = []
    for item in work:
        if not item.start or not item.end:
            continue
        sy, sm = map(int, item.start.split("-"))
        start = sy * 12 + sm - 1
        if item.end == "present":
            end = current
        else:
            ey, em = map(int, item.end.split("-"))
            end = min(ey * 12 + em, current)
        if start < end:
            intervals.append((start, end))
    merged: list[list[int]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return sum(end - start for start, end in merged)


def useful_text(text: str) -> bool:
    valid = len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", text))
    return valid >= 40 and text.count("\ufffd") < max(2, len(text) * 0.01)


class ResumeParser:
    def __init__(self, settings, cache, models):
        self.settings, self.cache, self.models = settings, cache, models
        self._ocr = None

    def ocr(self, image, page: int) -> ResumePage:
        import numpy as np
        import onnxruntime
        from rapidocr import RapidOCR

        if self._ocr is None:
            onnxruntime.disable_telemetry_events()
            self._ocr = RapidOCR(params={
                "Global.model_root_dir": str(self.settings.data_dir / "models" / "rapidocr"),
            })
        result = self._ocr(np.asarray(image.convert("RGB")))
        texts = list(result.txts or [])
        scores = [float(s) for s in (result.scores or [])]
        boxes = result.boxes.tolist() if result.boxes is not None else []
        text = "\n".join(texts)
        if not useful_text(text):
            raise AgentError("ocr_empty", f"第 {page} 页未识别出足够的简历文字")
        return ResumePage(
            page=page, method="ocr", text=text,
            confidence=sum(scores) / len(scores) if scores else None,
            blocks=[
                {"text": t, "confidence": scores[i] if i < len(scores) else None,
                 "bbox": boxes[i] if i < len(boxes) else None}
                for i, t in enumerate(texts)
            ],
        )

    def extract(self, path: Path, force_ocr: bool = False) -> list[ResumePage]:
        import pdfplumber
        import pypdfium2 as pdfium
        from PIL import Image, ImageOps

        path = path.expanduser().resolve()
        if not path.is_file() or not (0 < path.stat().st_size <= MAX_BYTES):
            raise AgentError("invalid_resume", "简历不存在、为空或超过 10 MiB")
        suffix = path.suffix.lower()
        key = self.cache.key("pages", {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "force_ocr": force_ocr, "parser": PARSER_VERSION,
        })
        cached = self.cache.get(key)
        if cached:
            return [ResumePage.model_validate(p) for p in cached]
        try:
            if suffix in (".txt", ".md"):
                if force_ocr:
                    raise AgentError("invalid_ocr_input", "强制 OCR 仅支持 PDF 和图片")
                text = path.read_text(encoding="utf-8")
                if not useful_text(text):
                    raise AgentError("resume_empty", "简历文字不足")
                pages = [ResumePage(page=1, method="text", text=text)]
            elif suffix in (".png", ".jpg", ".jpeg"):
                with Image.open(path) as image:
                    if image.width * image.height > 40_000_000:
                        raise AgentError("image_too_large", "图片超过 4000 万像素")
                    pages = [self.ocr(ImageOps.exif_transpose(image), 1)]
            elif suffix == ".pdf":
                pages = []
                with pdfplumber.open(path) as pdf, pdfium.PdfDocument(path) as rendered:
                    if not 0 < len(pdf.pages) <= MAX_PAGES:
                        raise AgentError("page_limit", "简历限 1–20 页")
                    for i, page in enumerate(pdf.pages):
                        text = page.extract_text(layout=True) or ""
                        if not force_ocr and useful_text(text):
                            words = page.extract_words()
                            pages.append(ResumePage(
                                page=i + 1, method="text", text=text,
                                blocks=[
                                    {"text": w["text"],
                                     "bbox": [w["x0"], w["top"], w["x1"], w["bottom"]]}
                                    for w in words
                                ],
                            ))
                        else:
                            bitmap = rendered[i].render(scale=3)
                            try:
                                pages.append(self.ocr(bitmap.to_pil(), i + 1))
                            finally:
                                bitmap.close()
            else:
                raise AgentError("unsupported_resume", "支持 PDF、PNG、JPEG、TXT、Markdown")
        except AgentError:
            raise
        except Exception:
            raise AgentError("resume_parse_failed", "简历读取或 OCR 失败，请检查文件和模型") from None
        if sum(len(p.text) for p in pages) > 100_000:
            raise AgentError("resume_too_long", "提取文本超过 10 万字符")
        self.cache.set(key, [p.model_dump() for p in pages])
        return pages

    def parse(self, path: Path, force_ocr: bool = False) -> ResumeProfile:
        pages = self.extract(path, force_ocr)
        safe_pages = [
            {"page": p.page, "text": redact(p.text), "method": p.method} for p in pages
        ]
        key = self.cache.key("profile", {
            "pages": safe_pages, "model": self.models.identity(), "parser": PARSER_VERSION,
        })
        cached = self.cache.get(key)
        if cached:
            extracted = ExtractedProfile.model_validate(cached)
        else:
            extracted = self.models.structured(
                ExtractedProfile,
                """提取简历的职业信息，不提取姓名、电话、邮箱、住址。
技能和实体必须出现在原文；summary 只概括可证实的能力，不评价为精通或专家。
每个工作、项目、教育实体提供包含该实体事实的原文 quote 和页码。
不要把教育或项目条目当工作经历。日期为 YYYY-MM，至今为 present，未知为空。
仅列出原文工作经历。不能根据项目技术推测额外技能。
以 as_of 为当前日期，不能无依据猜测年份笔误或 OCR 把其他年份识别为当前年份。
uncertainties 只记录缺失字段、实际矛盾或模糊识别，不对所有简历自述重复质疑。
quote 逐字引用，不加省略号、不改写；尽量引用含公司/学校/日期的完整行。""",
                json.dumps({"as_of": now()[:10], "pages": safe_pages}, ensure_ascii=False),
            )
        safe_text = "\n".join(p["text"] for p in safe_pages)
        for item in [*extracted.work, *extracted.projects, *extracted.education]:
            page = next((p["text"] for p in safe_pages if p["page"] == item.evidence.page), "")
            if compact(item.evidence.quote) not in compact(page):
                raise AgentError("ungrounded_resume", "简历实体引用未通过原文校验，请重新解析")
        original_skills = extracted.skills
        extracted.skills = [s for s in original_skills if compact(s) in compact(safe_text)]
        if len(original_skills) != len(extracted.skills):
            extracted.uncertainties.append("部分技能未能在原文中定位，已从技能清单移除")
        for work in extracted.work:
            quoted = re.sub(r"[./年]", "-", compact(work.evidence.quote))
            for field in ("start", "end"):
                value = getattr(work, field)
                if value == "present":
                    valid = any(t in quoted for t in ("至今", "现在", "present", "current"))
                else:
                    valid = not value or value in quoted
                if not valid:
                    setattr(work, field, "")
                    extracted.uncertainties.append(f"{work.company} 的工作日期待确认")
        for p in pages:
            if p.confidence is not None and p.confidence < 0.9:
                extracted.uncertainties.append(f"第 {p.page} 页 OCR 平均置信度低于 0.9，请核对")
        extracted.uncertainties = list(dict.fromkeys(extracted.uncertainties))
        self.cache.set(key, extracted.model_dump())
        as_of = date.fromisoformat(now()[:10])
        return ResumeProfile(
            **extracted.model_dump(),
            id=digest({"pages": safe_pages, "parser": PARSER_VERSION}),
            source_file=str(path.expanduser().resolve()),
            pages=pages, work_months=months_worked(extracted.work, as_of),
            as_of=as_of.isoformat(), parser_version=PARSER_VERSION,
        )
