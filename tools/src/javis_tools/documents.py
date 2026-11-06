import html
import io
import os
import uuid
from pathlib import Path

from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.opc.packuri import PackURI
from docx.opc.part import Part
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from fontTools import subset
from fontTools.ttLib import TTFont as SourceFont
from lxml import etree
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer


class DocumentError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def font_source(path=None, index=0):
    if path:
        candidates = [(Path(path), index)]
    else:
        candidates = [
            (Path("/System/Library/Fonts/STHeiti Light.ttc"), 1),
            (Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/simsun.ttc", 0),
            (Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"), 0),
            (Path("/usr/share/fonts/truetype/arphic/uming.ttc"), 0),
        ]
    for candidate, offset in candidates:
        if candidate.is_file():
            return candidate, offset
    raise DocumentError("chinese_font_required")


def embedded_font(doc, path, index, text):
    font = SourceFont(str(path), fontNumber=index)
    try:
        if font["OS/2"].fsType & (0x0002 | 0x0100 | 0x0200):
            raise DocumentError("font_embedding_or_subsetting_restricted")
        cmap = font.getBestCmap()
        if any(ord(char) not in cmap for char in text if not char.isspace()):
            raise DocumentError("font_missing_glyphs")
        family = font["name"].getDebugName(1)
        options = subset.Options()
        options.drop_tables += ["feat", "meta", "morx"]
        subsetter = subset.Subsetter(options=options)
        subsetter.populate(text=text)
        subsetter.subset(font)
        buffer = io.BytesIO()
        font.save(buffer)
    finally:
        font.close()
    key = uuid.uuid4()
    mask = key.bytes[::-1]
    data = bytearray(buffer.getvalue())
    for i in range(32):
        data[i] ^= mask[i % 16]
    table = doc.part.part_related_by(RT.FONT_TABLE)
    part = Part(
        PackURI("/word/fonts/brain-leader.odttf"),
        "application/vnd.openxmlformats-officedocument.obfuscatedFont",
        bytes(data), doc.part.package,
    )
    rel = table.relate_to(part, RT.FONT)
    element = parse_xml(table.blob)
    entry = OxmlElement("w:font")
    entry.set(qn("w:name"), family)
    embed = OxmlElement("w:embedRegular")
    embed.set(qn("r:id"), rel)
    embed.set(qn("w:fontKey"), "{" + str(key).upper() + "}")
    entry.append(embed)
    element.append(entry)
    table._blob = etree.tostring(element, xml_declaration=True, encoding="UTF-8", standalone=True)
    for setting in ("embedTrueTypeFonts", "saveSubsetFonts"):
        flag = OxmlElement("w:" + setting)
        doc.settings.element.append(flag)
    return family


def render_resume(
    draft: dict, contacts: dict, directory: Path, preview=False, font_path=None, font_index=0,
) -> list[dict]:
    """All formats consume exactly the same ordered text, without another LLM call."""
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    title = contacts.get("name") or "个人简历"
    contact = " | ".join(contacts[k] for k in ("phone", "email", "city", "link") if contacts.get(k))
    blocks = [(0, title)]
    if preview:
        blocks.append((0, "草稿预览 未采纳"))
    if contact:
        blocks.append((0, contact))
    for section in draft["sections"]:
        blocks.append((1, section["heading"]))
        blocks.extend((2, line["text"]) for line in section["lines"])
    font_path, font_index = font_source(font_path, font_index)
    stem = draft["id"] + ("-preview" if preview else "")
    md = directory / f"{stem}.md"
    md.touch(mode=0o600, exist_ok=False)
    md.write_text("\n\n".join(
        ("## " if kind == 1 else "- " if kind == 2 else "") + text
        for kind, text in blocks
    ) + "\n", encoding="utf-8")

    doc = Document()
    family = embedded_font(doc, font_path, font_index, "\n".join(t for _, t in blocks))
    for border in doc.styles.element.xpath(".//w:pBdr"):
        border.getparent().remove(border)
    section = doc.sections[0]
    section.page_width, section.page_height = Inches(8.5), Inches(11)
    section.top_margin = section.bottom_margin = Inches(0.65)
    section.left_margin = section.right_margin = Inches(0.75)
    for name in ("Normal", "Title", "Heading 1"):
        style = doc.styles[name]
        style.font.name = family
        style.font.size = Pt(11 if name == "Normal" else 14 if name == "Heading 1" else 20)
        style.font.color.rgb = RGBColor(0, 0, 0)
        rpr = style.element.get_or_add_rPr()
        fonts = rpr.find(qn("w:rFonts"))
        if fonts is None:
            fonts = OxmlElement("w:rFonts")
            rpr.insert(0, fonts)
        for attribute in ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme"):
            fonts.attrib.pop(qn("w:" + attribute), None)
        for attribute in ("ascii", "hAnsi", "eastAsia", "cs"):
            fonts.set(qn("w:" + attribute), family)
    for i, (kind, text) in enumerate(blocks):
        paragraph = doc.add_paragraph(
            text, style="Title" if i == 0 else "Heading 1" if kind == 1 else "Normal",
        )
        paragraph.paragraph_format.space_after = Pt(6)
        if kind == 1:
            paragraph.paragraph_format.keep_with_next = True
        elif kind == 2:
            paragraph.paragraph_format.keep_together = True
    docx = directory / f"{stem}.docx"
    docx.touch(mode=0o600, exist_ok=False)
    doc.save(docx)

    pdfmetrics.registerFont(TTFont("BrainLeaderCJK", str(font_path), subfontIndex=font_index))
    normal = ParagraphStyle(
        "resume", fontName="BrainLeaderCJK", fontSize=11, leading=16, wordWrap="CJK",
        textColor=colors.black, alignment=TA_LEFT, spaceAfter=6,
    )
    heading = ParagraphStyle(
        "heading", parent=normal, fontSize=14, leading=20, spaceBefore=8, keepWithNext=True,
    )
    title_style = ParagraphStyle("title", parent=normal, fontSize=20, leading=26, spaceAfter=10)
    flow = []
    for i, (kind, text) in enumerate(blocks):
        paragraph = Paragraph(
            html.escape(text).replace("\n", "<br/>"),
            title_style if i == 0 else heading if kind == 1 else normal,
        )
        flow.append(KeepTogether([paragraph]) if kind == 2 else paragraph)
        if kind == 1:
            flow.append(Spacer(1, 2))
    pdf = directory / f"{stem}.pdf"
    pdf.touch(mode=0o600, exist_ok=False)
    SimpleDocTemplate(
        str(pdf), pagesize=(612, 792), topMargin=46.8, bottomMargin=46.8,
        leftMargin=54, rightMargin=54,
    ).build(flow)
    result = []
    for path in (md, docx, pdf):
        os.chmod(path, 0o600)
        result.append({"path": str(path.resolve()), "format": path.suffix[1:]})
    return result
