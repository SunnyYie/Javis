"""Card 2.0: one question focus, per-point sources, at most three related articles."""

import html
import re
from urllib.parse import quote

QUESTION_NUMBER = re.compile(
    r"(?<![^\s:：。？?！!；;])(?:\((?P<ascii>[1-9]\d?)\)|（(?P<wide>[1-9]\d?)）)"
    r"|^[ \t]*(?P<line>[1-9]\d?)[.、][ \t]+",
    re.MULTILINE,
)


def escape(text):
    return "".join(
        f"&#{ord(char)};" if char in "*_~[]()#`|" else html.escape(char, quote=True)
        for char in text
    )


def link(label, url):
    return f"[{escape(label)}]({quote(url, safe='/:#%=&.-_~@')})"


def md(content, size="normal"):
    return {"tag": "markdown", "content": content, "text_size": size}


def block(elements, *, highlight=False):
    column = {
        "tag": "column", "width": "weighted", "weight": 1,
        "padding": "12px", "vertical_spacing": "8px", "elements": elements,
    }
    if highlight:
        column["background_style"] = "blue-50"
    return {"tag": "column_set", "flex_mode": "none", "columns": [column]}


def question_elements(text):
    elements = [md("<font color='grey'>题目</font>", "caption")]
    markers = list(QUESTION_NUMBER.finditer(text))
    # Only a complete numbered sequence is a list; preserve ambiguous prose unchanged.
    if (
        len(markers) < 2
        or [int(match.group(match.lastgroup)) for match in markers]
        != list(range(1, len(markers) + 1))
    ):
        return elements + [md("**" + escape(text) + "**", "question")]
    parts = [
        text[match.end():markers[i + 1].start() if i + 1 < len(markers) else len(text)].strip()
        for i, match in enumerate(markers)
    ]
    if not all(parts):
        return elements + [md("**" + escape(text) + "**", "question")]
    stem = text[:markers[0].start()].strip()
    if stem:
        elements.append(md("**" + escape(stem) + "**", "question"))
    for marker, part in zip(markers, parts, strict=True):
        elements.append(md("**" + escape(marker.group().strip()) + "** " + escape(part)))
    return elements


def render(record):
    sources = {source["id"]: source for source in record["sources"]}
    ids = list(dict.fromkeys(eid for point in record["answer"] for eid in point["evidence_ids"]))
    numbers = {eid: i for i, eid in enumerate(ids, 1)}
    answer = [md("**答案**")]
    for index, point in enumerate(record["answer"], 1):
        answer.append(md(f"**{index}.** {escape(point['text'])}"))
        refs = []
        for eid in dict.fromkeys(point["evidence_ids"]):
            source = sources[eid]
            label = source.get("path") or source.get("repo") or "参考资料"
            fragment = source["url"].split("#", 1)[-1] if "#" in source["url"] else ""
            if fragment:
                label += " · " + fragment
            refs.append(link(f"[{numbers[eid]}] {label}", source["url"]))
        answer.append(md("来源：" + "　".join(refs), "caption"))
    elements = [
        block(question_elements(record["question"]), highlight=True),
        block(answer),
    ]
    if record.get("readings"):
        reading = [md("**延伸阅读**")]
        for item in record["readings"][:3]:
            reading.append(md(link(item["title"], item["url"])))
            reading.append(md("<font color='grey'>" + escape(item["reason"]) + "</font>", "caption"))
        elements.append(block(reading))
    return {
        "schema": "2.0",
        "config": {
            "update_multi": True, "width_mode": "default",
            "style": {"text_size": {
                "question": {"default": "heading-3", "pc": "heading-3", "mobile": "heading-3"},
                "caption": {"default": "notation", "pc": "notation", "mobile": "notation"},
            }},
        },
        "header": {
            "title": {"tag": "plain_text", "content": "AI Agent 每日一题"},
            "subtitle": {"tag": "plain_text", "content": record["day"] + " · " + record["field"]},
            "template": "blue", "icon": {"tag": "standard_icon", "token": "myai_colorful"},
        },
        "body": {
            "direction": "vertical", "padding": "12px", "vertical_spacing": "12px",
            "elements": elements,
        },
    }
