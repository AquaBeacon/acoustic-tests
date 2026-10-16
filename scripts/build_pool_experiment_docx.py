#!/usr/bin/env python3
"""Build the integrated school-pool experiment report as a polished DOCX."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


NAVY = "17365D"
BLUE = "2E74B5"
DARK_BLUE = "1F4D78"
TEAL = "008C7A"
LIGHT_BLUE = "E8EEF5"
LIGHT_GRAY = "F2F4F7"
MID_GRAY = "667085"
DARK_GRAY = "344054"
GOLD = "9A6700"
LIGHT_GOLD = "FFF4CC"
RED = "9B1C1C"
LIGHT_RED = "FDECEC"
GREEN = "1E7A46"
LIGHT_GREEN = "EAF6EF"
WHITE = "FFFFFF"
BLACK = "000000"
TABLE_WIDTH_DXA = 9360
TABLE_INDENT_DXA = 120
CELL_MARGIN_TOP_BOTTOM = 80
CELL_MARGIN_LEFT_RIGHT = 120
LATIN_FONT = "Calibri"
JP_FONT = "Yu Gothic"


def set_run_font(
    run,
    *,
    size: float | None = None,
    color: str | None = None,
    bold: bool | None = None,
    italic: bool | None = None,
) -> None:
    run.font.name = LATIN_FONT
    rpr = run._element.get_or_add_rPr()
    fonts = rpr.rFonts
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.insert(0, fonts)
    fonts.set(qn("w:ascii"), LATIN_FONT)
    fonts.set(qn("w:hAnsi"), LATIN_FONT)
    fonts.set(qn("w:eastAsia"), JP_FONT)
    if size is not None:
        run.font.size = Pt(size)
    if color is not None:
        run.font.color.rgb = RGBColor.from_string(color)
    if bold is not None:
        run.bold = bold
    if italic is not None:
        run.italic = italic


def set_cell_shading(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def set_cell_margins(cell) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for edge, value in (
        ("top", CELL_MARGIN_TOP_BOTTOM),
        ("bottom", CELL_MARGIN_TOP_BOTTOM),
        ("start", CELL_MARGIN_LEFT_RIGHT),
        ("end", CELL_MARGIN_LEFT_RIGHT),
    ):
        tag = tc_mar.find(qn(f"w:{edge}"))
        if tag is None:
            tag = OxmlElement(f"w:{edge}")
            tc_mar.append(tag)
        tag.set(qn("w:w"), str(value))
        tag.set(qn("w:type"), "dxa")


def set_repeat_table_header(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    header = OxmlElement("w:tblHeader")
    header.set(qn("w:val"), "true")
    tr_pr.append(header)


def set_table_geometry(table, widths_dxa: list[int]) -> None:
    if sum(widths_dxa) != TABLE_WIDTH_DXA:
        raise ValueError(f"table widths must sum to {TABLE_WIDTH_DXA}")
    table.autofit = False
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    tbl_pr = table._tbl.tblPr
    tbl_w = tbl_pr.find(qn("w:tblW"))
    if tbl_w is None:
        tbl_w = OxmlElement("w:tblW")
        tbl_pr.append(tbl_w)
    tbl_w.set(qn("w:w"), str(TABLE_WIDTH_DXA))
    tbl_w.set(qn("w:type"), "dxa")
    tbl_ind = tbl_pr.find(qn("w:tblInd"))
    if tbl_ind is None:
        tbl_ind = OxmlElement("w:tblInd")
        tbl_pr.append(tbl_ind)
    tbl_ind.set(qn("w:w"), str(TABLE_INDENT_DXA))
    tbl_ind.set(qn("w:type"), "dxa")
    grid = table._tbl.tblGrid
    for child in list(grid):
        grid.remove(child)
    for width in widths_dxa:
        grid_col = OxmlElement("w:gridCol")
        grid_col.set(qn("w:w"), str(width))
        grid.append(grid_col)
    for row in table.rows:
        for idx, cell in enumerate(row.cells):
            cell.width = Inches(widths_dxa[idx] / 1440)
            tc_pr = cell._tc.get_or_add_tcPr()
            tc_w = tc_pr.find(qn("w:tcW"))
            if tc_w is None:
                tc_w = OxmlElement("w:tcW")
                tc_pr.append(tc_w)
            tc_w.set(qn("w:w"), str(widths_dxa[idx]))
            tc_w.set(qn("w:type"), "dxa")
            set_cell_margins(cell)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER


def set_paragraph_border(paragraph, *, color: str, size: int = 8) -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    p_bdr = p_pr.find(qn("w:pBdr"))
    if p_bdr is None:
        p_bdr = OxmlElement("w:pBdr")
        p_pr.append(p_bdr)
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), str(size))
    bottom.set(qn("w:space"), "4")
    bottom.set(qn("w:color"), color)
    p_bdr.append(bottom)


def set_paragraph_shading(paragraph, fill: str) -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    shd = p_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        p_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def keep_with_next(paragraph) -> None:
    paragraph.paragraph_format.keep_with_next = True


def add_field(paragraph, instruction: str) -> None:
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
    set_run_font(run, size=9, color=MID_GRAY)


def configure_styles(doc: Document) -> None:
    normal = doc.styles["Normal"]
    normal.font.name = LATIN_FONT
    normal._element.rPr.rFonts.set(qn("w:ascii"), LATIN_FONT)
    normal._element.rPr.rFonts.set(qn("w:hAnsi"), LATIN_FONT)
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), JP_FONT)
    normal.font.size = Pt(10.5)
    normal.font.color.rgb = RGBColor.from_string(BLACK)
    pf = normal.paragraph_format
    pf.space_before = Pt(0)
    pf.space_after = Pt(6)
    pf.line_spacing = 1.10

    heading_tokens = {
        "Heading 1": (16, BLUE, 16, 8),
        "Heading 2": (13, BLUE, 12, 6),
        "Heading 3": (12, DARK_BLUE, 8, 4),
    }
    for name, (size, color, before, after) in heading_tokens.items():
        style = doc.styles[name]
        style.font.name = LATIN_FONT
        style._element.rPr.rFonts.set(qn("w:ascii"), LATIN_FONT)
        style._element.rPr.rFonts.set(qn("w:hAnsi"), LATIN_FONT)
        style._element.rPr.rFonts.set(qn("w:eastAsia"), JP_FONT)
        style.font.size = Pt(size)
        style.font.color.rgb = RGBColor.from_string(color)
        style.font.bold = True
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(after)
        style.paragraph_format.keep_with_next = True

    for name in ("List Bullet", "List Number"):
        style = doc.styles[name]
        style.font.name = LATIN_FONT
        style._element.rPr.rFonts.set(qn("w:ascii"), LATIN_FONT)
        style._element.rPr.rFonts.set(qn("w:hAnsi"), LATIN_FONT)
        style._element.rPr.rFonts.set(qn("w:eastAsia"), JP_FONT)
        style.font.size = Pt(10.5)
        style.paragraph_format.left_indent = Inches(0.5)
        style.paragraph_format.first_line_indent = Inches(-0.25)
        style.paragraph_format.space_after = Pt(8)
        style.paragraph_format.line_spacing = 1.167


def configure_page(doc: Document) -> None:
    section = doc.sections[0]
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    section.top_margin = Inches(1)
    section.right_margin = Inches(1)
    section.bottom_margin = Inches(1)
    section.left_margin = Inches(1)
    section.header_distance = Inches(0.492)
    section.footer_distance = Inches(0.492)
    section.different_first_page_header_footer = True

    header = section.header
    p = header.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p.paragraph_format.space_after = Pt(0)
    run = p.add_run("AquaBeacon  |  学校プール実験・統合技術レポート")
    set_run_font(run, size=8.5, color=MID_GRAY, bold=True)

    footer = section.footer
    p = footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    p.paragraph_format.space_before = Pt(0)
    r = p.add_run("Page ")
    set_run_font(r, size=9, color=MID_GRAY)
    add_field(p, "PAGE")
    r = p.add_run(" / ")
    set_run_font(r, size=9, color=MID_GRAY)
    add_field(p, "NUMPAGES")


def add_title_page(doc: Document) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(88)
    p.paragraph_format.space_after = Pt(14)
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("AquaBeacon")
    set_run_font(r, size=13, color=TEAL, bold=True)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(10)
    r = p.add_run("学校プール実験\n統合技術レポート")
    set_run_font(r, size=28, color=NAVY, bold=True)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(28)
    r = p.add_run("200 kHz・5波バーストによる水中音響測距")
    set_run_font(r, size=14, color=DARK_BLUE)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(72)
    r = p.add_run("測定距離 1 m / 5 m  |  SDログ S000.CSV - S073.CSV")
    set_run_font(r, size=10.5, color=MID_GRAY)

    metadata = [
        ("実験場所", "学校のプール"),
        ("実験日", "2026年7月28日"),
        ("実験条件", "表層付近・水温申告35℃（実水温は未測定）"),
        ("中断理由", "降雨のため1 m・5 mで終了"),
        ("作成日", datetime.now().strftime("%Y年%m月%d日")),
    ]
    for label, value in metadata:
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(3)
        r = p.add_run(f"{label}: ")
        set_run_font(r, size=10, color=DARK_GRAY, bold=True)
        r = p.add_run(value)
        set_run_font(r, size=10, color=DARK_GRAY)

    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(50)
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("総合判定: FAIL（検出率不足） / 25 m見通し: INCONCLUSIVE")
    set_run_font(r, size=11, color=RED, bold=True)
    doc.add_page_break()


def add_heading(doc: Document, text: str, level: int = 1) -> None:
    p = doc.add_paragraph(text, style=f"Heading {level}")
    if level == 1:
        set_paragraph_border(p, color=LIGHT_BLUE, size=8)


def add_body(doc: Document, text: str, *, bold_lead: str | None = None) -> None:
    p = doc.add_paragraph()
    if bold_lead and text.startswith(bold_lead):
        r = p.add_run(bold_lead)
        set_run_font(r, bold=True, color=DARK_GRAY)
        r = p.add_run(text[len(bold_lead) :])
        set_run_font(r)
    else:
        r = p.add_run(text)
        set_run_font(r)


def add_bullet(doc: Document, text: str, *, numbered: bool = False) -> None:
    style = "List Number" if numbered else "List Bullet"
    p = doc.add_paragraph(style=style)
    r = p.add_run(text)
    set_run_font(r)


def add_callout(
    doc: Document,
    label: str,
    text: str,
    *,
    fill: str = LIGHT_BLUE,
    color: str = NAVY,
) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Inches(0.12)
    p.paragraph_format.right_indent = Inches(0.12)
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(10)
    set_paragraph_shading(p, fill)
    r = p.add_run(f"{label}  ")
    set_run_font(r, size=10.5, color=color, bold=True)
    r = p.add_run(text)
    set_run_font(r, size=10.5, color=DARK_GRAY)


def add_table(
    doc: Document,
    headers: list[str],
    rows: list[list[str]],
    widths_dxa: list[int],
    *,
    header_fill: str = LIGHT_GRAY,
) -> None:
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    set_table_geometry(table, widths_dxa)
    set_repeat_table_header(table.rows[0])
    for idx, value in enumerate(headers):
        cell = table.rows[0].cells[idx]
        set_cell_shading(cell, header_fill)
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(0)
        r = p.add_run(value)
        set_run_font(r, size=9.2, color=DARK_GRAY, bold=True)
    for row_values in rows:
        cells = table.add_row().cells
        for idx, value in enumerate(row_values):
            cell = cells[idx]
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            p.alignment = (
                WD_ALIGN_PARAGRAPH.LEFT
                if idx == 0 and len(headers) <= 3
                else WD_ALIGN_PARAGRAPH.CENTER
            )
            r = p.add_run(str(value))
            set_run_font(r, size=9.1)
    set_table_geometry(table, widths_dxa)
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(2)


def add_figure(
    doc: Document,
    image: Path,
    caption: str,
    *,
    width_in: float = 6.35,
    page_break_before: bool = False,
) -> None:
    if page_break_before:
        doc.add_page_break()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(4)
    keep_with_next(p)
    run = p.add_run()
    picture = run.add_picture(str(image), width=Inches(width_in))
    picture._inline.docPr.set("descr", caption)
    picture._inline.docPr.set("title", caption.split("。", 1)[0])
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(10)
    r = p.add_run(caption)
    set_run_font(r, size=9, color=MID_GRAY, italic=True)


def build_report(report_dir: Path, output_path: Path) -> None:
    images = {
        "distance": report_dir / "距離測定結果.png",
        "comparison": report_dir / "1m_5m比較.png",
        "threshold": report_dir / "ADC_閾値推移.png",
        "adc_strength": report_dir / "ADC信号強度比較.png",
        "prediction": report_dir / "25m信号強度予測.png",
    }
    for image in images.values():
        if not image.is_file():
            raise FileNotFoundError(image)

    doc = Document()
    configure_styles(doc)
    configure_page(doc)
    add_title_page(doc)

    add_heading(doc, "概要", 1)
    add_callout(
        doc,
        "結論",
        "1 m・5 mとも距離再現性は良好だったが、VALID率が合格基準90%を大きく下回ったため総合FAIL。"
        "5 m実測を基準にした25 m名目予測は閾値を上回る一方、追加損失6 dBで閾値を下回るため、"
        "現構成での25 m安定受信は未証明である。",
        fill=LIGHT_RED,
        color=RED,
    )
    add_body(
        doc,
        "本書は、学校プールで取得したSDログ74ファイルの解析結果、距離測定、ENV_ADC、CMP_OUT、"
        "TH_PWM自動調整、水温感度、および25 mまでの信号強度予測を一つの技術レポートに統合したものである。"
    )
    add_table(
        doc,
        ["評価項目", "結果", "判定"],
        [
            ["SDカード読出し", "74 / 74ファイル、整合性PASS", "PASS"],
            ["1 m VALID率", "11 / 35 = 31.4%", "FAIL"],
            ["5 m VALID率", "13 / 33 = 39.4%", "FAIL"],
            ["距離再現性", "1 m: 2.61 cm / 5 m: 3.07 cm", "PASS"],
            ["VALID score", "24件すべて score 8", "PASS"],
            ["25 m受信", "名目余裕3.8 dB、6 dB追加損失で閾値未満", "INCONCLUSIVE"],
        ],
        [3500, 3860, 2000],
    )

    add_heading(doc, "目次", 1)
    for item in (
        "1. 実験目的と範囲",
        "2. 実験構成・条件",
        "3. 取得データと完全性",
        "4. 距離測定結果",
        "5. 水温による距離補正",
        "6. ADC・CMP_OUT・閾値自動調整",
        "7. 25 mまでの信号強度予測",
        "8. 判定と考察",
        "9. 次回実験の推奨手順",
        "付録A. 解析方法と参照資料",
    ):
        add_bullet(doc, item)

    add_heading(doc, "1. 実験目的と範囲", 1)
    add_body(
        doc,
        "TXから200 kHzの5波バーストを送信し、RXでSYNC基準の飛行時間、ENV_ADC波形、"
        "CMP_OUTエッジ、TH_PWM閾値を記録して水中距離を測定する。プール実験では1 mと5 mを実施し、"
        "降雨により10 m以上の測定は中断した。"
    )
    add_bullet(doc, "距離精度と再現性の評価")
    add_bullet(doc, "VALID / NO_DETECT件数と合格基準90%の照合")
    add_bullet(doc, "ENV_ADC信号強度とTH_PWM自動調整の動作確認")
    add_bullet(doc, "CMP_OUT立上りとNO_DETECT原因の切り分け")
    add_bullet(doc, "1 m・5 m実測から25 mまでの受信見通しを推定")

    add_heading(doc, "2. 実験構成・条件", 1)
    add_table(
        doc,
        ["項目", "設定・状態"],
        [
            ["実験場所", "学校のプール（表層付近）"],
            ["測定距離", "1 m、5 m"],
            ["水温", "35℃との申告。振動子深度の実水温は未測定で、より高かった可能性あり"],
            ["天候", "途中から雨。安全のため5 m終了後に中断"],
            ["振動子", "TD04A × 2、水中配置"],
            ["TX", "COM10、GP15: 200 kHz・5波PWM、GP14: RS485経由SYNC"],
            ["RX", "COM5、GP14: SYNC、GP6: TH_PWM、GP26: ENV_ADC、GP2: CMP_OUT"],
            ["送信周期", "5秒"],
            ["SDカード", "RX側、1送信ごとに新規CSVファイル"],
            ["距離計算", "音速1500 m/s、SYSTEM_DELAY_US = 0"],
            ["受信窓", "最大30 m相当 + 余裕"],
            ["閾値探索", "300 mV開始、NO_DETECTごとに20 mV低下、探索下限60 mV"],
        ],
        [2500, 6860],
        header_fill=LIGHT_BLUE,
    )
    add_callout(
        doc,
        "注意",
        "水温35℃は振動子深度での同時計測値ではない。温度補正値は感度分析として扱い、"
        "ファームウェア定数の変更根拠にはしない。",
        fill=LIGHT_GOLD,
        color=GOLD,
    )

    add_heading(doc, "3. 取得データと完全性", 1)
    add_body(
        doc,
        "SDカードからS000.CSV〜S073.CSVの74ファイルを読み戻した。主測定区間はS006〜S040を1 m、"
        "S041〜S073を5 mと推定した。S000〜S005は設置確認データとして主集計から除外した。"
    )
    add_table(
        doc,
        ["項目", "結果"],
        [
            ["SDファイル", "74 / 74件を読出し"],
            ["主測定", "68件（1 m: 35件、5 m: 33件）"],
            ["ENV_ADC", "80,665サンプル、ドロップ0"],
            ["CMP_OUT", "1,073エッジ、SD省略0"],
            ["GPS時刻", "73 / 74件で有効"],
            ["最大SDファイル", "38,687 bytes（警告上限262,144 bytes未満）"],
            ["整合性", "74 / 74件 PASS"],
        ],
        [3000, 6360],
    )

    add_heading(doc, "4. 距離測定結果", 1)
    add_table(
        doc,
        ["距離", "測定数", "VALID", "VALID率", "平均", "標準偏差", "範囲"],
        [
            ["1 m", "35", "11", "31.4%", "1.0550 m", "0.0261 m", "1.020-1.098 m"],
            ["5 m", "33", "13", "39.4%", "5.1226 m", "0.0307 m", "5.069-5.172 m"],
        ],
        [900, 1050, 900, 1100, 1500, 1600, 2310],
    )
    add_body(
        doc,
        "VALIDになった測定だけを見ると標準偏差は1 mで2.61 cm、5 mで3.07 cmであり、"
        "仕様の距離再現性10 cm以下を満たす。一方、NO_DETECTが多く、システムとしての検出安定性は不足している。"
    )
    add_figure(
        doc,
        images["distance"],
        "図1　1 m・5 mの距離測定結果。×印はNO_DETECTを目標距離位置に表示。",
        width_in=6.35,
    )
    add_figure(
        doc,
        images["comparison"],
        "図2　距離別のVALID率、平均誤差および標準偏差の比較。",
        width_in=6.25,
        page_break_before=True,
    )

    add_heading(doc, "5. 水温による距離補正", 1)
    add_body(
        doc,
        "浅水・淡水としてNPL式の塩分0・深度ほぼ0を適用すると、音速は35℃で1519.864 m/s、"
        "40℃で1528.900 m/sとなる。ログは1500 m/sで距離換算されているため、高温補正では距離はさらに増加する。"
    )
    add_table(
        doc,
        ["距離条件", "ログ平均\n1500 m/s", "35℃補正", "40℃補正", "40℃時の目標誤差"],
        [
            ["1 m", "1.0550 m", "1.0690 m", "1.0753 m", "+7.5 cm"],
            ["5 m", "5.1226 m", "5.1905 m", "5.2213 m", "+22.1 cm"],
        ],
        [1400, 1900, 1800, 1800, 2460],
    )
    add_callout(
        doc,
        "判断",
        "水温上昇は今回の正方向の距離誤差を説明せず、むしろ誤差を増加させる。"
        "35℃から40℃への不確かさは5 m級で約3 cmであり、固定遅延・実距離・検出点の違いも別途評価が必要である。",
    )

    add_heading(doc, "6. ADC・CMP_OUT・閾値自動調整", 1)
    add_heading(doc, "6.1 ENV_ADC信号強度", 2)
    add_table(
        doc,
        ["指標", "1 m", "5 m"],
        [
            ["VALID波形数", "11", "13"],
            ["基線中央値", "8.0 mV", "8.0 mV"],
            ["直接波ピーク中央値", "288.0 mV", "288.0 mV"],
            ["基線超過RMS中央値", "171.0 mV", "159.3 mV"],
            ["包絡線面積中央値", "69,124 mV·us", "63,730 mV·us"],
            ["基線+10 mV以上の幅", "366 us", "485 us"],
        ],
        [4200, 2580, 2580],
    )
    add_body(
        doc,
        "1 mと5 mのピークがともに約288 mVなのは、音響減衰がないためではない。"
        "基板仕様ではNJM2137の出力スイングと包絡線回路によりENV_ADC上限が約0.18〜0.43 Vと見積もられており、"
        "今回のVALID波形は圧縮域に入っている可能性が高い。"
    )
    add_figure(
        doc,
        images["adc_strength"],
        "図3　VALID波形のADC信号強度比較。ピーク、包絡線面積、持続幅を別々に評価。",
        width_in=6.25,
    )

    add_heading(doc, "6.2 TH_PWM自動調整", 2)
    add_body(
        doc,
        "TH_PWMは300 mVから開始し、NO_DETECTごとに20 mVずつ低下して60 mVまで探索した。"
        "有効信号を検出すると、基線とピークの中間を目標に最大50 mVずつ上昇する。ログ上、自動調整動作は仕様どおり確認できた。"
    )
    add_figure(
        doc,
        images["threshold"],
        "図4　ENV_ADCピークとTH_PWM使用値の推移。信号消失後は60 mVまで探索しても復帰しない区間がある。",
        width_in=6.35,
        page_break_before=True,
    )

    add_heading(doc, "6.3 CMP_OUT解析", 2)
    add_bullet(doc, "NO_DETECT 44件はすべてCMP_OUT立上り0。誤った遅延候補を棄却したのではなく、比較器が発火していない。")
    add_bullet(doc, "VALID時のscoreは24件すべて8、noise_high_pctは0。検出できたときのデジタル判定品質は良好。")
    add_bullet(doc, "閾値60 mVでも復帰しないため、単純な閾値低下より、信号経路・姿勢・指向性・接触・気泡・マルチパスヌルの確認が優先。")

    add_heading(doc, "7. 25 mまでの信号強度予測", 1)
    add_body(
        doc,
        "1 m・5 mのピーク値は圧縮されているため、2点から減衰係数を回帰しない。"
        "5 mの観測値を基準に、自由音場の球面拡散（音圧1/r）と200 kHz純水吸収を適用した。"
        "35℃における5 mから25 mまでの吸収損失は約0.11 dBで、約14 dBの幾何拡散が支配的である。"
    )
    add_table(
        doc,
        ["距離", "ENV_ADC名目予測", "ENV名目予測", "保守発火レベル82 mVとの比較"],
        [
            ["5 m", "288 mV", "576 mV", "+16.9 dB"],
            ["10 m", "148 mV", "296 mV", "+11.2 dB"],
            ["15 m", "101 mV", "202 mV", "+7.8 dB"],
            ["20 m", "78 mV", "156 mV", "+5.6 dB"],
            ["25 m", "63 mV", "127 mV", "+3.8 dB"],
        ],
        [1400, 2200, 2000, 3760],
    )
    add_figure(
        doc,
        images["prediction"],
        "図5　5 m実測を基準とした25 mまでのENVピーク予測。追加損失6 dBで25 mは保守発火レベルを下回る。",
        width_in=6.25,
    )
    add_table(
        doc,
        ["25 mシナリオ", "予測ENVピーク", "余裕", "閾値交差"],
        [
            ["球面拡散のみ", "126.6 mV", "+3.8 dB", "可能"],
            ["追加フェージング6 dB", "71.4 mV", "-1.2 dB", "不可"],
            ["追加フェージング12 dB", "43.8 mV", "-5.4 dB", "不可"],
        ],
        [3000, 2300, 1700, 2360],
        header_fill=LIGHT_BLUE,
    )
    add_callout(
        doc,
        "25 m判定",
        "INCONCLUSIVE。理想配置なら検出可能性はあるが、表面・底面反射、指向性、姿勢ずれなどで"
        "6 dB程度の追加損失が生じるだけで保守閾値を下回る。今回の低いVALID率から、安定受信を保証できない。",
        fill=LIGHT_GOLD,
        color=GOLD,
    )

    add_heading(doc, "8. 判定と考察", 1)
    add_table(
        doc,
        ["合格項目", "基準", "結果", "判定"],
        [
            ["VALID率", "90%以上", "1 m: 31.4%、5 m: 39.4%", "FAIL"],
            ["score", "7以上", "VALID 24件すべて8", "PASS"],
            ["ToF範囲", "200〜20,000 us", "VALIDは範囲内", "PASS"],
            ["距離再現性", "標準偏差10 cm以下", "2.61 cm / 3.07 cm", "PASS"],
            ["誤検出", "無送信時VALIDなし", "今回は無送信試験未実施", "未確認"],
            ["外部電源起動", "外部電源のみで起動・送信", "ログ単独では直接確認不可", "未確認"],
        ],
        [1700, 2300, 3160, 2200],
    )
    add_callout(
        doc,
        "総合判定",
        "FAIL。距離が検出されたときの再現性・score・ログ保存は良好だが、VALID率が仕様19章の90%基準を満たさない。",
        fill=LIGHT_RED,
        color=RED,
    )
    add_heading(doc, "8.1 主な原因候補", 2)
    add_bullet(doc, "振動子の向き・深さ・距離が固定治具で再現されていない。")
    add_bullet(doc, "表面・底面反射によるマルチパスと干渉ヌル。")
    add_bullet(doc, "振動子面の気泡、水面変動、降雨、ケーブルや端子の接触変化。")
    add_bullet(doc, "RX利得トリマ位置が未記録で、受信余裕と圧縮開始点が不明。")
    add_bullet(doc, "VALID信号は約0.29 Vで圧縮され、実際の距離減衰をピーク値から観測できない。")

    add_heading(doc, "9. 次回実験の推奨手順", 1)
    steps = [
        "RX基板単体へ既知振幅の200 kHz・5波バーストを入力し、最小検出入力とENV_ADC圧縮開始点を測定する。",
        "2個のRX利得トリマ位置を写真・抵抗値で記録し、以後固定する。",
        "TX/RX振動子を固定治具へ載せ、向き、深さ、実距離、振動子深度の水温を同時記録する。",
        "5 mで閾値が60 mVへ到達した後、30回以上連続測定してVALID率90%以上を確認する。",
        "10 m、15 m、20 m、25 mの順に伸ばし、各距離でENV_ADC、CMP_OUT、TH_PWM、距離結果を保存する。",
        "25 m開始直後は閾値300 mVから60 mVまで約60秒必要なため、探索完了後の送信から評価する。",
        "別試験として無送信状態を記録し、低閾値で誤VALIDが発生しないことを確認する。",
    ]
    for step in steps:
        add_bullet(doc, step, numbered=True)

    add_heading(doc, "付録A. 解析方法と参照資料", 1)
    add_heading(doc, "A.1 ファイル区分", 2)
    add_table(
        doc,
        ["区分", "ファイル", "用途"],
        [
            ["設置確認", "S000-S005", "主集計から除外"],
            ["1 m", "S006-S040", "35測定"],
            ["5 m", "S041-S073", "33測定"],
            ["波形解析", "ADC全データ.csv", "ENV_ADC時系列"],
            ["エッジ解析", "CMP_OUT全エッジ.csv", "比較器立上り・立下り"],
            ["統合結果", "測定統合解析.csv", "距離・ADC・CMP・閾値の統合"],
        ],
        [1800, 2600, 4960],
    )
    add_heading(doc, "A.2 ADC強度指標", 2)
    add_body(
        doc,
        "VALID波形について、到来時刻の100 us以上前を基線、到来20 us前から500 us後までを直接波窓とした。"
        "ピーク、基線超過RMS、正の基線超過分の時間積分、基線+10 mV以上の持続幅を算出した。"
    )
    add_heading(doc, "A.3 25 m予測式", 2)
    add_body(
        doc,
        "音圧振幅をA(r)=A(5 m)×(5/r)×10^(-α(r-5)/20)×10^(-Lfade/20)として計算した。"
        "ENV_ADCはENVの1/2として換算し、受信判定は自動探索下限60 mVに基板仕様のヒステリシス22 mVを"
        "全量加えた82 mVを保守発火レベルとした。"
    )
    add_heading(doc, "A.4 参照資料", 2)
    refs = [
        "AquaBeacon_仕様書.md / common/aquabeacon_config.h",
        "RX_improved_mini 基板仕様書.md",
        "NPL, Speed of sound in sea-water: https://resource.npl.co.uk/acoustics/techguides/soundseawater/underlying-phys.html",
        "Bilaniuk & Wong, Speed of sound in pure water, DOI: https://doi.org/10.1121/1.406819",
        "U.S. Navy, Fundamentals of Naval Weapons Systems: https://man.fas.org/dod-101/navy/docs/fun/part08.htm",
        "Ainslie & McColm (1998), JASA 103(3), DOI: https://doi.org/10.1121/1.421258",
    ]
    for ref in refs:
        add_bullet(doc, ref)

    add_heading(doc, "A.5 成果物", 2)
    add_body(
        doc,
        "本Wordレポートは、前提条件.md、簡易レポート.md、詳細分析.md、信号強度_25m評価.md、"
        "各集計CSVおよび5点のPNG図を統合した。元ログ.csv、ADC全データ.csv、CMP_OUT全エッジ.csvは"
        "再解析用データとして実験レポートフォルダに保持する。"
    )

    core = doc.core_properties
    core.title = "AquaBeacon 学校プール実験 統合技術レポート"
    core.subject = "200 kHz・5波バースト水中音響測距の1 m / 5 m実験解析"
    core.author = "AquaBeacon Project"
    core.keywords = "AquaBeacon, TD04A, 水中音響, 距離測定, ENV_ADC, CMP_OUT"
    core.comments = "Generated from verified SD-card experiment artifacts."

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("report_dir", type=Path)
    parser.add_argument("output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report_dir = args.report_dir.resolve()
    output = args.output.resolve()
    build_report(report_dir, output)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
