from __future__ import annotations

import csv
import math
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw

from analysis_pool_20260810 import ACTIVE_RUNS, _font


ROOT = Path(__file__).resolve().parent
INPUT = ROOT / "analysis_input" / "pool_20260810"
OUT = ROOT / "reports" / "20260810_153810_プール実験データ分析"


def load_shot(path: Path):
    meta = {}
    header = None
    rows = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for line in f:
            line = line.rstrip("\r\n")
            if line.startswith("# ") and "," in line:
                k, v = line[2:].split(",", 1); meta[k] = v
            elif line.startswith("record_type,"):
                header = next(csv.reader([line]))
            elif header and line.startswith("ADC,"):
                vals = next(csv.reader([line]))
                if len(vals) == len(header): rows.append(dict(zip(header, vals)))
    t = np.array([float(r["t_from_sync_us"]) for r in rows], dtype=float)
    mv = np.array([float(r["env_adc_mv"]) for r in rows], dtype=float)
    return meta, t, mv


def moving_average(y, n=7):
    if len(y) < n: return y.copy()
    return np.convolve(y, np.ones(n) / n, mode="same")


def find_separated_peaks(t, y, start_us, threshold, min_sep_us=500):
    idx = np.flatnonzero((t >= start_us) & (t <= 21900))
    local = [i for i in idx[1:-1] if y[i] >= y[i-1] and y[i] > y[i+1] and y[i] >= threshold]
    ranked = sorted(local, key=lambda i: y[i], reverse=True)
    chosen = []
    for i in ranked:
        if all(abs(t[i] - t[j]) >= min_sep_us for j in chosen):
            left = y[(t >= t[i]-300) & (t < t[i]-80)]
            right = y[(t > t[i]+80) & (t <= t[i]+300)]
            shoulder = max(float(np.min(left)) if len(left) else y[i], float(np.min(right)) if len(right) else y[i])
            if y[i] - shoulder >= 3:
                chosen.append(i)
    return sorted(chosen, key=lambda i: t[i])


def analyze_one(condition, file_num, expected_m):
    path = INPUT / f"S{file_num:03d}.CSV"
    meta, t, mv = load_shot(path)
    tof = float(meta.get("t_detect_us", 0)) - float(meta.get("t_sync_us", 0))
    if tof <= 0:
        # SUMMARY tof already includes configured system delay and is the relevant arrival estimate.
        tof = expected_m / 1500 * 1e6
    # The SUMMARY row's reported tof is more robust than local timer subtraction across wrap.
    with path.open("r", encoding="utf-8-sig") as f:
        for line in f:
            if line.startswith("SUMMARY,"):
                parts = next(csv.reader([line])); tof = float(parts[7]); break
    smooth = moving_average(mv, 7)
    baseline_mask = (t >= 250) & (t <= min(2200, max(800, tof - 1200)))
    baseline = mv[baseline_mask]
    noise_med = float(np.median(baseline))
    mad = float(np.median(np.abs(baseline - noise_med)))
    noise_sigma = max(1.0, 1.4826 * mad)
    direct_mask = (t >= tof - 350) & (t <= tof + 650)
    direct_peak = float(np.max(smooth[direct_mask]))
    direct_peak_t = float(t[direct_mask][int(np.argmax(smooth[direct_mask]))])
    excess = max(0.1, direct_peak - noise_med)
    area_mask = (t >= tof - 350) & (t <= tof + 850)
    area = float(np.trapezoid(np.maximum(mv[area_mask] - noise_med, 0), t[area_mask]) / 1000)
    snr_db = 20 * math.log10(excess / noise_sigma)
    # Exclude the first 1.5 ms after the direct arrival to avoid classifying
    # transducer/envelope ringing as an environmental reflection.
    search_start = tof + 1500
    threshold = noise_med + max(6 * noise_sigma, 0.05 * excess, 8)
    peak_idx = find_separated_peaks(t, smooth, search_start, threshold)
    candidates = [{"t_us": float(t[i]), "delay_us": float(t[i]-direct_peak_t), "mv": float(smooth[i]), "ratio": float((smooth[i]-noise_med)/excess)} for i in peak_idx]
    strongest = max(candidates, key=lambda x: x["ratio"], default=None)
    return {
        "condition": condition, "file": path.name, "file_num": file_num, "expected_distance_m": expected_m,
        "tof_us": tof, "noise_median_mv": noise_med, "noise_sigma_mv": noise_sigma,
        "direct_peak_mv": direct_peak, "direct_peak_t_us": direct_peak_t,
        "direct_excess_mv": excess, "snr_db": snr_db, "direct_area_mv_ms": area,
        "multipath_candidate_count": len(candidates),
        "strongest_multipath_delay_us": np.nan if strongest is None else strongest["delay_us"],
        "strongest_multipath_extra_path_m": np.nan if strongest is None else strongest["delay_us"] / 1e6 * 1500,
        "strongest_multipath_mv": np.nan if strongest is None else strongest["mv"],
        "strongest_multipath_ratio": np.nan if strongest is None else strongest["ratio"],
        "candidate_list": ";".join(f"{x['delay_us']:.0f}us/{x['ratio']:.2f}" for x in candidates),
        "_t": t, "_mv": mv, "_smooth": smooth, "_candidates": candidates,
    }


def strength_chart(summary, path):
    w,h=1500,760; left,top,right,bottom=140,90,55,140
    im=Image.new("RGB",(w,h),"white"); d=ImageDraw.Draw(im)
    ymax=max(320, float((summary.direct_peak_mv_mean + summary.direct_peak_mv_std).max())*1.12)
    py=lambda y:h-bottom-y/ymax*(h-top-bottom)
    d.line((left,top,left,h-bottom,w-right,h-bottom),fill="#333333",width=3)
    for y in np.linspace(0,ymax,6):
        yy=py(y); d.line((left,yy,w-right,yy),fill="#dddddd"); d.text((35,yy-12),f"{y:.0f}",font=_font(22),fill="#333333")
    step=(w-left-right)/len(summary)
    colors=["#264653","#2a9d8f","#e9c46a","#f4a261","#e76f51","#457b9d","#6d597a"]
    labels={
        "25m":"25.0 m", "20m_plus_cm":"20.0 m\n+ several cm", "20m":"20.0 m",
        "12.5m":"12.5 m", "5m":"5.0 m", "lateral_A":"16.0 m\n(offset 0.0 m)",
        "lateral_B":"16.7 m\n(offset 5.0 m)",
    }
    for i,(_,r) in enumerate(summary.iterrows()):
        x=left+step*(i+.5); mean=r.direct_peak_mv_mean; sd=r.direct_peak_mv_std
        d.rectangle((x-42,py(mean),x+42,h-bottom),fill=colors[i],outline=colors[i])
        d.line((x,py(mean-sd),x,py(mean+sd)),fill="#222222",width=4)
        d.line((x-18,py(mean-sd),x+18,py(mean-sd)),fill="#222222",width=4)
        d.line((x-18,py(mean+sd),x+18,py(mean+sd)),fill="#222222",width=4)
        label=labels.get(r.condition,r.condition.replace("_"," "))
        lines=label.split("\n")
        for line_no,line in enumerate(lines):
            box=d.textbbox((0,0),line,font=_font(17)); text_w=box[2]-box[0]
            d.text((x-text_w/2,h-bottom+20+line_no*24),line,font=_font(17),fill="#222222")
        d.text((x-39,py(mean)-34),f"{mean:.1f}",font=_font(18,True),fill="#222222")
    d.text((left,22),"Direct-arrival signal strength by condition",font=_font(34,True),fill="#111111")
    d.text((28,55),"Y: Smoothed direct-arrival peak (mV), mean +/- SD",font=_font(21),fill="#333333")
    d.text((w//2-105,h-bottom+70),"X: Measurement condition",font=_font(21),fill="#333333")
    im.save(path)


def multipath_chart(shots, path):
    cand=shots.dropna(subset=["strongest_multipath_delay_us"])
    w,h=1500,760; left,top,right,bottom=140,90,55,170
    im=Image.new("RGB",(w,h),"white"); d=ImageDraw.Draw(im)
    xmax=max(6.0,float(cand.strongest_multipath_delay_us.max()/1000)*1.08 if len(cand) else 6)
    ymax=max(1.0,float(cand.strongest_multipath_ratio.max())*1.12 if len(cand) else 1)
    px=lambda x:left+x/xmax*(w-left-right); py=lambda y:h-bottom-y/ymax*(h-top-bottom)
    d.line((left,top,left,h-bottom,w-right,h-bottom),fill="#333333",width=3)
    for y in np.linspace(0,ymax,6):
        yy=py(y); d.line((left,yy,w-right,yy),fill="#dddddd"); d.text((35,yy-12),f"{y:.1f}",font=_font(22),fill="#333333")
    colors={c:col for c,col in zip(ACTIVE_RUNS,["#264653","#2a9d8f","#e9c46a","#f4a261","#e76f51","#457b9d","#6d597a"])}
    for _,r in cand.iterrows():
        x=px(r.strongest_multipath_delay_us/1000); y=py(r.strongest_multipath_ratio); c=colors[r.condition]
        d.ellipse((x-7,y-7,x+7,y+7),fill=c)
    d.line((left,py(.2),w-right,py(.2)),fill="#888888",width=2)
    d.text((left,22),"Strongest post-arrival peak candidates",font=_font(34,True),fill="#111111")
    d.text((25,55),"Y: Relative amplitude (ratio; direct arrival = 1.0)",font=_font(21),fill="#333333")
    d.text((w//2-130,h-82),"X: Delay after direct peak (ms)",font=_font(21),fill="#333333")
    lx,ly=left,h-55
    for cond,col in colors.items():
        d.rectangle((lx,ly,lx+18,ly+18),fill=col); d.text((lx+25,ly-4),cond,font=_font(17),fill="#222222"); lx+=180
    im.save(path)


def representative_chart(records, path):
    conds=list(ACTIVE_RUNS)
    reps=[]
    for cond in conds:
        vals=[r for r in records if r["condition"]==cond]
        med=np.median([r["direct_peak_mv"] for r in vals])
        reps.append(min(vals,key=lambda r:abs(r["direct_peak_mv"]-med)))
    w,h=1600,1250; left,top,right,bottom=175,75,55,85; panel_h=(h-top-bottom)/len(reps)
    im=Image.new("RGB",(w,h),"white"); d=ImageDraw.Draw(im)
    d.text((left,15),"Representative received envelopes and post-arrival candidates",font=_font(32,True),fill="#111111")
    for j,r in enumerate(reps):
        y0=top+j*panel_h; y1=y0+panel_h-22; t=r["_t"]/1000; mv=r["_smooth"]
        ymin=0; ymax=max(320,float(np.max(mv))*1.05); px=lambda x:left+x/22*(w-left-right); py=lambda y:y1-y/ymax*(y1-y0)
        d.line((left,y0,left,y1,w-right,y1),fill="#777777",width=1)
        d.text((left-48,y0-3),f"{ymax:.0f}",font=_font(14),fill="#555555")
        d.text((left-22,y1-18),"0",font=_font(14),fill="#555555")
        pts=[(px(x),py(y)) for x,y in zip(t,mv)]; d.line(pts,fill="#2a9d8f",width=2)
        xd=px(r["direct_peak_t_us"]/1000); d.line((xd,y0,xd,y1),fill="#e76f51",width=3)
        for c in r["_candidates"]:
            xc=px(c["t_us"]/1000); d.line((xc,y0,xc,y1),fill="#6d597a",width=2)
        d.text((10,y0+12),r["condition"],font=_font(20,True),fill="#222222")
        d.text((10,y0+42),r["file"],font=_font(17),fill="#555555")
    for x_ms in [0,5,10,15,20]:
        xx=left+x_ms/22*(w-left-right)
        d.line((xx,h-bottom,xx,h-bottom+7),fill="#333333",width=2)
        d.text((xx-10,h-bottom+10),str(x_ms),font=_font(16),fill="#333333")
    d.text((w//2-105,h-42),"X: Time after SYNC (ms)",font=_font(21),fill="#333333")
    label=Image.new("RGBA",(320,36),(255,255,255,0)); ld=ImageDraw.Draw(label)
    ld.text((0,0),"Y: Smoothed ADC envelope (mV)",font=_font(20),fill="#333333")
    label=label.rotate(90,expand=True)
    im.paste(label,(75,(h-label.height)//2),label)
    im.save(path)


def main():
    records=[]
    for cond,(start,end,expected) in ACTIVE_RUNS.items():
        for n in range(start,end+1): records.append(analyze_one(cond,n,expected))
    export=pd.DataFrame([{k:v for k,v in r.items() if not k.startswith("_")} for r in records])
    export.to_csv(OUT/"signal_multipath_per_shot.csv",index=False,encoding="utf-8-sig")
    summary=[]
    for cond,g in export.groupby("condition",sort=False):
        summary.append({
            "condition":cond,"n":len(g),
            "direct_peak_mv_mean":g.direct_peak_mv.mean(),"direct_peak_mv_std":g.direct_peak_mv.std(ddof=1),
            "direct_peak_mv_min":g.direct_peak_mv.min(),"direct_peak_mv_max":g.direct_peak_mv.max(),
            "snr_db_mean":g.snr_db.mean(),"direct_area_mv_ms_mean":g.direct_area_mv_ms.mean(),
            "shots_with_multipath_candidate":int((g.multipath_candidate_count>0).sum()),
            "multipath_candidate_rate_pct":float((g.multipath_candidate_count>0).mean()*100),
            "shots_with_strong_candidate":int((g.strongest_multipath_ratio>=0.15).sum()),
            "strong_candidate_rate_pct":float((g.strongest_multipath_ratio>=0.15).mean()*100),
            "strongest_delay_us_median":g.strongest_multipath_delay_us.median(),
            "strongest_extra_path_m_median":g.strongest_multipath_extra_path_m.median(),
            "strongest_ratio_median":g.strongest_multipath_ratio.median(),
        })
    summary=pd.DataFrame(summary)
    summary.to_csv(OUT/"signal_multipath_summary.csv",index=False,encoding="utf-8-sig")

    # Cluster recurring candidate delays within each condition. A repeatable
    # delay across independent shots is stronger multipath evidence than one peak.
    clusters=[]
    for cond in ACTIVE_RUNS:
        pts=[]
        for r in records:
            if r["condition"]==cond:
                for c in r["_candidates"]: pts.append((c["delay_us"],c["ratio"],r["file"]))
        groups=[]
        for pt in sorted(pts):
            placed=False
            for group in groups:
                if abs(pt[0]-np.median([x[0] for x in group]))<=400:
                    group.append(pt); placed=True; break
            if not placed: groups.append([pt])
        nshots=sum(1 for r in records if r["condition"]==cond)
        for group in groups:
            files=sorted(set(x[2] for x in group))
            if len(files)>=2:
                delay=float(np.median([x[0] for x in group])); ratio=float(np.median([x[1] for x in group]))
                clusters.append({"condition":cond,"median_delay_us":delay,"extra_path_m":delay/1e6*1500,
                                 "shots_observed":len(files),"n_shots":nshots,"observation_rate_pct":len(files)/nshots*100,
                                 "median_relative_amplitude":ratio,"files":";".join(files)})
    pd.DataFrame(clusters).to_csv(OUT/"multipath_recurring_clusters.csv",index=False,encoding="utf-8-sig")
    strength_chart(summary,OUT/"direct_signal_strength.png")
    multipath_chart(export,OUT/"multipath_candidates.png")
    representative_chart(records,OUT/"representative_waveforms.png")
    print(summary.to_string(index=False))


if __name__=="__main__": main()
