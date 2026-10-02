"""
Shared builder for the NMHS-HMIS-V1.0 manuals: fonts, styles, page furniture,
table of contents, and a tiny content vocabulary so the documents read as one
family. Requires `reportlab` and the DejaVu fonts (see README.md here).

Content is a list of tuples:
    ("h1", text) ("h2", text) ("h3", text)
    ("p", text)                      plain paragraph; **bold** and `code` allowed
    ("steps", [text, ...])           numbered procedure
    ("bullets", [text, ...])
    ("table", [header...], [[cell...], ...], [width fractions] or None)
    ("note", kind, title, text)      kind: info | warn | danger | tip
    ("code", text)
    ("flow", [label, ...], cols)     boxes joined by arrows
    ("arch", rows)                   layered diagram, rows of labels
    ("pagebreak",)
    ("spacer", points)
"""
import re
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, CondPageBreak, Flowable, Frame,
                                NextPageTemplate, PageBreak, PageTemplate, Paragraph,
                                Preformatted, Spacer, Table, TableStyle)
from reportlab.platypus.tableofcontents import TableOfContents

FONT_DIR = "/usr/share/fonts/truetype/dejavu/"
pdfmetrics.registerFont(TTFont("Sans", FONT_DIR + "DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("Sans-Bold", FONT_DIR + "DejaVuSans-Bold.ttf"))
pdfmetrics.registerFont(TTFont("Sans-Italic", FONT_DIR + "DejaVuSans-Oblique.ttf"))
pdfmetrics.registerFont(TTFont("Sans-BoldItalic", FONT_DIR + "DejaVuSans-BoldOblique.ttf"))
pdfmetrics.registerFont(TTFont("Mono", FONT_DIR + "DejaVuSansMono.ttf"))
pdfmetrics.registerFont(TTFont("Mono-Bold", FONT_DIR + "DejaVuSansMono-Bold.ttf"))
pdfmetrics.registerFontFamily("Sans", normal="Sans", bold="Sans-Bold",
                              italic="Sans-Italic", boldItalic="Sans-BoldItalic")

NAVY = colors.HexColor("#12355b")
TEAL = colors.HexColor("#0f766e")
INK = colors.HexColor("#1e293b")
MUTED = colors.HexColor("#475569")
RULE = colors.HexColor("#cbd5e1")
ZEBRA = colors.HexColor("#f1f5f9")
HEAD_BG = colors.HexColor("#e2e8f0")

CALLOUT = {
    "info": (colors.HexColor("#eff6ff"), colors.HexColor("#1d4ed8"), "NOTE"),
    "tip": (colors.HexColor("#ecfdf5"), colors.HexColor("#047857"), "TIP"),
    "warn": (colors.HexColor("#fffbeb"), colors.HexColor("#b45309"), "IMPORTANT"),
    "danger": (colors.HexColor("#fef2f2"), colors.HexColor("#b91c1c"), "WARNING"),
}

PAGE_W, PAGE_H = A4
MARGIN_X = 20 * mm
BODY_W = PAGE_W - 2 * MARGIN_X

S = {
    "body": ParagraphStyle("body", fontName="Sans", fontSize=9.6, leading=13.6,
                           textColor=INK, spaceAfter=5),
    "small": ParagraphStyle("small", fontName="Sans", fontSize=8.2, leading=11, textColor=MUTED),
    "cell": ParagraphStyle("cell", fontName="Sans", fontSize=8.4, leading=11, textColor=INK),
    "cellhead": ParagraphStyle("cellhead", fontName="Sans-Bold", fontSize=8.4, leading=11,
                               textColor=NAVY),
    "h1": ParagraphStyle("h1", fontName="Sans-Bold", fontSize=17, leading=21, textColor=NAVY,
                         spaceBefore=4, spaceAfter=10),
    "h2": ParagraphStyle("h2", fontName="Sans-Bold", fontSize=12.4, leading=16, textColor=TEAL,
                         spaceBefore=10, spaceAfter=5),
    "h3": ParagraphStyle("h3", fontName="Sans-Bold", fontSize=10.4, leading=14, textColor=INK,
                         spaceBefore=7, spaceAfter=3),
    "step": ParagraphStyle("step", fontName="Sans", fontSize=9.6, leading=13.4, textColor=INK),
    "code": ParagraphStyle("code", fontName="Mono", fontSize=7.9, leading=10.2, textColor=INK),
    "toc1": ParagraphStyle("toc1", fontName="Sans-Bold", fontSize=10, leading=15, textColor=NAVY,
                           leftIndent=0),
    "toc2": ParagraphStyle("toc2", fontName="Sans", fontSize=8.8, leading=12, textColor=INK,
                           leftIndent=14),
}


def md(text):
    """Escape, then allow **bold**, *italic* and `code`."""
    out = escape(text)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    out = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", out)
    out = re.sub(r"`(.+?)`", r'<font name="Mono" size="8.4" color="#0f3d63">\1</font>', out)
    return out


class Heading(Paragraph):
    """A heading that registers itself with the table of contents."""

    def __init__(self, text, level, style):
        super().__init__(md(text), style)
        self.toc_level = level
        self.toc_text = text


class Callout(Flowable):
    def __init__(self, kind, title, text):
        super().__init__()
        self.bg, self.edge, label = CALLOUT[kind]
        heading = f"{label}: {title}" if title else label
        self.head = Paragraph(md(heading), ParagraphStyle("ch", parent=S["body"],
                              fontName="Sans-Bold", textColor=self.edge, spaceAfter=2))
        self.text = Paragraph(md(text), ParagraphStyle("ct", parent=S["body"], spaceAfter=0))

    def wrap(self, aw, ah):
        self.aw = aw
        inner = aw - 18
        _, h1 = self.head.wrap(inner, ah)
        _, h2 = self.text.wrap(inner, ah)
        self.h1, self.h2 = h1, h2
        self.height = h1 + h2 + 16
        return aw, self.height

    def draw(self):
        c = self.canv
        c.setFillColor(self.bg)
        c.setStrokeColor(self.bg)
        c.rect(0, 0, self.aw, self.height, fill=1, stroke=0)
        c.setFillColor(self.edge)
        c.rect(0, 0, 3.2, self.height, fill=1, stroke=0)
        self.head.drawOn(c, 11, self.height - 7 - self.h1)
        self.text.drawOn(c, 11, 7)


class FlowDiagram(Flowable):
    """Boxes left to right, wrapping into rows, joined by arrows."""

    def __init__(self, labels, cols=4, box_h=34):
        super().__init__()
        self.labels, self.cols, self.box_h = labels, cols, box_h
        self.gap_x, self.gap_y = 18, 18

    def wrap(self, aw, ah):
        self.aw = aw
        rows = (len(self.labels) + self.cols - 1) // self.cols
        self.box_w = (aw - (self.cols - 1) * self.gap_x) / self.cols
        self.height = rows * self.box_h + (rows - 1) * self.gap_y + 6
        return aw, self.height

    def _pos(self, i):
        r, col = divmod(i, self.cols)
        x = col * (self.box_w + self.gap_x)
        y = self.height - 3 - (r + 1) * self.box_h - r * self.gap_y
        return x, y

    def draw(self):
        c = self.canv
        style = ParagraphStyle("fb", fontName="Sans", fontSize=7.8, leading=9.4,
                               textColor=INK, alignment=TA_CENTER)
        for i, label in enumerate(self.labels):
            x, y = self._pos(i)
            first = i == 0
            last = i == len(self.labels) - 1
            c.setFillColor(colors.HexColor("#e0f2f1") if (first or last) else colors.white)
            c.setStrokeColor(TEAL)
            c.setLineWidth(0.8)
            c.roundRect(x, y, self.box_w, self.box_h, 4, fill=1, stroke=1)
            p = Paragraph(md(label), style)
            _, ph = p.wrap(self.box_w - 6, self.box_h)
            p.drawOn(c, x + 3, y + (self.box_h - ph) / 2)
            if not last:
                nx, ny = self._pos(i + 1)
                c.setStrokeColor(TEAL)
                c.setFillColor(TEAL)
                if ny == y:
                    x1, x2, ym = x + self.box_w, nx, y + self.box_h / 2
                    c.line(x1 + 1, ym, x2 - 4, ym)
                    path = c.beginPath()
                    path.moveTo(x2 - 1, ym); path.lineTo(x2 - 6, ym + 3); path.lineTo(x2 - 6, ym - 3)
                    path.close(); c.drawPath(path, fill=1, stroke=0)
                else:  # wrap to the next row: down from the box, then to the next row's first box
                    xm = x + self.box_w / 2
                    c.line(xm, y - 1, xm, y - self.gap_y / 2)
                    nxm = nx + self.box_w / 2
                    c.line(xm, y - self.gap_y / 2, nxm, y - self.gap_y / 2)
                    c.line(nxm, y - self.gap_y / 2, nxm, ny + self.box_h + 4)
                    path = c.beginPath()
                    top = ny + self.box_h
                    path.moveTo(nxm, top + 1); path.lineTo(nxm - 3, top + 6); path.lineTo(nxm + 3, top + 6)
                    path.close(); c.drawPath(path, fill=1, stroke=0)


class LayerDiagram(Flowable):
    """Rows of boxes, top to bottom, with a down-arrow between rows."""

    def __init__(self, rows, row_h=30):
        super().__init__()
        self.rows, self.row_h, self.gap = rows, row_h, 16

    def wrap(self, aw, ah):
        self.aw = aw
        self.height = len(self.rows) * self.row_h + (len(self.rows) - 1) * self.gap + 4
        return aw, self.height

    def draw(self):
        c = self.canv
        style = ParagraphStyle("lb", fontName="Sans", fontSize=7.8, leading=9.4, textColor=INK,
                               alignment=TA_CENTER)
        y = self.height - 2
        for r, row in enumerate(self.rows):
            y -= self.row_h
            n = len(row)
            gap = 10
            w = (self.aw - (n - 1) * gap) / n
            for i, label in enumerate(row):
                x = i * (w + gap)
                dashed = label.startswith("~")
                text = label.lstrip("~")
                c.setFillColor(colors.HexColor("#f8fafc") if dashed else colors.HexColor("#e0f2f1"))
                c.setStrokeColor(MUTED if dashed else TEAL)
                c.setDash(3, 2) if dashed else c.setDash()
                c.roundRect(x, y, w, self.row_h, 4, fill=1, stroke=1)
                c.setDash()
                p = Paragraph(md(text), style)
                _, ph = p.wrap(w - 6, self.row_h)
                p.drawOn(c, x + 3, y + (self.row_h - ph) / 2)
            if r < len(self.rows) - 1:
                c.setStrokeColor(TEAL); c.setFillColor(TEAL)
                xm = self.aw / 2
                c.line(xm, y - 1, xm, y - self.gap + 5)
                path = c.beginPath()
                path.moveTo(xm, y - self.gap + 1); path.lineTo(xm - 3, y - self.gap + 6)
                path.lineTo(xm + 3, y - self.gap + 6); path.close(); c.drawPath(path, fill=1, stroke=0)
                y -= self.gap


def make_table(header, rows, widths=None):
    data = [[Paragraph(md(h), S["cellhead"]) for h in header]]
    for row in rows:
        data.append([Paragraph(md(str(cell)), S["cell"]) for cell in row])
    n = len(header)
    widths = widths or [1.0 / n] * n
    col_w = [BODY_W * f for f in widths]
    t = Table(data, colWidths=col_w, repeatRows=1, hAlign="LEFT")
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), HEAD_BG),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, NAVY),
        ("GRID", (0, 0), (-1, -1), 0.3, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4.5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4.5),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), ZEBRA))
    t.setStyle(TableStyle(style))
    return t


def numbered(items, style_key="step", bullet=False):
    out = []
    for i, text in enumerate(items, 1):
        mark = "•" if bullet else f"{i}."
        cell = Table([[Paragraph(f'<font name="Sans-Bold" color="#0f766e">{mark}</font>', S["step"]),
                       Paragraph(md(text), S[style_key])]],
                     colWidths=[16, BODY_W - 16], hAlign="LEFT")
        cell.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                  ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                  ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                                  ("TOPPADDING", (0, 0), (-1, -1), 0),
                                  ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5)]))
        out.append(cell)
    out.append(Spacer(1, 4))
    return out


def code_block(text):
    pre = Preformatted(text.strip("\n"), S["code"])
    t = Table([[pre]], colWidths=[BODY_W], hAlign="LEFT")
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
                           ("BOX", (0, 0), (-1, -1), 0.4, RULE),
                           ("LEFTPADDING", (0, 0), (-1, -1), 7),
                           ("TOPPADDING", (0, 0), (-1, -1), 5),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    return t


def render(content):
    story = []
    for item in content:
        kind = item[0]
        if kind == "h1":
            story += [CondPageBreak(60 * mm), Heading(item[1], 0, S["h1"])]
        elif kind == "h2":
            story += [CondPageBreak(35 * mm), Heading(item[1], 1, S["h2"])]
        elif kind == "h3":
            story += [CondPageBreak(25 * mm), Paragraph(md(item[1]), S["h3"])]
        elif kind == "p":
            story.append(Paragraph(md(item[1]), S["body"]))
        elif kind == "steps":
            story += numbered(item[1])
        elif kind == "bullets":
            story += numbered(item[1], bullet=True)
        elif kind == "table":
            story += [make_table(item[1], item[2], item[3] if len(item) > 3 else None),
                      Spacer(1, 7)]
        elif kind == "note":
            story += [Callout(item[1], item[2], item[3]), Spacer(1, 7)]
        elif kind == "code":
            story += [code_block(item[1]), Spacer(1, 6)]
        elif kind == "flow":
            cols = item[2] if len(item) > 2 else 4
            story += [Spacer(1, 3), FlowDiagram(item[1], cols), Spacer(1, 9)]
        elif kind == "arch":
            story += [Spacer(1, 3), LayerDiagram(item[1]), Spacer(1, 9)]
        elif kind == "pagebreak":
            story.append(PageBreak())
        elif kind == "spacer":
            story.append(Spacer(1, item[1]))
        else:
            raise ValueError(kind)
    return story


class ManualDoc(BaseDocTemplate):
    def __init__(self, path, meta):
        super().__init__(path, pagesize=A4, leftMargin=MARGIN_X, rightMargin=MARGIN_X,
                         topMargin=22 * mm, bottomMargin=20 * mm,
                         title=meta["title"], author="NMHS-HMIS-V1.0", subject=meta["subtitle"],
                         creator="NMHS-HMIS-V1.0 documentation build")
        self.meta = meta
        frame = Frame(MARGIN_X, 20 * mm, BODY_W, PAGE_H - 42 * mm, id="body",
                      leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        cover = Frame(MARGIN_X, 20 * mm, BODY_W, PAGE_H - 40 * mm, id="cover")
        self.addPageTemplates([PageTemplate("cover", [cover], onPage=self.draw_cover),
                               PageTemplate("body", [frame], onPage=self.draw_furniture)])

    def afterFlowable(self, flowable):
        if isinstance(flowable, Heading):
            key = f"h{id(flowable)}"
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(flowable.toc_text, key, level=flowable.toc_level,
                                      closed=flowable.toc_level > 0)
            self.notify("TOCEntry", (flowable.toc_level, flowable.toc_text, self.page, key))

    def draw_cover(self, canv, doc):
        m = self.meta
        canv.saveState()
        canv.setFillColor(NAVY)
        canv.rect(0, PAGE_H - 95 * mm, PAGE_W, 95 * mm, fill=1, stroke=0)
        canv.setFillColor(TEAL)
        canv.rect(0, PAGE_H - 98 * mm, PAGE_W, 3 * mm, fill=1, stroke=0)
        canv.setFillColor(colors.white)
        canv.setFont("Sans-Bold", 11)
        canv.drawString(MARGIN_X, PAGE_H - 28 * mm, "NGOZI MATERNITY AND HOSPITAL SERVICES (NMHS)")
        canv.setFont("Sans-Bold", 23)
        y = PAGE_H - 48 * mm
        for line in m["title_lines"]:
            canv.drawString(MARGIN_X, y, line)
            y -= 10 * mm
        canv.setFont("Sans", 13)
        for line in m["subtitle_lines"]:
            canv.drawString(MARGIN_X, y - 2 * mm, line)
            y -= 7 * mm
        canv.setFillColor(INK)
        top = PAGE_H - 118 * mm
        rows = [("System", "NMHS-HMIS-V1.0"), ("Document", m["doc_id"]), ("Version", m["version"]),
                ("Date", m["date"]), ("Audience", m["audience"]),
                ("Repository inspected", m["repo"])]
        for label, value in rows:
            canv.setFont("Sans-Bold", 9.5)
            canv.setFillColor(MUTED)
            canv.drawString(MARGIN_X, top, label.upper())
            canv.setFont("Sans", 10.5)
            canv.setFillColor(INK)
            canv.drawString(MARGIN_X + 50 * mm, top, value)
            top -= 8.5 * mm
        canv.setStrokeColor(RULE)
        canv.line(MARGIN_X, 52 * mm, PAGE_W - MARGIN_X, 52 * mm)
        p = Paragraph(md(m["source_note"]), ParagraphStyle("sn", parent=S["small"], fontSize=8.6,
                                                         leading=12, textColor=MUTED))
        _, h = p.wrap(BODY_W, 40 * mm)
        p.drawOn(canv, MARGIN_X, 48 * mm - h)
        canv.setFont("Sans", 8)
        canv.setFillColor(MUTED)
        canv.drawString(MARGIN_X, 14 * mm, "Confidential — for NMHS staff and authorised administrators.")
        canv.restoreState()

    def draw_furniture(self, canv, doc):
        canv.saveState()
        canv.setStrokeColor(RULE)
        canv.setLineWidth(0.5)
        canv.line(MARGIN_X, PAGE_H - 15 * mm, PAGE_W - MARGIN_X, PAGE_H - 15 * mm)
        canv.setFont("Sans-Bold", 7.8)
        canv.setFillColor(NAVY)
        canv.drawString(MARGIN_X, PAGE_H - 12.5 * mm, "NMHS-HMIS-V1.0")
        canv.setFont("Sans", 7.8)
        canv.setFillColor(MUTED)
        canv.drawRightString(PAGE_W - MARGIN_X, PAGE_H - 12.5 * mm, self.meta["short_title"])
        canv.line(MARGIN_X, 13.5 * mm, PAGE_W - MARGIN_X, 13.5 * mm)
        canv.drawString(MARGIN_X, 9.5 * mm, f"Version {self.meta['version']} · {self.meta['date']}")
        canv.drawRightString(PAGE_W - MARGIN_X, 9.5 * mm, f"Page {doc.page}")
        canv.restoreState()


def build(path, meta, content):
    doc = ManualDoc(path, meta)
    toc = TableOfContents()
    toc.levelStyles = [S["toc1"], S["toc2"]]
    toc.dotsMinLevel = 0
    story = [NextPageTemplate("body"), PageBreak(),
             Paragraph("Contents", S["h1"]), toc, PageBreak()]
    story += render(content)
    doc.multiBuild(story)
    return doc
