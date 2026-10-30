"""Build the Korean manual PDF from manual_ko.md.

Run:   python docs/user-guide/build_manual.py           (writes manual-ko.pdf at the repo root)
Check: python docs/user-guide/build_manual.py --check   (parses manual_ko.md only; no reportlab needed)

Requires reportlab and the Windows Malgun Gothic fonts for the PDF. Set DARA_MANUAL_FONT_DIR to use
another directory containing malgun.ttf/malgunbd.ttf. Screenshots live in images/.

manual_ko.md syntax (a small Markdown subset):
  # Title                  document title (cover); the next paragraph is the subtitle
  ## Heading               new page with a chapter heading (write the number yourself, e.g. "## 04 분석 제출")
  ### Heading              section heading
  - item                   bullet
  1. item                  numbered step
  > text                   small grey note (consecutive lines are joined with a line break)
  ![caption](images/x.png){height=300}   screenshot with caption (height in pt, default 285)
  (1) text                 numbered legend line under a screenshot; the number is drawn as a red
                           badge matching the callouts in the image (consecutive lines form one list)
  | a | b |                pipe table, first row is the header, the |---| row is skipped
  <!-- widths: 130,357 --> column widths (pt) for the next table
  [label](url), **bold**   inline link and bold; other text is escaped
"""
import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / 'manual_ko.md'
TARGET = ROOT.parents[1] / 'manual-ko.pdf'
DEFAULT_WIDTHS = (130, 357)

_LINK = re.compile(r'\[([^\]]+)\]\(([^)\s]+)\)')
_BOLD = re.compile(r'\*\*(.+?)\*\*')
_IMAGE = re.compile(r'^!\[(.*)\]\(([^)]+)\)(?:\{height=(\d+)\})?\s*$')
_LEGEND = re.compile(r'^\((\d+)\)\s+(.+)$')
_STEP = re.compile(r'^(\d+)\. (.+)$')  # numbered step: '1. text'
_WIDTHS = re.compile(r'^<!--\s*widths:\s*([\d,\s]+)-->\s*$')


def inline(text):
    """Escape text for reportlab Paragraph markup and convert links and bold."""
    out = html.escape(text, quote=False)
    out = _LINK.sub(lambda m: f'<link href="{m.group(2)}" color="#007E87">{m.group(1)}</link>', out)
    return _BOLD.sub(r'<b>\1</b>', out)


def parse(text):
    """Return a list of blocks: (kind, payload)."""
    blocks, para, notes, rows, widths, legend = [], [], [], [], None, []
    after_title = False

    def flush():
        nonlocal para, notes, rows, widths, after_title, legend
        if legend:
            blocks.append(('legend', legend))
            legend = []
        if para:
            kind = 'subtitle' if after_title else 'body'
            blocks.append((kind, inline(' '.join(para))))
            after_title = False
        if notes:
            blocks.append(('note', '<br/>'.join(inline(n) for n in notes)))
        if rows:
            blocks.append(('table', ([[inline(c) for c in r] for r in rows], widths or DEFAULT_WIDTHS)))
            widths = None
        para, notes, rows = [], [], []

    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        match = _WIDTHS.match(stripped)
        if match:
            flush()
            widths = tuple(int(v) for v in match.group(1).split(',') if v.strip())
            continue
        if stripped.startswith('<!--'):
            continue
        if stripped.startswith('|'):
            if para or notes:
                flush()
            cells = [c.strip() for c in stripped.strip('|').split('|')]
            if not all(re.fullmatch(r':?-{3,}:?', c) for c in cells):
                rows.append(cells)
            continue
        if rows:
            flush()
        if stripped.startswith('# '):
            flush()
            blocks.append(('title', inline(stripped[2:])))
            after_title = True
        elif stripped.startswith('## '):
            flush()
            blocks.append(('page', inline(stripped[3:])))
        elif stripped.startswith('### '):
            flush()
            blocks.append(('h2', inline(stripped[4:])))
        elif stripped.startswith('- '):
            flush()
            blocks.append(('bullet', inline(stripped[2:])))
        elif _STEP.match(stripped):
            flush()
            m = _STEP.match(stripped)
            blocks.append(('step', (m.group(1), inline(m.group(2)))))
        elif stripped.startswith('>'):
            if para:
                flush()
            notes.append(stripped[1:].strip())
        elif _LEGEND.match(stripped):
            if para or notes or rows:
                flush()
            m = _LEGEND.match(stripped)
            legend.append((m.group(1), inline(m.group(2))))
        elif _IMAGE.match(stripped):
            flush()
            m = _IMAGE.match(stripped)
            blocks.append(('image', (m.group(2), inline(m.group(1)), int(m.group(3) or 285))))
        else:
            if notes:
                flush()
            para.append(stripped)
    flush()
    return blocks


def build(blocks, target=TARGET):
    import os

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Flowable, Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    class Badge(Flowable):
        """Red numbered circle matching the callout badges drawn on the screenshots."""

        def __init__(self, number, size=13):
            super().__init__()
            self.number, self.size = number, size
            self.width = self.height = size

        def draw(self):
            c, r = self.canv, self.size / 2
            c.setFillColor(colors.HexColor('#E02424'))
            c.circle(r, r, r, stroke=0, fill=1)
            c.setFillColor(colors.white)
            c.setFont('KoreanBold', 8 if len(self.number) < 2 else 6.8)
            c.drawCentredString(r, r - 2.9, self.number)

    font = Path(os.environ.get('DARA_MANUAL_FONT_DIR', 'C:/Windows/Fonts'))
    pdfmetrics.registerFont(TTFont('Korean', str(font / 'malgun.ttf')))
    pdfmetrics.registerFont(TTFont('KoreanBold', str(font / 'malgunbd.ttf')))
    pdfmetrics.registerFontFamily('Korean', normal='Korean', bold='KoreanBold')
    navy, teal, grey = colors.HexColor('#15324F'), colors.HexColor('#007E87'), colors.HexColor('#536375')
    styles = {
        'title': ParagraphStyle('title', fontName='KoreanBold', fontSize=28, leading=39, textColor=navy, spaceAfter=14),
        'subtitle': ParagraphStyle('subtitle', fontName='Korean', fontSize=13, leading=20, textColor=teal, spaceAfter=18, wordWrap='CJK'),
        'page': ParagraphStyle('page', fontName='KoreanBold', fontSize=19, leading=27, textColor=navy, spaceAfter=12),
        'h2': ParagraphStyle('h2', fontName='KoreanBold', fontSize=11.5, leading=18, textColor=teal, spaceBefore=10, spaceAfter=4, keepWithNext=1),
        'body': ParagraphStyle('body', fontName='Korean', fontSize=9.8, leading=16, wordWrap='CJK', spaceAfter=6),
        'bullet': ParagraphStyle('bullet', fontName='Korean', fontSize=9.8, leading=16, wordWrap='CJK', spaceAfter=3,
                                 leftIndent=12, bulletIndent=2),
        'step': ParagraphStyle('step', fontName='Korean', fontSize=9.8, leading=16, wordWrap='CJK', spaceAfter=3,
                               leftIndent=16, bulletIndent=2),
        'note': ParagraphStyle('note', fontName='Korean', fontSize=8, leading=12, textColor=grey, spaceAfter=8, wordWrap='CJK'),
        'legend': ParagraphStyle('legend', fontName='Korean', fontSize=9, leading=13.5, wordWrap='CJK'),
        'cell': ParagraphStyle('cell', fontName='Korean', fontSize=8.8, leading=13.5, wordWrap='CJK'),
    }
    story = []
    previous = None
    for kind, payload in blocks:
        if previous in ('bullet', 'step') and kind != previous:
            story.append(Spacer(1, 4))
        if kind == 'page':
            if story:
                story.append(PageBreak())
            story.append(Paragraph(payload, styles['page']))
        elif kind == 'bullet':
            story.append(Paragraph(payload, styles['bullet'], bulletText='·'))
        elif kind == 'step':
            number, text = payload
            story.append(Paragraph(text, styles['step'], bulletText=f'{number}.'))
        elif kind == 'image':
            path, caption, height = payload
            img = Image(str(ROOT / path))
            ratio = min(487 / img.imageWidth, height / img.imageHeight)
            img.drawWidth, img.drawHeight = img.imageWidth * ratio, img.imageHeight * ratio
            story.append(img)
            story.append(Paragraph(caption, styles['note']))
        elif kind == 'legend':
            data = [[Badge(n), Paragraph(text, styles['legend'])] for n, text in payload]
            table = Table(data, colWidths=[20, 467], hAlign='LEFT')
            table.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 2),
                ('TOPPADDING', (0, 0), (-1, -1), 1.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 1.5),
                ('TOPPADDING', (0, 0), (0, -1), 2.5),
            ]))
            if previous == 'image':  # keep screenshot, caption and its legend on one page
                story[-2:] = [KeepTogether(story[-2:] + [table])]
            else:
                story.append(table)
            story.append(Spacer(1, 8))
        elif kind == 'table':
            rows, widths = payload
            cells = [[Paragraph(c, styles['cell']) for c in r] for r in rows]
            table = Table(cells, colWidths=list(widths), hAlign='LEFT', repeatRows=1)
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#E6EEF4')),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LINEBELOW', (0, 0), (-1, -1), 0.4, colors.HexColor('#D4DFE7')),
                ('LEFTPADDING', (0, 0), (-1, -1), 7), ('RIGHTPADDING', (0, 0), (-1, -1), 7),
                ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ]))
            story.extend([table, Spacer(1, 8)])
        else:
            story.append(Paragraph(payload, styles[kind]))
        previous = kind

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor('#D4DFE7'))
        canvas.line(54, 45, A4[0] - 54, 45)
        canvas.setFont('Korean', 8)
        canvas.setFillColor(grey)
        canvas.drawString(54, 30, 'DARA · 한글 사용 설명서')
        canvas.drawRightString(A4[0] - 54, 30, str(doc.page))
        canvas.restoreState()

    doc = SimpleDocTemplate(str(target), pagesize=A4, rightMargin=54, leftMargin=54,
                            topMargin=48, bottomMargin=62, title='DARA 한글 사용 설명서', author='DARA 프로젝트')
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return target


def check(blocks):
    """Validate the parsed manual: images exist, tables are rectangular and fit the page."""
    problems = []
    for kind, payload in blocks:
        if kind == 'image' and not (ROOT / payload[0]).is_file():
            problems.append(f'missing image: {payload[0]}')
        if kind == 'table':
            rows, widths = payload
            if any(len(r) != len(widths) for r in rows):
                problems.append(f'table column count != widths {widths}: {rows[0]}')
            if sum(widths) > 487:
                problems.append(f'table wider than the text column: {widths}')
    return problems


if __name__ == '__main__':
    parsed = parse(SOURCE.read_text(encoding='utf-8'))
    issues = check(parsed)
    for issue in issues:
        print('ERROR:', issue)
    if '--check' in sys.argv:
        counts = {}
        for kind, _ in parsed:
            counts[kind] = counts.get(kind, 0) + 1
        print('blocks:', counts)
        sys.exit(1 if issues else 0)
    if issues:
        sys.exit(1)
    print(build(parsed))
