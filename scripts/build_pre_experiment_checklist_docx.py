#!/usr/bin/env python3
"""Create the concise AquaBeacon pre-experiment checklist DOCX."""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = (
    ROOT
    / "reports"
    / "20260729_182721_pool_run2_power_loss"
    / "AquaBeacon_実験前チェックリスト.docx"
)

BLUE = "2E74B5"
DARK_BLUE = "1F4D78"
LIGHT_BLUE = "E8EEF5"
PALE_YELLOW = "FFF4CC"
DARK_GRAY = "344054"
MID_GRAY = "667085"
RED = "9B1C1C"
WHITE = "FFFFFF"
LATIN_FONT = "Calibri"
JP_FONT = "Yu Gothic"
TABLE_WIDTH_DXA = 9360


PREPARATION_ITEMS = [
    "TX・RX・TD04A用外部電源のバッテリーを満充電し、予備電源も準備した。",
    "TX・RXのファームウェアと、TXの送信間隔が5秒であることを確認した。",
    "RXにSDカードを装着し、空き容量、書き込み、新しいCSVファイルの作成を確認した。",
    "TX・RX・RS485・TD04A・GPSの配線を配線図と照合し、極性、固定、抜けがないことを確認した。",
    "ブレッドボードや仮配線を避け、基板・はんだ付け・固定済み配線で構成した。",
    "30 mテザーの損傷、絡まり、コネクタ、ストレインリリーフを確認し、スプールから安全に展開できる。",
    "振動子、筐体、防水コネクタ、ペネトレーターに緩み、亀裂、漏水経路がないことを確認した。",
    "PC、RX COM5、TX COM10、GPS時刻、実験距離、水温、SDファイル番号を記録できる状態にした。",
    "振動子RX側とTX側を見ずにつけて送信・受信確認がSDカードから確認できた。",
]

FINAL_ITEMS = [
    "RX側 RS485変換ケーブルが光っており、5秒ごとに点滅している。",
    "TX側 RS485変換ケーブルが光っており、5秒ごとに点滅している。",
    "TX側 TD04A用外部電源のコネクタが赤く点灯している。",
    "プールサイドではSDカードの中身を確認せず、SDカードや配線に触れない。読出し確認は実験後に行う。",
    "テスト送信を3回行い、RXでENV_ADC・CMP_OUTの反応と測定ログを確認した。",
]


def set_font(run, size=10.5, color=DARK_GRAY, bold=False):
    run.font.name = LATIN_FONT
    run.font.size = Pt(size)
    run.font.color.rgb = RGBColor.from_string(color)
    run.bold = bold
    rpr = run._element.get_or_add_rPr()
    fonts = rpr.rFonts
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.insert(0, fonts)
    fonts.set(qn("w:ascii"), LATIN_FONT)
    fonts.set(qn("w:hAnsi"), LATIN_FONT)
    fonts.set(qn("w:eastAsia"), JP_FONT)


def shade(cell, fill):
    tcpr = cell._tc.get_or_add_tcPr()
    shd = tcpr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tcpr.append(shd)
    shd.set(qn("w:fill"), fill)


def shade_paragraph(paragraph, fill):
    ppr = paragraph._p.get_or_add_pPr()
    shd = ppr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        ppr.append(shd)
    shd.set(qn("w:fill"), fill)


def set_cell_margins(cell, top=90, start=120, bottom=90, end=120):
    tcpr = cell._tc.get_or_add_tcPr()
    tc_mar = tcpr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tcpr.append(tc_mar)
    for edge, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = tc_mar.find(qn(f"w:{edge}"))
        if node is None:
            node = OxmlElement(f"w:{edge}")
            tc_mar.append(node)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")


def set_table_geometry(table, widths):
    if sum(widths) != TABLE_WIDTH_DXA:
        raise ValueError("Table widths must sum to 9360 DXA")
    table.autofit = False
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    tblpr = table._tbl.tblPr
    tblw = tblpr.find(qn("w:tblW"))
    if tblw is None:
        tblw = OxmlElement("w:tblW")
        tblpr.append(tblw)
    tblw.set(qn("w:w"), str(TABLE_WIDTH_DXA))
    tblw.set(qn("w:type"), "dxa")
    grid = table._tbl.tblGrid
    for child in list(grid):
        grid.remove(child)
    for width in widths:
        col = OxmlElement("w:gridCol")
        col.set(qn("w:w"), str(width))
        grid.append(col)
    for row in table.rows:
        for index, cell in enumerate(row.cells):
            cell.width = Inches(widths[index] / 1440)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            set_cell_margins(cell)
            tcw = cell._tc.get_or_add_tcPr().find(qn("w:tcW"))
            if tcw is None:
                tcw = OxmlElement("w:tcW")
                cell._tc.get_or_add_tcPr().append(tcw)
            tcw.set(qn("w:w"), str(widths[index]))
            tcw.set(qn("w:type"), "dxa")


def repeat_header(row):
    trpr = row._tr.get_or_add_trPr()
    node = OxmlElement("w:tblHeader")
    node.set(qn("w:val"), "true")
    trpr.append(node)


def add_field(paragraph, instruction):
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = instruction
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    text = OxmlElement("w:t")
    text.text = "1"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.extend((begin, instr, separate, text, end))
    set_font(run, size=8.5, color=MID_GRAY)


def configure_document(doc):
    section = doc.sections[0]
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    section.top_margin = Inches(1)
    section.right_margin = Inches(1)
    section.bottom_margin = Inches(1)
    section.left_margin = Inches(1)
    section.header_distance = Inches(0.492)
    section.footer_distance = Inches(0.492)

    normal = doc.styles["Normal"]
    normal.font.name = LATIN_FONT
    normal.font.size = Pt(10.5)
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), JP_FONT)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.15

    heading = doc.styles["Heading 1"]
    heading.font.name = LATIN_FONT
    heading.font.size = Pt(16)
    heading.font.bold = True
    heading.font.color.rgb = RGBColor.from_string(BLUE)
    heading._element.rPr.rFonts.set(qn("w:eastAsia"), JP_FONT)
    heading.paragraph_format.space_before = Pt(14)
    heading.paragraph_format.space_after = Pt(3)
    heading.paragraph_format.keep_with_next = True

    header = section.header.paragraphs[0]
    header.paragraph_format.space_after = Pt(0)
    set_font(header.add_run("AquaBeacon  |  FIELD OPERATIONS"), size=8.5, color=MID_GRAY, bold=True)

    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    set_font(footer.add_run("Page "), size=8.5, color=MID_GRAY)
    add_field(footer, "PAGE")
    set_font(footer.add_run(" / "), size=8.5, color=MID_GRAY)
    add_field(footer, "NUMPAGES")


def add_title(doc):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(2)
    set_font(p.add_run("AQUABEACON"), size=10, color=BLUE, bold=True)

    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(6)
    set_font(p.add_run("プール実験前チェックリスト"), size=23, color=DARK_BLUE, bold=True)

    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(10)
    set_font(p.add_run("実験日：____________　場所：____________　予定距離：____________ m"), size=10.5)
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(12)
    set_font(p.add_run("確認担当：____________　立会確認：____________　開始予定：______ 時 ______ 分"), size=10.5)

    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Inches(0.08)
    p.paragraph_format.right_indent = Inches(0.08)
    p.paragraph_format.space_after = Pt(0)
    shade_paragraph(p, PALE_YELLOW)
    set_font(p.add_run("重要："), size=10.5, color=RED, bold=True)
    set_font(p.add_run("全項目に判定（〇・△・✖）を記入し、✖がある場合は実験を開始しない。異常時は電源を切り、原因を確認してから再点検する。"), size=10.5)


def add_section_heading(doc, number, title, subtitle):
    p = doc.add_paragraph(style="Heading 1")
    set_font(p.add_run(f"{number}  {title}"), size=16, color=BLUE, bold=True)
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(7)
    p.paragraph_format.keep_with_next = True
    set_font(p.add_run(subtitle), size=9.5, color=MID_GRAY)


def add_check_table(doc, items):
    table = doc.add_table(rows=1, cols=4)
    table.style = "Table Grid"
    headers = ["No.", "確認項目", "判定（〇・△・✖）", "記録・異常内容"]
    widths = [600, 5760, 1300, 1700]
    for index, header in enumerate(headers):
        cell = table.rows[0].cells[index]
        shade(cell, LIGHT_BLUE)
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(0)
        set_font(p.add_run(header), size=9, bold=True)
    repeat_header(table.rows[0])

    for index, item in enumerate(items, 1):
        cells = table.add_row().cells
        values = [str(index), item, "□", ""]
        for col, value in enumerate(values):
            p = cells[col].paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT if col in (1, 3) else WD_ALIGN_PARAGRAPH.CENTER
            set_font(p.add_run(value), size=9.2 if col == 1 else 9)
    set_table_geometry(table, widths)


def add_start_decision(doc):
    add_section_heading(doc, "3", "開始判定", "下のどちらか一方にチェックし、確認者2名で署名する。")
    table = doc.add_table(rows=1, cols=2)
    table.style = "Table Grid"
    for index, header in enumerate(("判定", "開始条件")):
        cell = table.rows[0].cells[index]
        shade(cell, LIGHT_BLUE)
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(0)
        set_font(p.add_run(header), size=9, bold=True)
    repeat_header(table.rows[0])
    rows = [
        ("□ 開始可", "全13項目の判定が〇または△。✖がなく、実験を開始する。"),
        ("□ 開始不可", "1項目以上が✖または未記入。原因解消後に再点検する。"),
    ]
    for decision, meaning in rows:
        row = table.add_row()
        p = row.cells[0].paragraphs[0]
        p.paragraph_format.space_after = Pt(0)
        set_font(p.add_run(decision), size=11, color=RED if "不可" in decision else DARK_BLUE, bold=True)
        p = row.cells[1].paragraphs[0]
        p.paragraph_format.space_after = Pt(0)
        set_font(p.add_run(meaning), size=9.5)
    set_table_geometry(table, [2200, 7160])

    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(12)
    set_font(p.add_run("直前確認時刻：______ 時 ______ 分　　SD開始ファイル：S______.CSV"), size=10.5)
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(10)
    set_font(p.add_run("確認担当署名：________________________　立会確認署名：________________________"), size=10.5)
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(12)
    p.paragraph_format.space_after = Pt(2)
    set_font(p.add_run("異常・対応メモ"), size=10.5, bold=True)
    for _ in range(3):
        p = doc.add_paragraph("________________________________________________________________________________")
        p.paragraph_format.space_after = Pt(3)
        set_font(p.runs[0], size=9, color=MID_GRAY)


def build():
    doc = Document()
    configure_document(doc)
    add_title(doc)
    add_section_heading(doc, "1", "実験前の準備", "出発前または設営前に確認する。全9項目。")
    add_check_table(doc, PREPARATION_ITEMS)

    doc.add_page_break()
    add_section_heading(doc, "2", "実験直前の確認", "電源投入後、測定開始の直前に確認する。全5項目。判定欄に〇・△・✖を記入する。")
    add_check_table(doc, FINAL_ITEMS)
    add_start_decision(doc)

    props = doc.core_properties
    props.title = "AquaBeacon プール実験前チェックリスト"
    props.subject = "電源・配線・SD保存・送受信動作の実験前確認"
    props.author = "AquaBeacon Project"
    props.keywords = "AquaBeacon, TD04A, 実験前チェックリスト, RS485, SDカード"

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(OUTPUT)
    print(OUTPUT)


if __name__ == "__main__":
    build()
