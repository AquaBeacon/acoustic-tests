from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
INPUT = ROOT / "analysis_input" / "pool_20260810"
OUT = ROOT / "reports" / "20260810_153810_プール実験データ分析"

VIDEO_START_JST = datetime.fromisoformat("2026-08-10T15:38:10+09:00")

BOUNDARIES = [
    ("setup_unclassified", 0, None),
    ("25m", 9 * 60 + 40, 25.0),
    ("20m_plus_cm", 12 * 60 + 30, 20.0),
    ("20m", 13 * 60 + 38, 20.0),
    ("12.5m", 15 * 60 + 5, 12.5),
    ("5m", 17 * 60 + 30, 5.0),
    ("lateral_A", 22 * 60 + 30, None),
    ("lateral_B", 23 * 60 + 57, None),
]

# Clear contiguous transmission runs visible in the logs. NO_DETECT blocks outside
# these ranges coincide with repositioning/off periods and are not counted as sent shots.
ACTIVE_RUNS = {
    "25m": (21, 37, 25.0),
    "20m_plus_cm": (44, 53, 20.0),
    "20m": (54, 59, 20.0),
    "12.5m": (64, 68, 12.5),
    "5m": (82, 87, 5.0),
    "lateral_A": (103, 109, 16.0),
    "lateral_B": (114, 124, float(np.hypot(16.0, 5.0))),
}


def parse_file(path: Path) -> dict:
    result = {"file": path.name, "size_bytes": path.stat().st_size}
    if path.stat().st_size == 0:
        result.update({"parse_status": "EMPTY", "utc": None})
        return result

    meta = {}
    header = None
    rows = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for line in f:
            line = line.rstrip("\r\n")
            if line.startswith("# ") and "," in line:
                k, v = line[2:].split(",", 1)
                meta[k] = v
            elif line.startswith("record_type,"):
                header = next(csv.reader([line]))
            elif header and line:
                vals = next(csv.reader([line]))
                if len(vals) == len(header):
                    rows.append(dict(zip(header, vals)))

    utc = None
    if meta.get("utc_iso"):
        utc = datetime.fromisoformat(meta["utc_iso"].replace("Z", "+00:00"))
    result.update({
        "parse_status": "OK",
        "utc": utc,
        "utc_iso": meta.get("utc_iso"),
        "seq": int(meta.get("seq", 0) or 0),
        "t_sync_us": int(meta.get("t_sync_us", 0) or 0),
        "t_detect_us": int(meta.get("t_detect_us", 0) or 0),
        "threshold_mv": float(meta.get("threshold_mv", "nan")),
        "threshold_next_mv": float(meta.get("threshold_next_mv", "nan")),
        "capture_samples": int(meta.get("capture_samples", 0) or 0),
        "capture_dropped": int(meta.get("capture_dropped", 0) or 0),
        "time_source": meta.get("time_source"),
        "time_packet_age_us": int(meta.get("time_packet_age_us", 0) or 0),
    })

    summary = next((r for r in rows if r["record_type"] == "SUMMARY"), None)
    if summary:
        for k in ["tof_us", "range_m", "score", "rx_peak_pct", "noise_high_pct", "status"]:
            result[k] = summary.get(k)
        for k in ["tof_us", "score", "rx_peak_pct", "noise_high_pct"]:
            result[k] = float(result[k]) if result[k] not in (None, "") else np.nan
        result["range_m"] = float(result["range_m"]) if result["range_m"] not in (None, "") else np.nan

    adc = [r for r in rows if r["record_type"] == "ADC"]
    if adc:
        t = np.array([float(r["t_from_sync_us"]) for r in adc])
        mv = np.array([float(r["env_adc_mv"]) for r in adc])
        result.update({
            "adc_n": len(mv),
            "adc_max_mv": float(np.max(mv)),
            "adc_p99_mv": float(np.percentile(mv, 99)),
            "adc_median_mv": float(np.median(mv)),
            "adc_mean_mv": float(np.mean(mv)),
            "adc_std_mv": float(np.std(mv, ddof=1)) if len(mv) > 1 else 0.0,
            "adc_peak_t_us": float(t[int(np.argmax(mv))]),
            "adc_above_threshold_n": int(np.sum(mv >= result["threshold_mv"])),
        })
    return result


def classify(utc: datetime | None) -> tuple[str, float | None, float | None]:
    if utc is None:
        return "unclassified", None, None
    elapsed = (utc - VIDEO_START_JST.astimezone(timezone.utc)).total_seconds()
    chosen = BOUNDARIES[0]
    for item in BOUNDARIES:
        if elapsed >= item[1]:
            chosen = item
        else:
            break
    return chosen[0], chosen[2], elapsed


def _font(size: int, bold: bool = False):
    candidates = [
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/meiryo.ttc"),
    ]
    for p in candidates:
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default()


def scatter_chart(df: pd.DataFrame, path: Path) -> None:
    w, h = 1600, 760
    left, top, right, bottom = 150, 90, 60, 160
    im = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(im)
    colors = {"25m": "#264653", "20m_plus_cm": "#2a9d8f", "20m": "#e9c46a", "12.5m": "#f4a261", "5m": "#e76f51"}
    xmin, xmax = df["utc"].min().timestamp(), df["utc"].max().timestamp()
    ymin, ymax = 0.0, max(22.0, float(df["adc_peak_t_us"].max() / 1000) * 1.05)
    px = lambda x: left + (x - xmin) / (xmax - xmin) * (w - left - right)
    py = lambda y: h - bottom - (y - ymin) / (ymax - ymin) * (h - top - bottom)
    d.line((left, top, left, h - bottom, w - right, h - bottom), fill="#333333", width=3)
    for y in np.linspace(ymin, ymax, 6):
        yy = py(y); d.line((left, yy, w-right, yy), fill="#dddddd", width=1)
        d.text((25, yy-12), f"{y:.1f}", font=_font(24), fill="#333333")
    for cond, g in df.groupby("condition", sort=False):
        for _, r in g.iterrows():
            x, y = px(r["utc"].timestamp()), py(r["adc_peak_t_us"] / 1000)
            c = colors.get(cond, "#777777"); d.ellipse((x-6, y-6, x+6, y+6), fill=c)
    for stamp in np.linspace(xmin, xmax, 5):
        xx = px(stamp)
        label = datetime.fromtimestamp(stamp, tz=timezone.utc).strftime("%H:%M:%S")
        d.line((xx, h-bottom, xx, h-bottom+8), fill="#333333", width=2)
        d.text((xx-45, h-bottom+14), label, font=_font(18), fill="#333333")
    d.text((left, 22), "ADC envelope peak time by measurement condition", font=_font(34, True), fill="#111111")
    d.text((20, 55), "Y: Peak time after SYNC (ms)", font=_font(23), fill="#333333")
    d.text((w//2-95, h-bottom+48), "X: UTC time (HH:MM:SS)", font=_font(21), fill="#333333")
    lx, ly = left, h-72
    for cond in colors:
        d.rectangle((lx, ly, lx+22, ly+22), fill=colors[cond]); d.text((lx+30, ly-3), cond, font=_font(22), fill="#222222"); lx += 235
    im.save(path)


def box_chart(df: pd.DataFrame, ordered: list[str], path: Path) -> None:
    w, h = 1500, 760; left, top, right, bottom = 130, 90, 50, 175
    im = Image.new("RGB", (w, h), "white"); d = ImageDraw.Draw(im)
    ymax = max(500.0, float(df["adc_max_mv"].max()) * 1.05); py = lambda y: h-bottom-y/ymax*(h-top-bottom)
    d.line((left, top, left, h-bottom, w-right, h-bottom), fill="#333333", width=3)
    for y in np.linspace(0, ymax, 6):
        yy=py(y); d.line((left,yy,w-right,yy),fill="#dddddd"); d.text((30,yy-12),f"{y:.0f}",font=_font(22),fill="#333333")
    step=(w-left-right)/len(ordered)
    for i, cond in enumerate(ordered):
        vals=df.loc[df["condition"]==cond,"adc_max_mv"].dropna().values
        x=left+step*(i+.5)
        if len(vals):
            q1,med,q3=np.percentile(vals,[25,50,75]); lo,hi=np.min(vals),np.max(vals)
            d.line((x,py(lo),x,py(hi)),fill="#264653",width=3)
            d.rectangle((x-45,py(q3),x+45,py(q1)),outline="#264653",fill="#dcebea",width=3)
            d.line((x-45,py(med),x+45,py(med)),fill="#e76f51",width=4)
        label=cond.replace("_"," "); d.text((x-65,h-bottom+25),label,font=_font(20),fill="#222222")
    d.text((left,22),"Maximum ADC envelope amplitude by condition",font=_font(34,True),fill="#111111")
    d.text((20,55),"Y: Maximum ADC envelope (mV)",font=_font(22),fill="#333333")
    d.text((w//2-105,h-bottom+67),"X: Measurement condition",font=_font(21),fill="#333333")
    # Legend for the box plot encoding.
    ly=h-55; lx=left
    d.line((lx,ly,lx+42,ly),fill="#e76f51",width=5); d.text((lx+54,ly-13),"median",font=_font(20),fill="#222222")
    lx+=250; d.rectangle((lx,ly-13,lx+42,ly+13),outline="#264653",fill="#dcebea",width=3)
    d.text((lx+54,ly-13),"25th-75th percentile",font=_font(20),fill="#222222")
    lx+=440; d.line((lx+21,ly-18,lx+21,ly+18),fill="#264653",width=3)
    d.line((lx+5,ly-18,lx+37,ly-18),fill="#264653",width=3); d.line((lx+5,ly+18,lx+37,ly+18),fill="#264653",width=3)
    d.text((lx+54,ly-13),"minimum-maximum",font=_font(20),fill="#222222")
    im.save(path)


def timeline_chart(df: pd.DataFrame, path: Path) -> None:
    w,h=1600,760; left,top,right,bottom=130,90,50,150
    im=Image.new("RGB",(w,h),"white"); d=ImageDraw.Draw(im)
    xmin,xmax=df["utc"].min().timestamp(),df["utc"].max().timestamp(); ymax=max(float(df["adc_max_mv"].max()),float(df["threshold_mv"].max()))*1.08
    px=lambda x:left+(x-xmin)/(xmax-xmin)*(w-left-right); py=lambda y:h-bottom-y/ymax*(h-top-bottom)
    d.line((left,top,left,h-bottom,w-right,h-bottom),fill="#333333",width=3)
    for y in np.linspace(0,ymax,6):
        yy=py(y); d.line((left,yy,w-right,yy),fill="#dddddd"); d.text((25,yy-12),f"{y:.0f}",font=_font(22),fill="#333333")
    pts1=[(px(r["utc"].timestamp()),py(r["adc_max_mv"])) for _,r in df.iterrows()]
    pts2=[(px(r["utc"].timestamp()),py(r["threshold_mv"])) for _,r in df.iterrows()]
    d.line(pts1,fill="#2a9d8f",width=3); d.line(pts2,fill="#e76f51",width=3)
    for x,y in pts1: d.ellipse((x-3,y-3,x+3,y+3),fill="#2a9d8f")
    for stamp in np.linspace(xmin, xmax, 5):
        xx=px(stamp); label=datetime.fromtimestamp(stamp,tz=timezone.utc).strftime("%H:%M:%S")
        d.line((xx,h-bottom,xx,h-bottom+8),fill="#333333",width=2)
        d.text((xx-45,h-bottom+12),label,font=_font(18),fill="#333333")
    d.text((left,22),"Signal amplitude and adaptive threshold over time",font=_font(34,True),fill="#111111")
    d.text((25,55),"Y: Voltage (mV)",font=_font(23),fill="#333333")
    d.text((w//2-95,h-bottom+50),"X: UTC time (HH:MM:SS)",font=_font(21),fill="#333333")
    d.line((left,h-55,left+45,h-55),fill="#2a9d8f",width=5); d.text((left+55,h-69),"ADC max",font=_font(22),fill="#222222")
    d.line((left+260,h-55,left+305,h-55),fill="#e76f51",width=5); d.text((left+315,h-69),"threshold",font=_font(22),fill="#222222")
    im.save(path)


def accuracy_chart(active: pd.DataFrame, path: Path) -> None:
    subset = active[active["condition"].isin(["25m", "20m", "12.5m", "5m"])].copy()
    w,h=1400,800; left,top,right,bottom=145,90,55,170
    im=Image.new("RGB",(w,h),"white"); d=ImageDraw.Draw(im)
    ymin,ymax=0,27; py=lambda y:h-bottom-(y-ymin)/(ymax-ymin)*(h-top-bottom)
    d.line((left,top,left,h-bottom,w-right,h-bottom),fill="#333333",width=3)
    for y in np.arange(0,26,5):
        yy=py(y); d.line((left,yy,w-right,yy),fill="#dddddd"); d.text((45,yy-12),f"{y}",font=_font(22),fill="#333333")
    step=(w-left-right)/len(subset)
    for i,(_,r) in enumerate(subset.iterrows()):
        x=left+step*(i+.5); expected=r["expected_distance_m"]; mean=r["range_mean_m"]; sd=r["range_std_m"]
        d.ellipse((x-15,py(expected)-15,x+15,py(expected)+15),outline="#264653",width=5)
        d.ellipse((x-13,py(mean)-13,x+13,py(mean)+13),fill="#e76f51")
        d.line((x,py(mean-sd),x,py(mean+sd)),fill="#e76f51",width=5)
        d.line((x-18,py(mean-sd),x+18,py(mean-sd)),fill="#e76f51",width=4)
        d.line((x-18,py(mean+sd),x+18,py(mean+sd)),fill="#e76f51",width=4)
        d.text((x-55,h-bottom+28),r["condition"],font=_font(23),fill="#222222")
    d.text((left,22),"Measured distance vs nominal distance (active runs)",font=_font(34,True),fill="#111111")
    d.text((30,55),"Y: Distance (m)",font=_font(23),fill="#333333")
    d.text((w//2-105,h-bottom+67),"X: Measurement condition",font=_font(21),fill="#333333")
    d.ellipse((left,h-62,left+24,h-38),outline="#264653",width=4); d.text((left+38,h-67),"nominal",font=_font(22),fill="#222222")
    d.ellipse((left+250,h-62,left+274,h-38),fill="#e76f51"); d.text((left+288,h-67),"measured mean +/- SD",font=_font(22),fill="#222222")
    im.save(path)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    records = [parse_file(p) for p in sorted(INPUT.glob("S*.CSV"))]
    for r in records:
        condition, expected_m, elapsed_s = classify(r.get("utc"))
        r["condition"] = condition
        r["expected_distance_m"] = expected_m
        r["video_elapsed_s"] = elapsed_s
        r["jst_iso"] = r["utc"].astimezone(VIDEO_START_JST.tzinfo).isoformat() if r.get("utc") else None
        if expected_m is not None:
            r["expected_tof_us"] = expected_m / 1500.0 * 1_000_000
            r["peak_time_error_us"] = r.get("adc_peak_t_us", np.nan) - r["expected_tof_us"]
        else:
            r["expected_tof_us"] = np.nan
            r["peak_time_error_us"] = np.nan

    df = pd.DataFrame(records)
    df = df.sort_values(["utc_iso", "file"], na_position="last")
    df.to_csv(OUT / "measurement_index.csv", index=False, encoding="utf-8-sig")

    usable = df[(df["parse_status"] == "OK") & (df["condition"] != "setup_unclassified")].copy()
    summaries = []
    for cond, g in usable.groupby("condition", sort=False):
        valid = g["status"].eq("VALID")
        expected = g["expected_distance_m"].dropna()
        measured_valid = g.loc[valid, "range_m"].dropna()
        peak_errors = g["peak_time_error_us"].dropna()
        summaries.append({
            "condition": cond,
            "expected_distance_m": expected.iloc[0] if len(expected) else np.nan,
            "n_files": len(g),
            "n_valid": int(valid.sum()),
            "valid_rate_pct": float(valid.mean() * 100),
            "status_counts": json.dumps(Counter(g["status"].fillna("MISSING")), ensure_ascii=False),
            "range_mean_m_valid": float(measured_valid.mean()) if len(measured_valid) else np.nan,
            "range_std_m_valid": float(measured_valid.std(ddof=1)) if len(measured_valid) > 1 else np.nan,
            "range_min_m_valid": float(measured_valid.min()) if len(measured_valid) else np.nan,
            "range_max_m_valid": float(measured_valid.max()) if len(measured_valid) else np.nan,
            "score_mean": float(g["score"].mean()),
            "score_min": float(g["score"].min()),
            "adc_max_mv_mean": float(g["adc_max_mv"].mean()),
            "adc_max_mv_median": float(g["adc_max_mv"].median()),
            "adc_max_mv_max": float(g["adc_max_mv"].max()),
            "adc_peak_t_us_mean": float(g["adc_peak_t_us"].mean()),
            "peak_time_error_us_mean": float(peak_errors.mean()) if len(peak_errors) else np.nan,
            "capture_dropped_total": int(g["capture_dropped"].sum()),
            "utc_start": g["utc_iso"].min(),
            "utc_end": g["utc_iso"].max(),
            "jst_start": g["jst_iso"].min(),
            "jst_end": g["jst_iso"].max(),
        })
    summary = pd.DataFrame(summaries)
    summary.to_csv(OUT / "condition_summary.csv", index=False, encoding="utf-8-sig")

    active_summaries = []
    for cond, (start_n, end_n, expected_m) in ACTIVE_RUNS.items():
        names = {f"S{i:03d}.CSV" for i in range(start_n, end_n + 1)}
        g = df[df["file"].isin(names)].copy()
        valid = g["status"].eq("VALID")
        measured = g.loc[valid, "range_m"].dropna()
        active_summaries.append({
            "condition": cond,
            "file_range": f"S{start_n:03d}-S{end_n:03d}",
            "expected_distance_m": expected_m,
            "n_sent": len(g),
            "n_valid": int(valid.sum()),
            "valid_rate_pct": float(valid.mean() * 100),
            "range_mean_m": float(measured.mean()),
            "range_std_m": float(measured.std(ddof=1)) if len(measured) > 1 else np.nan,
            "range_min_m": float(measured.min()),
            "range_max_m": float(measured.max()),
            "bias_m": float(measured.mean() - expected_m),
            "score_min": float(g["score"].min()),
            "score_mean": float(g["score"].mean()),
            "adc_max_mv_mean": float(g["adc_max_mv"].mean()),
            "utc_start": g["utc_iso"].min(),
            "utc_end": g["utc_iso"].max(),
            "jst_start": g["jst_iso"].min(),
            "jst_end": g["jst_iso"].max(),
        })
    active = pd.DataFrame(active_summaries)
    active.to_csv(OUT / "active_run_summary.csv", index=False, encoding="utf-8-sig")

    plot_df = usable[usable["expected_distance_m"].notna()]
    ordered = [b[0] for b in BOUNDARIES if b[0] != "setup_unclassified"]
    scatter_chart(plot_df, OUT / "peak_time_by_condition.png")
    box_chart(usable, ordered, OUT / "amplitude_by_condition.png")
    timeline_chart(usable, OUT / "signal_threshold_timeline.png")
    accuracy_chart(active, OUT / "distance_accuracy.png")

    overview = {
        "file_count": len(df),
        "empty_files": df.loc[df["parse_status"] == "EMPTY", "file"].tolist(),
        "utc_min": df["utc_iso"].dropna().min(),
        "utc_max": df["utc_iso"].dropna().max(),
        "jst_min": df["jst_iso"].dropna().min(),
        "jst_max": df["jst_iso"].dropna().max(),
        "status_counts": Counter(df["status"].fillna("MISSING")),
        "time_sources": Counter(df["time_source"].fillna("MISSING")),
        "active_total_sent": int(active["n_sent"].sum()),
        "active_total_valid": int(active["n_valid"].sum()),
        "active_valid_rate_pct": float(active["n_valid"].sum() / active["n_sent"].sum() * 100),
        "lateral_measured_delta_m": float(active.loc[active["condition"] == "lateral_B", "range_mean_m"].iloc[0] - active.loc[active["condition"] == "lateral_A", "range_mean_m"].iloc[0]),
        "lateral_expected_delta_m": float(np.hypot(16.0, 5.0) - 16.0),
    }
    (OUT / "overview.json").write_text(json.dumps(overview, ensure_ascii=False, indent=2, default=int), encoding="utf-8")
    print(summary.to_string(index=False))
    print(active.to_string(index=False))
    print(json.dumps(overview, ensure_ascii=False, indent=2, default=int))


if __name__ == "__main__":
    main()
