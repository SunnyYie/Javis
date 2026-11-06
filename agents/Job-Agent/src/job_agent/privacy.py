import re

PHONE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
ID_NUMBER = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")


def redact(text: str) -> str:
    text = ID_NUMBER.sub("[证件已隐藏]", text)
    text = PHONE.sub("[电话已隐藏]", text)
    text = EMAIL.sub("[邮箱已隐藏]", text)
    text = re.sub(
        r"(?m)^(\s*)(?:姓名|联系人|微信|住址|通讯地址)\s*[:：].*$",
        r"\1[身份信息已隐藏]",
        text,
    )
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]{2,4}", line.strip()):
            lines[i] = "[姓名已隐藏]"
        else:
            lines[i] = re.sub(
                r"^(\s*)[\u4e00-\u9fff]{2,4}(?=前端|后端|全栈|软件|工程师|求职)",
                r"\1[姓名已隐藏] ",
                line,
            )
        break
    return "\n".join(lines)
