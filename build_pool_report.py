from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pandas as pd
from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from analysis_pool_20260810 import ACTIVE_RUNS


ROOT = Path(__file__).resolve().parent
IN_DIR = ROOT / "analysis_input" / "pool_20260810"
OUT = ROOT / "reports" / "20260810_153810_プール実験データ分析"
PYTHON = "Bundled Codex Python runtime"
BLUE = "2E74B5"
DARK = "1F4D78"
LIGHT = "F2F4F7"
WATER_TEMP_C = 32.5
# NPL Bilaniuk and Wong 112-point pure-water equation, atmospheric pressure.
SOUND_SPEED_TEMP_MPS = 1514.702858569
FIRMWARE_SOUND_SPEED_MPS = 1500.0
TEMP_DISTANCE_FACTOR = SOUND_SPEED_TEMP_MPS / FIRMWARE_SOUND_SPEED_MPS
NPL_SOUND_SPEED_URL = "https://resource.npl.co.uk/acoustics/techguides/soundpurewater/underlying-phys.html"


def set_cell_shading(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def set_cell_margins(cell, top=80, start=120, bottom=80, end=120) -> None:
    tc = cell._tc
    tc_pr = tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for m, v in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = tc_mar.find(qn(f"w:{m}"))
        if node is None:
            node = OxmlElement(f"w:{m}")
            tc_mar.append(node)
        node.set(qn("w:w"), str(v)); node.set(qn("w:type"), "dxa")


def set_repeat_table_header(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    tbl_header = OxmlElement("w:tblHeader")
    tbl_header.set(qn("w:val"), "true")
    tr_pr.append(tbl_header)


def set_table_widths(table, widths_in: list[float]) -> None:
    table.autofit = False
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    tbl_pr = table._tbl.tblPr
    tbl_w = tbl_pr.find(qn("w:tblW"))
    if tbl_w is None:
        tbl_w = OxmlElement("w:tblW"); tbl_pr.append(tbl_w)
    tbl_w.set(qn("w:w"), "9360"); tbl_w.set(qn("w:type"), "dxa")
    tbl_ind = tbl_pr.find(qn("w:tblInd"))
    if tbl_ind is None:
        tbl_ind = OxmlElement("w:tblInd"); tbl_pr.append(tbl_ind)
    tbl_ind.set(qn("w:w"), "120"); tbl_ind.set(qn("w:type"), "dxa")
    for row in table.rows:
        for i, cell in enumerate(row.cells):
            cell.width = Inches(widths_in[i])
            set_cell_margins(cell)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER


def font_run(run, size=None, bold=None, color=None) -> None:
    run.font.name = "Calibri"
    run._element.get_or_add_rPr().rFonts.set(qn("w:ascii"), "Calibri")
    run._element.get_or_add_rPr().rFonts.set(qn("w:hAnsi"), "Calibri")
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Yu Gothic")
    if size is not None: run.font.size = Pt(size)
    if bold is not None: run.bold = bold
    if color is not None: run.font.color.rgb = RGBColor.from_string(color)


def add_page_number(paragraph) -> None:
    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = paragraph.add_run("Page ")
    font_run(run, 9, color="666666")
    fld = OxmlElement("w:fldSimple"); fld.set(qn("w:instr"), "PAGE")
    paragraph._p.append(fld)


def add_table(doc, headers: list[str], rows: list[list[str]], widths: list[float], font_size=9):
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    set_table_widths(table, widths)
    set_repeat_table_header(table.rows[0])
    for i, h in enumerate(headers):
        set_cell_shading(table.rows[0].cells[i], LIGHT)
        p = table.rows[0].cells[i].paragraphs[0]
        p.paragraph_format.space_after = Pt(0)
        r = p.add_run(h); font_run(r, font_size, True, DARK)
    for vals in rows:
        cells = table.add_row().cells
        for i, value in enumerate(vals):
            p = cells[i].paragraphs[0]; p.paragraph_format.space_after = Pt(0)
            r = p.add_run(str(value)); font_run(r, font_size)
    return table


def add_bullet(doc, text: str) -> None:
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.space_after = Pt(4)
    r = p.add_run(text); font_run(r, 11)


def add_caption(doc, text: str) -> None:
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(2); p.paragraph_format.space_after = Pt(8)
    r = p.add_run(text); font_run(r, 9, color="666666")


def build_supporting_artifacts(df: pd.DataFrame, active: pd.DataFrame, overview: dict) -> None:
    result_cols = ["seq", "t_sync_us", "t_detect_us", "tof_us", "range_m", "score", "rx_peak_pct", "noise_high_pct", "status"]
    df.loc[df["parse_status"] == "OK", result_cols].to_csv(OUT / "実験結果.csv", index=False, encoding="utf-8-sig")

    temp_corrected = active.copy()
    temp_corrected["water_temperature_c"] = WATER_TEMP_C
    temp_corrected["firmware_sound_speed_mps"] = FIRMWARE_SOUND_SPEED_MPS
    temp_corrected["temperature_sound_speed_mps"] = SOUND_SPEED_TEMP_MPS
    temp_corrected["recorded_mean_distance_m"] = temp_corrected["range_mean_m"]
    temp_corrected["temperature_corrected_mean_distance_m"] = temp_corrected["range_mean_m"] * TEMP_DISTANCE_FACTOR
    temp_corrected["temperature_corrected_std_m"] = temp_corrected["range_std_m"] * TEMP_DISTANCE_FACTOR
    temp_corrected["temperature_corrected_bias_m"] = temp_corrected["temperature_corrected_mean_distance_m"] - temp_corrected["expected_distance_m"]
    temp_corrected.to_csv(OUT / "temperature_corrected_distance_summary.csv", index=False, encoding="utf-8-sig")

    with (OUT / "元ログ.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f); w.writerow(["source_file", "line_no", "raw_line"])
        for path in sorted(IN_DIR.glob("S*.CSV")):
            if path.stat().st_size == 0:
                w.writerow([path.name, 0, ""]); continue
            with path.open("r", encoding="utf-8-sig", errors="replace") as src:
                for i, line in enumerate(src, 1):
                    w.writerow([path.name, i, line.rstrip("\r\n")])

    sd_rows = []
    for path in sorted(IN_DIR.glob("S*.CSV")):
        data = path.read_bytes()
        row = df.loc[df["file"] == path.name]
        sd_rows.append({
            "file": path.name, "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "read_status": "EMPTY" if len(data) == 0 else "READ_OK",
            "utc_iso": "" if row.empty else (row.iloc[0].get("utc_iso") or ""),
            "measurement_status": "" if row.empty else (row.iloc[0].get("status") or ""),
        })
    pd.DataFrame(sd_rows).to_csv(OUT / "SD読出し.csv", index=False, encoding="utf-8-sig")

    preconditions = f"""# 前提条件

- 実験日: 2026-08-10（日本標準時）
- 動画開始時刻: 2026-08-10 15:38:10 JST（= 06:38:10 UTC）
- 目的: 直線距離測定（25 m、20 m+数cm、20 m、12.5 m、5 m）および横ずらし（A/B）の評価
- 時刻対応: SD内 `utc_iso` はUTCとして解釈し、JSTへは9時間加算
- 実験時水温: 32.5 ℃
- 水中音速: 1500.0 m/s（ファームウェア設定）。NPLの純水式による32.5 ℃の参考値は1514.70 m/s
- プール図の公称寸法: 縦16 m、A-B間5 m
- TX/RX設定: AquaBeacon mini-shot、GPS_NMEA/GPS_PPS時刻、ADCエンベロープ記録
- 元データ: `{IN_DIR}` の S000.CSV〜S124.CSV
- SDカード状態: 125ファイルを読み出し、124ファイルは内容あり、S042.CSVは0バイト
- 送信回数の扱い: 距離変更・移動中に連続するNO_DETECT区間は送信停止/準備区間と判断し、連続VALID区間62件を有効送信列として評価
- 配線・電源状態・外部電源単独起動: 提供データだけでは未確認
- 既知の制約: 20 m+数cmの実距離、A/Bの設置誤差、水深・塩素濃度は未記録
- 信号強度: ADCエンベロープの平滑化ピーク、背景雑音、SNR、直接波積分値で比較（音圧校正値ではない）
- マルチパス候補: 直接波後1.5 ms以降、背景雑音6σ以上かつ直接波の5%以上の独立ピーク。複数ショットで遅延が±0.4 ms以内に再現するものを反射波候補とした
"""
    (OUT / "前提条件.md").write_text(preconditions, encoding="utf-8")

    brief = f"""# 簡易レポート

## 目的

2026-08-10のプール実験について、動画経過時刻とSDカードUTC時刻を対応付け、測距成功率・精度・再現性・横ずらし応答・SD保存状態を評価する。

## 実施内容

動画開始 15:38:10 JSTを基準に各実験開始時刻をUTCへ変換し、125個のmini-shot CSVを区間分類した。連続VALID区間を実送信列として、距離平均・標準偏差・最小・最大・score・ADC最大値を集計した。Aは16 m、Bは sqrt(16^2+5^2)=16.763 mを公称距離とした。

## 結果

- SD読出し: 125件中124件成功、S042.CSVは0バイト
- 実送信: {int(active['n_sent'].sum())}件、VALID: {int(active['n_valid'].sum())}件、VALID率: 100.0%
- score: 全有効測定で8/8
- 距離再現性: 各条件の標準偏差 1.0〜2.7 cm（基準10 cm以下）
- 無送信/準備区間: NO_DETECT 62件、VALID誤検出0件
- 横ずらし: A平均16.886 m、B平均17.629 m、増分0.743 m。幾何学予測0.763 mとの差は-0.020 m
- 受信強度: 直線距離条件の直接波ピークは約250〜260 mV、横ずらしAは237 mV、Bは94 mV（B/A=39.5%）
- マルチパス候補: 20 m+数cm、20 m、5 mで直接波の6.3〜6.6 ms後に再現。余分な経路長は約9.5〜9.9 m
- score: 今回のburst5モードではVALID時に8、非VALID時に0を記録する互換値。信号品質の8段階評価ではない
- 水温補正: 32.5 ℃の純水音速1514.70 m/sを適用すると、記録距離は約0.980%増加。25 m平均は24.796 mから25.039 mになる

## 判定

測距性能は **PASS**。VALID率、score、ToF範囲、距離再現性、無送信時誤検出の仕様基準を満たした。SD保存健全性はS042.CSVが0バイトのため **FAIL**。

## 異常・考察

横ずらしA/Bはいずれも公称距離より約0.87 m長いが、A→Bの相対増分は2 cm以内で幾何学予測と一致した。32.5 ℃の音速補正をするとA/Bの絶対値はさらに約1%長くなるため、この共通オフセットは音速だけでは説明できず、固定遅延または実配置の確認が必要である。一方、25 m平均は温度補正により24.796 mから25.039 mとなり、約-0.20 mの偏りはほぼ解消する。S042.CSVの空ファイルはSD書込み中断またはファイル確定処理を要確認。

直線距離条件のピーク値が距離によらず約250 mV付近に集中するため、アナログ前段の圧縮または飽和により距離減衰が見えにくい可能性がある。BではピークがAの39.5%まで低下したがSNRは約38 dBあり、score 8を維持した。約6.3〜6.6 ms遅れのピークは複数距離で再現し、固定境界からのマルチパスと整合する。ただし水深・壁位置・振動子指向性が未記録のため、反射面の特定はできない。

## 次の確認事項

- 水温32.5 ℃に対応する音速を設定した上で、既知距離からSYSTEM_DELAY_USを再校正する
- A/BのTX・RX中心間距離を巻尺で再測定する
- SD書込み後のflush/close、電源断タイミング、空ファイル発生条件を確認する
- 次回は各条件の送信開始・停止を動画またはイベントログに明示する
"""
    (OUT / "簡易レポート.md").write_text(brief, encoding="utf-8")


def build_docx(df: pd.DataFrame, active: pd.DataFrame, overview: dict, signal: pd.DataFrame, clusters: pd.DataFrame) -> Path:
    doc = Document()
    sec = doc.sections[0]
    sec.top_margin = Inches(0.75); sec.bottom_margin = Inches(0.75)
    sec.left_margin = Inches(1); sec.right_margin = Inches(1)
    sec.header_distance = Inches(0.35); sec.footer_distance = Inches(0.35)

    styles = doc.styles
    normal = styles["Normal"]
    normal.font.name = "Calibri"; normal.font.size = Pt(11)
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Yu Gothic")
    normal.paragraph_format.space_after = Pt(6); normal.paragraph_format.line_spacing = 1.10
    for name, size, color, before, after in (("Heading 1",16,BLUE,16,8),("Heading 2",13,BLUE,12,6),("Heading 3",12,DARK,8,4)):
        st=styles[name]; st.font.name="Calibri"; st.font.size=Pt(size); st.font.bold=True; st.font.color.rgb=RGBColor.from_string(color)
        st._element.rPr.rFonts.set(qn("w:eastAsia"), "Yu Gothic")
        st.paragraph_format.space_before=Pt(before); st.paragraph_format.space_after=Pt(after); st.paragraph_format.keep_with_next=True

    hp = sec.header.paragraphs[0]
    hp.text = "AquaBeacon | 2026-08-10 プール実験"
    font_run(hp.runs[0], 9, color="666666")
    add_page_number(sec.footer.paragraphs[0])

    p = doc.add_paragraph(); p.paragraph_format.space_before=Pt(12); p.paragraph_format.space_after=Pt(4)
    r=p.add_run("AquaBeacon プール実験データ分析レポート"); font_run(r, 23, True, "000000")
    p=doc.add_paragraph(); p.paragraph_format.space_after=Pt(12)
    r=p.add_run("2026年8月10日 / 直線距離測定・横ずらし測定"); font_run(r, 14, color="444444")
    add_table(doc,["項目","内容"],[
        ["動画開始","2026-08-10 15:38:10 JST"],
        ["SD時刻","UTC（JSTより9時間遅い）"],
        ["水温・音速","32.5 ℃ / 設定1500.0 m/s（純水参考1514.70 m/s）"],
        ["解析対象","S000.CSV〜S124.CSV（125ファイル）"],
        ["総合所見","測距性能 PASS / SD保存健全性 FAIL（S042が0バイト）"],
    ],[1.25,5.25],10)

    doc.add_heading("要約", level=1)
    p=doc.add_paragraph()
    r=p.add_run("有効送信列62件はすべてVALIDで、各条件の距離標準偏差は1.0〜2.7 cmだった。"); font_run(r,11,True,DARK)
    add_bullet(doc,"VALID率90%以上、ToF 200〜20000 us、距離標準偏差10 cm以下を満たした。score=8はburst5のVALID互換値であり、独立した品質点ではない。")
    add_bullet(doc,"準備・送信停止とみられる62件はすべてNO_DETECTで、無送信時のVALID誤検出は0件だった。")
    add_bullet(doc,"横ずらしA→Bの測距増分0.743 mは、16 m×5 mの幾何学予測0.763 mと2.0 cm差で一致した。")
    add_bullet(doc,"水温32.5 ℃では純水音速が約1514.70 m/sとなる。1500 m/sで記録した距離を補正すると、25 m平均は24.796 mから25.039 mになる。")
    add_bullet(doc,"一方、A/Bの絶対距離は公称値より約0.87 m長く、固定遅延または実配置の再確認が必要。")
    add_bullet(doc,"SD読出しは124/125ファイル成功。S042.CSVのみ0バイトで、保存処理の健全性はFAILとした。")

    doc.add_heading("1. 時刻対応と区間分け", level=1)
    doc.add_paragraph("動画開始15:38:10 JSTを06:38:10 UTCへ変換し、動画経過時間を加算した。SDログ内のutc_isoはGPS由来のUTCであり、9時間加算したJST時刻が動画の実験順序と一致した。")
    time_rows = [
        ["25 m","09:40","15:47:50","06:47:50"], ["20 m+数cm","12:30","15:50:40","06:50:40"],
        ["20 m","13:38","15:51:48","06:51:48"], ["12.5 m","15:05","15:53:15","06:53:15"],
        ["5 m","17:30","15:55:40","06:55:40"], ["横ずらしA","22:30","16:00:40","07:00:40"],
        ["横ずらしB","23:57","16:02:07","07:02:07"],
    ]
    add_table(doc,["条件","動画経過","開始JST","開始UTC"],time_rows,[1.9,1.25,1.65,1.65],9)
    doc.add_picture(str(OUT / "実験時刻メモ.png"), width=Inches(6.4))
    add_caption(doc,"図1　動画から読み取った実験開始時刻とA/B配置メモ（提供画像）")

    doc.add_heading("2. データ処理方法", level=1)
    add_bullet(doc,"各CSVのメタデータ、SUMMARY行、ADC時系列を読み取り、UTC/JST・status・距離・score・ADC最大値を索引化した。")
    add_bullet(doc,"連続VALID列を実送信区間とした。VALID列の後にまとまるNO_DETECT列は、距離変更または送信停止区間として分離した。")
    add_bullet(doc,"直線距離は表示値を正解値とし、20 m+数cmは実測値が不明なため絶対誤差の確定判定から除外した。")
    add_bullet(doc,"横ずらしAは16 m、Bはsqrt(16^2+5^2)=16.763 mを公称値とした。")
    add_bullet(doc,"記録距離はファームウェア設定1500 m/sによる。水温32.5 ℃の参考補正にはNPLのBilaniuk and Wong純水式から1514.70 m/sを使用し、記録距離に1514.70/1500を乗じた。")

    doc.add_heading("3. 直線距離測定の結果", level=1)
    direct = active[active["condition"].isin(["25m","20m_plus_cm","20m","12.5m","5m"])]
    rows=[]
    labels={"25m":"25 m","20m_plus_cm":"20 m+数cm","20m":"20 m","12.5m":"12.5 m","5m":"5 m"}
    for _,r in direct.iterrows():
        bias = "参考" if r["condition"]=="20m_plus_cm" else f"{r['bias_m']:+.3f}"
        rows.append([labels[r["condition"]],f"{int(r['n_valid'])}/{int(r['n_sent'])}",f"{r['range_mean_m']:.3f}",f"{r['range_std_m']*100:.1f}",bias,"8（VALID固定）"])
    add_table(doc,["条件","VALID","平均距離(m)","標準偏差(cm)","偏り(m)","score（互換値）"],rows,[1.15,0.7,1.05,1.1,0.85,1.65],8.2)
    p=doc.add_paragraph(); p.paragraph_format.space_before=Pt(4); p.paragraph_format.space_after=Pt(7)
    r=p.add_run("scoreの意味（今回のburst5モード）: "); font_run(r,9.5,True,DARK)
    r=p.add_run("RXはコンパレータ出力を2 µs間隔で読み、3サンプル連続HIGHを検出すると到来を確定する。補正後の距離が0.3〜30 mならVALIDとなり、CSV互換のためscore=8を固定記録する。非VALIDはscore=0である。したがって表中の8は信号品質の段階評価ではなく、全件がVALIDだったことを表す。旧8ビットパターン方式では8ビット中の一致数がscoreだが、今回その方式は使用していない。"); font_run(r,9.5)
    doc.add_picture(str(OUT / "distance_accuracy.png"), width=Inches(6.4))
    add_caption(doc,"図2　有効送信区間の公称距離と測定平均（エラーバーは標準偏差）")
    doc.add_paragraph("20 m、12.5 m、5 mでは平均誤差が5 cm以内で、再現性も良好だった。25 mは平均24.796 mで約20.4 cm短く、距離依存の校正誤差、設置距離、反射波または音速設定の影響を追加確認する必要がある。")

    doc.add_heading("3.1 水温32.5 ℃による音速補正", level=2)
    doc.add_paragraph("実験時水温は32.5 ℃だった。NPL掲載のBilaniuk and Wong純水式（大気圧、0〜100 ℃）では音速は1514.70 m/sとなる。ファームウェア設定1500.0 m/sとの差は+0.980%であるため、同じToFから求める物理距離も記録値より約0.980%長くなる。元の実験結果は変更せず、参考補正値を以下に示す。")
    temp_rows=[]
    for _,r in direct.iterrows():
        corrected=r["range_mean_m"]*TEMP_DISTANCE_FACTOR
        corr_bias=corrected-r["expected_distance_m"]
        bias_text="参考" if r["condition"]=="20m_plus_cm" else f"{corr_bias:+.3f}"
        temp_rows.append([labels[r["condition"]],f"{r['range_mean_m']:.3f}",f"{corrected:.3f}",bias_text])
    add_table(doc,["条件","記録平均(m) @1500","32.5 ℃補正平均(m)","補正後偏り(m)"],temp_rows,[1.35,1.65,1.9,1.6],8.7)
    p=doc.add_paragraph(); p.paragraph_format.space_before=Pt(4); p.paragraph_format.space_after=Pt(6)
    r=p.add_run("解釈: "); font_run(r,9.5,True,DARK)
    r=p.add_run("25 mは補正後25.039 mとなり公称値との差が約+3.9 cmまで縮小する。一方、20 mは20.178 m、12.5 mは12.580 m、5 mは5.046 mとなる。公称距離の設置誤差や固定遅延も含まれるため、温度補正だけで全条件を完全には一致させられない。"); font_run(r,9.5)
    p=doc.add_paragraph(); p.paragraph_format.space_after=Pt(7)
    r=p.add_run("音速式の出典: "); font_run(r,8.5,True,"666666")
    r=p.add_run(NPL_SOUND_SPEED_URL); font_run(r,8.5,color="666666")

    doc.add_heading("4. 横ずらし測定の結果", level=1)
    a=active.loc[active["condition"]=="lateral_A"].iloc[0]; b=active.loc[active["condition"]=="lateral_B"].iloc[0]
    delta=b["range_mean_m"]-a["range_mean_m"]; expected_delta=b["expected_distance_m"]-a["expected_distance_m"]
    add_table(doc,["位置","公称距離(m)","平均(m)","標準偏差(cm)","偏り(m)","VALID"],[
        ["A",f"{a['expected_distance_m']:.3f}",f"{a['range_mean_m']:.3f}",f"{a['range_std_m']*100:.1f}",f"{a['bias_m']:+.3f}",f"{int(a['n_valid'])}/{int(a['n_sent'])}"],
        ["B",f"{b['expected_distance_m']:.3f}",f"{b['range_mean_m']:.3f}",f"{b['range_std_m']*100:.1f}",f"{b['bias_m']:+.3f}",f"{int(b['n_valid'])}/{int(b['n_sent'])}"],
    ],[0.7,1.25,1.05,1.35,1.0,0.9],9)
    p=doc.add_paragraph()
    r=p.add_run(f"A→Bの実測増分は{delta:.3f} m、幾何学予測は{expected_delta:.3f} m、差は{delta-expected_delta:+.3f} m（約{abs(delta-expected_delta)*100:.1f} cm）だった。"); font_run(r,11,True,DARK)
    corrected_delta = delta * TEMP_DISTANCE_FACTOR
    corrected_delta_error = corrected_delta - expected_delta
    doc.add_paragraph(
        f"32.5 ℃補正後のA→B増分は{corrected_delta:.3f} mで、幾何学予測との差は"
        f"{corrected_delta_error:+.3f} m（約{abs(corrected_delta_error)*100:.1f} cm）となる。"
        f"A/Bとも絶対値には大きな共通オフセットがあり、温度補正後はA=約"
        f"{a['range_mean_m']*TEMP_DISTANCE_FACTOR:.3f} m、B=約"
        f"{b['range_mean_m']*TEMP_DISTANCE_FACTOR:.3f} mとなるため、絶対値のずれは音速だけでは説明できない。"
        "固定遅延または実配置を再確認する必要がある。"
    )

    doc.add_heading("5. 信号・送信状態", level=1)
    doc.add_picture(str(OUT / "signal_threshold_timeline.png"), width=Inches(6.4))
    add_caption(doc,"図3　ADC最大値と自動しきい値の時系列。信号列と準備/停止列が明瞭に分離している")
    doc.add_picture(str(OUT / "amplitude_by_condition.png"), width=Inches(6.4))
    add_caption(doc,"図4　条件別ADC最大値分布。オレンジ線=中央値、箱=25〜75パーセンタイル、ひげ=最小〜最大。区間全体を含み、低値群は準備/停止中のNO_DETECT")
    doc.add_paragraph("図4のオレンジ線は各条件におけるADC最大値の中央値である。箱の下端・上端はそれぞれ25・75パーセンタイル、縦のひげは最小値から最大値までを表す。有効送信列のADC最大値平均は約219〜289 mVだった。低いADC最大値が連続する区間はNO_DETECTと一致しており、移動中または送信停止中のデータを有効測定から分離できる。")

    doc.add_page_break()
    doc.add_heading("6. 受信信号強度の比較", level=1)
    doc.add_paragraph("各有効ショットについて、SUMMARYのToF付近±約0.5 msから平滑化したADCエンベロープの直接波ピークを求めた。背景雑音は到来前区間の中央値とMADから推定し、SNRと直接波周辺の積分強度も算出した。以下は受信回路出力の比較であり、校正済み音圧ではない。")
    sig_labels={
        "25m":"25.0 m", "20m_plus_cm":"20.0 m+数cm", "20m":"20.0 m", "12.5m":"12.5 m",
        "5m":"5.0 m", "lateral_A":"16.0 m（offset 0.0 m）", "lateral_B":"16.7 m（offset 5.0 m）",
    }
    sig_rows=[]
    for _,r in signal.iterrows():
        sig_rows.append([sig_labels[r.condition],f"{r.direct_peak_mv_mean:.1f} ± {r.direct_peak_mv_std:.1f}",f"{r.snr_db_mean:.1f}",f"{r.direct_area_mv_ms_mean:.1f}"])
    add_table(doc,["条件","直接波ピーク(mV)","平均SNR(dB)","積分強度(mV・ms)"],sig_rows,[1.8,1.55,1.2,1.95],8.5)
    doc.add_picture(str(OUT / "direct_signal_strength_v3.png"), width=Inches(6.4))
    add_caption(doc,"図5　条件別の直接波ピーク電圧（平均±標準偏差）")
    a_sig=signal.loc[signal.condition=="lateral_A"].iloc[0]; b_sig=signal.loc[signal.condition=="lateral_B"].iloc[0]
    ratio=b_sig.direct_peak_mv_mean/a_sig.direct_peak_mv_mean*100
    p=doc.add_paragraph(); r=p.add_run(f"16.7 m（offset 5.0 m）の直接波ピークは平均{b_sig.direct_peak_mv_mean:.1f} mVで、16.0 m（offset 0.0 m）の{ratio:.1f}%まで低下した。")
    font_run(r,11,True,DARK)
    doc.add_paragraph("offset 5.0 m条件は斜距離の増加が約0.76 mに留まる一方、信号強度は約60%低下しているため、距離減衰だけでなく振動子の指向性、設置角度、遮蔽または干渉の影響が大きいと考えられる。ただし平均SNRは約38 dBで、全件VALIDを維持した。直線距離条件のピークは約250〜260 mVに集中し、距離による単調な減衰が見えない。アナログ前段の圧縮・クリッピング・自動しきい値制御の影響を確認する必要がある。")

    doc.add_heading("7. マルチパス候補の観測", level=1)
    doc.add_paragraph("直接波の立上り・振動子リンギングを除外するため、直接波ピーク後1.5 ms以降を探索した。背景雑音の6σ以上かつ直接波振幅の5%以上で、周囲から独立したピークを候補とした。さらに同一条件の複数ショットで遅延が±0.4 ms以内に再現するピーク群を、再現性のあるマルチパス候補とした。")
    cluster_rows=[]
    for _,r in clusters.iterrows():
        corrected_extra_path = r.median_delay_us / 1_000_000 * SOUND_SPEED_TEMP_MPS
        cluster_rows.append([sig_labels[r.condition],f"{r.median_delay_us/1000:.3f}",f"{corrected_extra_path:.3f}",f"{int(r.shots_observed)}/{int(r.n_shots)}",f"{r.median_relative_amplitude*100:.1f}%"])
    add_table(doc,["条件","遅延(ms)","余分経路(m,32.5℃)","観測率","直接波比"],cluster_rows,[1.5,1.05,1.55,1.0,1.1],8.8)
    doc.add_picture(str(OUT / "multipath_candidates.png"), width=Inches(6.4))
    add_caption(doc,"図6　各ショットで最も強い直接波後ピーク候補。灰線は直接波比20%")
    doc.add_picture(str(OUT / "representative_waveforms.png"), width=Inches(6.4))
    add_caption(doc,"図7　代表受信波形。赤線=直接波、紫線=反射波候補（検出された場合）")
    doc.add_paragraph("20 m+数cm、20 m、5 mの3条件で、約6.3〜6.6 ms遅れのピークが83〜90%のショットに再現した。水温32.5 ℃の純水参考音速1514.70 m/sでは余分な経路長9.56〜10.01 mに相当し、同じプール境界からの反射という仮説と整合する。直接波比は中央値8.6〜15.1%だった。")
    add_bullet(doc,"25 mは直接波後の記録余裕が約5.5 msしかなく、約6.5 ms遅れの反射は記録窓外になるため、未観測でも否定材料にならない。")
    add_bullet(doc,"12.5 mと横ずらしA/Bでは再現性のある候補を検出しなかった。反射経路の指向性、干渉、反射面との幾何関係が条件で変わった可能性がある。")
    add_bullet(doc,"反射面を特定するには、水深、壁までの距離、振動子深度・向きが必要。余分な経路約9.7 mは、直接経路から約4.8〜5.0 m離れた境界への往復差とも整合し得るが、現データだけでは断定できない。")

    doc.add_page_break()
    doc.add_heading("8. 仕様基準による判定", level=1)
    active_df=df[df["file"].isin({f"S{i:03d}.CSV" for _,(s,e,_) in ACTIVE_RUNS.items() for i in range(s,e+1)})]
    tof_min=active_df["tof_us"].min(); tof_max=active_df["tof_us"].max(); max_sd=active["range_std_m"].max()
    add_table(doc,["項目","基準","結果","判定"],[
        ["VALID率",">=90%",f"62/62 = 100%","PASS"], ["score",">=7（旧方式）","全件8（VALID固定値）","参考*"],
        ["ToF","200〜20000 us",f"{tof_min:.0f}〜{tof_max:.0f} us","PASS"],
        ["距離再現性","標準偏差<=10 cm",f"最大{max_sd*100:.1f} cm","PASS"],
        ["無送信時誤検出","VALIDなし","NO_DETECT 62件、VALID 0件","PASS"],
        ["SD読出し","全件読出し","124/125、S042は0バイト","FAIL"],
    ],[1.25,1.45,2.65,0.75],9)
    p=doc.add_paragraph(); p.paragraph_format.space_before=Pt(3); p.paragraph_format.space_after=Pt(6)
    r=p.add_run("* score判定注記: "); font_run(r,9,True,DARK)
    r=p.add_run("burst5ではscoreはVALIDなら8、非VALIDなら0となるCSV互換値で、独立した品質判定ではない。実質的な検出条件はコンパレータを2 µs間隔で読み、3回連続HIGHを確認し、距離が0.3〜30 mに入ることである。"); font_run(r,9)

    doc.add_heading("9. 異常・考察", level=1)
    add_bullet(doc,"S042.CSVは0バイト。前後のS041/S043は読めるため、単発の書込み未完了、flush/close不足、または電源/カード接触の一時異常が候補。")
    add_bullet(doc,"A/Bの共通オフセットは32.5 ℃の音速補正後も残り、むしろ絶対距離は約1%長くなる。SYSTEM_DELAY_US、実配置、水面・壁反射などを切り分ける必要がある。")
    add_bullet(doc,"25 m平均は32.5 ℃補正により24.796 mから25.039 mとなり、約-0.20 mの偏りがほぼ解消する。他条件との違いを確かめるため、公称距離と振動子中心位置の再測定が重要。")
    add_bullet(doc,"動画メモ時刻と最初の有効ログには数秒〜約35秒の差がある条件がある。メモは配置開始、ログは送信開始を示す可能性がある。")
    add_bullet(doc,"マルチパス候補の遅延は再現したが、受信窓22 msのため長距離条件ほど観測可能な反射遅延が短い。条件間比較では観測窓の打切りを考慮する必要がある。")

    doc.add_heading("10. 次回実験への提案", level=1)
    for text in [
        "各条件で『送信開始』『送信停止』をシリアルイベントと動画の両方に記録し、予定送信回数を確定する。",
        "5 m程度の既知距離で固定遅延を校正後、20 m・25 mで線形性を再確認する。",
        "水温32.5 ℃に対応する音速を設定し、TX/RX振動子中心間距離を巻尺で記録する。水温センサの測定誤差も残す。",
        "SD書込みごとにflushし、ファイルclose完了後にLEDまたはログで完了を表示する。電源断試験で空ファイル再現性を確認する。",
        "横ずらしはA/Bを各10回以上測定し、絶対距離と差分距離を別々に評価する。",
        "水深・各壁までの距離・振動子深度と向きを記録し、予測反射経路と観測遅延を照合する。受信窓を延長したマルチパス専用測定も行う。",
        "信号強度比較用にはADC前段の飽和を避け、固定ゲインで記録する。可能なら既知入力でmVから音圧への校正を行う。",
    ]: add_bullet(doc,text)

    doc.add_heading("付録A. データ完全性", level=1)
    add_table(doc,["項目","値"],[
        ["SDファイル数","125"], ["内容あり","124"], ["空ファイル","S042.CSV（0バイト）"],
        ["記録期間UTC",f"{overview['utc_min']}〜{overview['utc_max']}"], ["時刻源","GPS_NMEA 108件 / GPS_PPS 16件"],
        ["水温・参考音速",f"{WATER_TEMP_C:.1f} ℃ / {SOUND_SPEED_TEMP_MPS:.2f} m/s（NPL純水式）"],
        ["解析成果物","実験結果.csv、元ログ.csv、SD読出し.csv、温度補正・信号・マルチパス集計CSV、グラフPNG"],
    ],[1.7,4.8],9)
    doc.add_paragraph("各SDファイルのSHA-256、サイズ、読出し状態はSD読出し.csvに記録した。元ログ.csvには全ファイルの各行をファイル名・行番号付きで保存した。")

    out = OUT / "AquaBeacon_プール実験データ分析レポート_20260810_v3.docx"
    doc.save(out)
    return out


def main() -> None:
    df = pd.read_csv(OUT / "measurement_index.csv")
    active = pd.read_csv(OUT / "active_run_summary.csv")
    overview = json.loads((OUT / "overview.json").read_text(encoding="utf-8"))
    signal = pd.read_csv(OUT / "signal_multipath_summary.csv")
    clusters = pd.read_csv(OUT / "multipath_recurring_clusters.csv")
    build_supporting_artifacts(df, active, overview)
    print(build_docx(df, active, overview, signal, clusters))


if __name__ == "__main__":
    main()
