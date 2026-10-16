#!/usr/bin/env python3
"""Interactive viewer/exporter for AquaBeacon mini-shot ADC CSV files.

The GUI shows every ADC sample in a selected Sxxx.CSV.  It also provides a
table with relative and derived UTC/JST timestamps and can export the selected
waveform as PNG or all sample rows as CSV.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import sys

import matplotlib
import numpy as np


JST = timezone(timedelta(hours=9))
FILE_RE = re.compile(r"S(\d+)\.CSV$", re.IGNORECASE)
DEFAULT_WATER_TEMPERATURE_C = 32.5
REFERENCE_SOUND_SPEED_MPS = 1500.0


@dataclass
class Shot:
    path: Path
    meta: dict[str, str]
    summary: dict[str, str]
    samples: list[dict[str, str]]

    @property
    def utc_base(self) -> datetime | None:
        value = self.meta.get("utc_iso", "")
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None


def natural_file_key(path: Path) -> tuple[int, str]:
    match = FILE_RE.fullmatch(path.name)
    return (int(match.group(1)) if match else 10**9, path.name.lower())


def read_shot(path: Path) -> Shot:
    meta: dict[str, str] = {}
    header: list[str] | None = None
    summary: dict[str, str] = {}
    samples: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\r\n")
            if line.startswith("# ") and "," in line:
                key, value = line[2:].split(",", 1)
                meta[key] = value
            elif line.startswith("record_type,"):
                header = next(csv.reader([line]))
            elif header and line:
                values = next(csv.reader([line]))
                if len(values) != len(header):
                    continue
                row = dict(zip(header, values))
                if row.get("record_type") == "SUMMARY":
                    summary = row
                elif row.get("record_type") == "ADC":
                    samples.append(row)
    return Shot(path=path, meta=meta, summary=summary, samples=samples)


def sample_arrays(shot: Shot) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    t_us = np.asarray([float(r["t_from_sync_us"]) for r in shot.samples], dtype=float)
    mv = np.asarray([float(r["env_adc_mv"]) for r in shot.samples], dtype=float)
    cmp_out = np.asarray([float(r.get("cmp_out") or 0) for r in shot.samples], dtype=float)
    return t_us, mv, cmp_out


def smooth(values: np.ndarray, width: int = 7) -> np.ndarray:
    if len(values) < width:
        return values.copy()
    return np.convolve(values, np.ones(width) / width, mode="same")


def corrected_sound_speed_mps(temperature_c: float) -> float:
    """Bilaniuk-Wong pure-water sound speed at atmospheric pressure (0-100 C)."""
    if not 0.0 <= temperature_c <= 100.0:
        raise ValueError("水温は0～100 ℃の範囲で入力してください。")
    t = temperature_c
    return (1402.38742 + 5.03821344*t - 5.80539349e-2*t**2
            + 3.32000870e-4*t**3 - 1.44537900e-6*t**4 + 2.99402365e-9*t**5)


def two_point_metrics(t1_us: float, t2_us: float, temperature_c: float) -> dict[str, float]:
    delta_us = t2_us - t1_us
    absolute_seconds = abs(delta_us) / 1_000_000.0
    corrected_speed = corrected_sound_speed_mps(temperature_c)
    return {
        "delta_us": delta_us,
        "absolute_delta_us": abs(delta_us),
        "reference_speed_mps": REFERENCE_SOUND_SPEED_MPS,
        "reference_distance_m": absolute_seconds * REFERENCE_SOUND_SPEED_MPS,
        "temperature_c": temperature_c,
        "corrected_speed_mps": corrected_speed,
        "corrected_distance_m": absolute_seconds * corrected_speed,
    }


def multipath_candidates(shot: Shot) -> list[tuple[float, float]]:
    """Return (absolute time from SYNC [us], smoothed voltage [mV])."""
    if not shot.samples:
        return []
    t_us, mv, _ = sample_arrays(shot)
    tof = float(shot.summary.get("tof_us") or 0)
    if tof <= 0:
        return []
    filtered = smooth(mv)
    baseline = mv[(t_us >= 250) & (t_us <= min(2200, max(800, tof - 1200)))]
    if len(baseline) == 0:
        return []
    median = float(np.median(baseline))
    sigma = max(1.0, 1.4826 * float(np.median(np.abs(baseline - median))))
    direct = filtered[(t_us >= tof - 350) & (t_us <= tof + 650)]
    if len(direct) == 0:
        return []
    excess = max(0.1, float(np.max(direct)) - median)
    threshold = median + max(6 * sigma, 0.05 * excess, 8)
    indices = np.flatnonzero((t_us >= tof + 1500) & (t_us <= 21900))
    local = [i for i in indices[1:-1] if filtered[i] >= filtered[i-1] and filtered[i] > filtered[i+1] and filtered[i] >= threshold]
    chosen: list[int] = []
    for index in sorted(local, key=lambda i: filtered[i], reverse=True):
        if all(abs(t_us[index] - t_us[other]) >= 500 for other in chosen):
            chosen.append(index)
    return sorted([(float(t_us[i]), float(filtered[i])) for i in chosen])


def draw_shot(axis, shot: Shot, show_points: bool = True, show_smooth: bool = True) -> None:
    axis.clear()
    if not shot.samples:
        axis.text(0.5, 0.5, "No ADC samples", ha="center", va="center", transform=axis.transAxes)
        return
    t_us, mv, _ = sample_arrays(shot)
    t_ms = t_us / 1000.0
    if show_points:
        axis.plot(t_ms, mv, color="#8ecbc2", linewidth=0.7, marker=".", markersize=2.2, label="ADC samples")
    if show_smooth:
        axis.plot(t_ms, smooth(mv), color="#087f73", linewidth=1.4, label="7-sample moving average")
    threshold = float(shot.meta.get("threshold_mv") or shot.summary.get("threshold_mv") or 0)
    if threshold > 0:
        axis.axhline(threshold, color="#e09f3e", linestyle="--", linewidth=1.2, label=f"Threshold ({threshold:.0f} mV)")
    tof = float(shot.summary.get("tof_us") or 0)
    if tof > 0:
        axis.axvline(tof / 1000, color="#e76f51", linewidth=1.5, label=f"Direct arrival ({tof/1000:.3f} ms)")
    candidates = multipath_candidates(shot)
    for number, (candidate_us, _) in enumerate(candidates):
        axis.axvline(candidate_us / 1000, color="#6d597a", linewidth=1.0, alpha=0.85,
                     label="Multipath candidate" if number == 0 else None)
    status = shot.summary.get("status", "")
    distance = shot.summary.get("range_m", "")
    axis.set_title(f"{shot.path.name}   status={status}   range={distance} m")
    axis.set_xlabel("Time after SYNC (ms)")
    axis.set_ylabel("ADC envelope voltage (mV)")
    axis.set_xlim(float(t_ms.min()), float(t_ms.max()))
    axis.set_ylim(bottom=0)
    axis.grid(True, alpha=0.25)
    axis.legend(loc="upper right", fontsize=8)


def timestamp_strings(shot: Shot, t_us: float) -> tuple[str, str]:
    if shot.utc_base is None:
        return "", ""
    stamp = shot.utc_base + timedelta(microseconds=t_us)
    return stamp.astimezone(timezone.utc).isoformat(timespec="microseconds"), stamp.astimezone(JST).isoformat(timespec="microseconds")


def export_samples(shot: Shot, destination: Path) -> None:
    fields = ["source_file", "sample_index", "t_from_sync_us", "t_from_sync_ms", "derived_utc", "derived_jst",
              "env_adc_raw", "env_adc_mv", "cmp_out", "threshold_mv"]
    with destination.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in shot.samples:
            t_us = float(row["t_from_sync_us"])
            utc_text, jst_text = timestamp_strings(shot, t_us)
            writer.writerow({
                "source_file": shot.path.name,
                "sample_index": row.get("index", ""),
                "t_from_sync_us": f"{t_us:.0f}",
                "t_from_sync_ms": f"{t_us/1000:.6f}",
                "derived_utc": utc_text,
                "derived_jst": jst_text,
                "env_adc_raw": row.get("env_adc_raw", ""),
                "env_adc_mv": row.get("env_adc_mv", ""),
                "cmp_out": row.get("cmp_out", ""),
                "threshold_mv": row.get("threshold_mv", shot.meta.get("threshold_mv", "")),
            })


def export_all_samples(paths: list[Path], destination: Path) -> tuple[int, int]:
    fields = ["source_file", "sample_index", "t_from_sync_us", "t_from_sync_ms", "derived_utc", "derived_jst",
              "env_adc_raw", "env_adc_mv", "cmp_out", "threshold_mv"]
    sample_count = 0
    with destination.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for source in paths:
            shot = read_shot(source)
            for row in shot.samples:
                t_us = float(row["t_from_sync_us"]); utc_text, jst_text = timestamp_strings(shot, t_us)
                writer.writerow({"source_file": source.name, "sample_index": row.get("index", ""),
                                 "t_from_sync_us": f"{t_us:.0f}", "t_from_sync_ms": f"{t_us/1000:.6f}",
                                 "derived_utc": utc_text, "derived_jst": jst_text,
                                 "env_adc_raw": row.get("env_adc_raw", ""), "env_adc_mv": row.get("env_adc_mv", ""),
                                 "cmp_out": row.get("cmp_out", ""),
                                 "threshold_mv": row.get("threshold_mv", shot.meta.get("threshold_mv", ""))})
                sample_count += 1
    return len(paths), sample_count


def export_two_point_measurement(
    shot: Shot,
    point1: tuple[int, float, float],
    point2: tuple[int, float, float],
    temperature_c: float,
    destination: Path,
) -> None:
    index1, t1_us, voltage1_mv = point1
    index2, t2_us, voltage2_mv = point2
    metrics = two_point_metrics(t1_us, t2_us, temperature_c)
    utc1, jst1 = timestamp_strings(shot, t1_us)
    utc2, jst2 = timestamp_strings(shot, t2_us)
    row = {
        "source_file": shot.path.name,
        "water_temperature_c": f"{temperature_c:.3f}",
        "point1_index": index1, "point1_t_from_sync_us": f"{t1_us:.0f}",
        "point1_adc_mv": f"{voltage1_mv:.3f}", "point1_utc": utc1, "point1_jst": jst1,
        "point2_index": index2, "point2_t_from_sync_us": f"{t2_us:.0f}",
        "point2_adc_mv": f"{voltage2_mv:.3f}", "point2_utc": utc2, "point2_jst": jst2,
        "delta_t_p2_minus_p1_us": f"{metrics['delta_us']:.0f}",
        "absolute_delta_t_us": f"{metrics['absolute_delta_us']:.0f}",
        "reference_sound_speed_mps": f"{metrics['reference_speed_mps']:.3f}",
        "reference_distance_difference_m": f"{metrics['reference_distance_m']:.6f}",
        "corrected_sound_speed_mps": f"{metrics['corrected_speed_mps']:.6f}",
        "corrected_distance_difference_m": f"{metrics['corrected_distance_m']:.6f}",
    }
    with destination.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader(); writer.writerow(row)


def export_png(shot: Shot, destination: Path) -> None:
    matplotlib.use("Agg", force=True)
    from matplotlib.figure import Figure
    figure = Figure(figsize=(13, 6.8), dpi=140, layout="constrained")
    axis = figure.add_subplot(111)
    draw_shot(axis, shot)
    figure.savefig(destination, dpi=180)


def load_condition_index(path: Path | None) -> dict[str, str]:
    if path is None or not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return {row["file"]: row.get("condition", "unclassified") for row in csv.DictReader(handle)}


class Viewer:
    def __init__(self, root, input_dir: Path, index_path: Path | None):
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
        matplotlib.use("TkAgg", force=True)
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
        from matplotlib.figure import Figure

        self.tk, self.ttk, self.filedialog, self.messagebox = tk, ttk, filedialog, messagebox
        self.root = root
        self.input_dir = input_dir
        self.index_path = index_path
        self.condition_index = load_condition_index(index_path)
        self.all_files: list[Path] = []
        self.visible_files: list[Path] = []
        self.current: Shot | None = None
        self.selected_points: list[tuple[int, float, float]] = []
        self.selection_artists: list[object] = []

        root.title("AquaBeacon ADC Waveform Viewer")
        root.geometry("1480x900")
        controls = ttk.Frame(root, padding=6)
        controls.pack(fill=tk.X)
        ttk.Button(controls, text="フォルダを開く", command=self.choose_dir).pack(side=tk.LEFT)
        self.dir_label = ttk.Label(controls, text=str(input_dir))
        self.dir_label.pack(side=tk.LEFT, padx=8)
        ttk.Label(controls, text="条件:").pack(side=tk.LEFT, padx=(15, 2))
        self.condition_var = tk.StringVar(value="すべて")
        self.condition_combo = ttk.Combobox(controls, textvariable=self.condition_var, width=18, state="readonly")
        self.condition_combo.pack(side=tk.LEFT)
        self.condition_combo.bind("<<ComboboxSelected>>", lambda _e: self.apply_filter())
        ttk.Button(controls, text="前へ", command=lambda: self.step(-1)).pack(side=tk.LEFT, padx=(12, 2))
        ttk.Button(controls, text="次へ", command=lambda: self.step(1)).pack(side=tk.LEFT, padx=2)
        ttk.Button(controls, text="PNG出力", command=self.save_png).pack(side=tk.RIGHT, padx=2)
        ttk.Button(controls, text="ADC CSV出力", command=self.save_csv).pack(side=tk.RIGHT, padx=2)
        ttk.Button(controls, text="全ADC結合CSV", command=self.save_all_csv).pack(side=tk.RIGHT, padx=2)

        measurement = ttk.Frame(root, padding=(6, 0, 6, 6))
        measurement.pack(fill=tk.X)
        ttk.Label(measurement, text="2点距離差:").pack(side=tk.LEFT)
        ttk.Label(measurement, text="水温").pack(side=tk.LEFT, padx=(10, 2))
        self.temperature_var = tk.StringVar(value=f"{DEFAULT_WATER_TEMPERATURE_C:.1f}")
        self.temperature_entry = ttk.Entry(measurement, textvariable=self.temperature_var, width=7)
        self.temperature_entry.pack(side=tk.LEFT)
        ttk.Label(measurement, text="℃").pack(side=tk.LEFT, padx=(2, 8))
        self.temperature_entry.bind("<Return>", lambda _e: self.update_measurement())
        self.temperature_entry.bind("<FocusOut>", lambda _e: self.update_measurement())
        ttk.Button(measurement, text="再計算", command=self.update_measurement).pack(side=tk.LEFT, padx=2)
        ttk.Button(measurement, text="選択解除", command=self.clear_selection).pack(side=tk.LEFT, padx=2)
        ttk.Button(measurement, text="2点結果CSV", command=self.save_two_point_csv).pack(side=tk.LEFT, padx=2)
        self.speed_label = ttk.Label(measurement, text="")
        self.speed_label.pack(side=tk.LEFT, padx=(12, 6))
        self.measurement_label = ttk.Label(measurement, text="波形上の任意の2点をクリックしてください。",
                                           foreground="#174f78", wraplength=780, justify=tk.LEFT)
        self.measurement_label.pack(side=tk.LEFT, padx=8, fill=tk.X, expand=True)

        main = ttk.Panedwindow(root, orient=tk.HORIZONTAL)
        main.pack(fill=tk.BOTH, expand=True)
        left = ttk.Frame(main, padding=5)
        right = ttk.Frame(main, padding=5)
        main.add(left, weight=1); main.add(right, weight=5)
        self.file_list = tk.Listbox(left, exportselection=False, width=22)
        self.file_list.pack(fill=tk.BOTH, expand=True)
        self.file_list.bind("<<ListboxSelect>>", self.on_file_selected)

        notebook = ttk.Notebook(right)
        notebook.pack(fill=tk.BOTH, expand=True)
        graph_tab = ttk.Frame(notebook); table_tab = ttk.Frame(notebook)
        notebook.add(graph_tab, text="波形"); notebook.add(table_tab, text="全ADCサンプル")
        self.figure = Figure(figsize=(10, 6), dpi=100, layout="constrained")
        self.axis = self.figure.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.figure, master=graph_tab)
        self.canvas.mpl_connect("button_press_event", self.on_plot_click)
        NavigationToolbar2Tk(self.canvas, graph_tab).update()
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.info = ttk.Label(graph_tab, text="", anchor="w")
        self.info.pack(fill=tk.X)

        columns = ("index", "us", "ms", "utc", "jst", "raw", "mv", "cmp", "threshold")
        self.table = ttk.Treeview(table_tab, columns=columns, show="headings")
        headings = {"index":"index", "us":"SYNC後 (us)", "ms":"SYNC後 (ms)", "utc":"UTC（導出）",
                    "jst":"JST（導出）", "raw":"ADC raw", "mv":"ADC (mV)", "cmp":"cmp", "threshold":"しきい値 (mV)"}
        widths = {"index":65, "us":95, "ms":95, "utc":210, "jst":210, "raw":75, "mv":85, "cmp":55, "threshold":100}
        for col in columns:
            self.table.heading(col, text=headings[col]); self.table.column(col, width=widths[col], anchor="center")
        yscroll = ttk.Scrollbar(table_tab, orient=tk.VERTICAL, command=self.table.yview)
        self.table.configure(yscrollcommand=yscroll.set)
        self.table.pack(side=tk.LEFT, fill=tk.BOTH, expand=True); yscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.refresh_files()
        self.update_measurement()

    def refresh_files(self):
        self.all_files = sorted(self.input_dir.glob("S*.CSV"), key=natural_file_key)
        conditions = sorted(set(self.condition_index.get(p.name, "未分類") for p in self.all_files))
        self.condition_combo["values"] = ["すべて", *conditions]
        self.dir_label.configure(text=str(self.input_dir))
        self.apply_filter()

    def apply_filter(self):
        selected = self.condition_var.get()
        self.visible_files = [p for p in self.all_files if selected == "すべて" or self.condition_index.get(p.name, "未分類") == selected]
        self.file_list.delete(0, self.tk.END)
        for path in self.visible_files:
            cond = self.condition_index.get(path.name, "未分類")
            self.file_list.insert(self.tk.END, f"{path.name}  [{cond}]")
        if self.visible_files:
            self.file_list.selection_set(0); self.load_index(0)

    def choose_dir(self):
        chosen = self.filedialog.askdirectory(initialdir=self.input_dir)
        if chosen:
            self.input_dir = Path(chosen); self.refresh_files()

    def on_file_selected(self, _event=None):
        selection = self.file_list.curselection()
        if selection: self.load_index(selection[0])

    def step(self, offset: int):
        selection = self.file_list.curselection()
        if not selection: return
        target = max(0, min(len(self.visible_files)-1, selection[0] + offset))
        self.file_list.selection_clear(0, self.tk.END); self.file_list.selection_set(target); self.file_list.see(target)
        self.load_index(target)

    def load_index(self, index: int):
        try:
            self.current = read_shot(self.visible_files[index])
        except Exception as exc:
            self.messagebox.showerror("読込エラー", str(exc)); return
        self.selected_points = []
        self.selection_artists = []
        draw_shot(self.axis, self.current); self.canvas.draw_idle()
        self.update_measurement()
        condition = self.condition_index.get(self.current.path.name, "未分類")
        self.info.configure(text=f"条件: {condition} / ADC samples: {len(self.current.samples)} / UTC基準: {self.current.meta.get('utc_iso','不明')}")
        self.table.delete(*self.table.get_children())
        for row in self.current.samples:
            t_us = float(row["t_from_sync_us"]); utc_text, jst_text = timestamp_strings(self.current, t_us)
            self.table.insert("", "end", values=(row.get("index",""), f"{t_us:.0f}", f"{t_us/1000:.6f}", utc_text,
                                                    jst_text, row.get("env_adc_raw",""), row.get("env_adc_mv",""),
                                                    row.get("cmp_out",""), row.get("threshold_mv", self.current.meta.get("threshold_mv",""))))

    def water_temperature(self) -> float:
        try:
            return float(self.temperature_var.get().strip())
        except ValueError as exc:
            raise ValueError("水温を数値で入力してください。") from exc

    def on_plot_click(self, event):
        if not self.current or not self.current.samples or event.inaxes is not self.axis or event.xdata is None:
            return
        toolbar = getattr(self.canvas, "toolbar", None)
        if toolbar is not None and getattr(toolbar, "mode", ""):
            return
        t_us, mv, _ = sample_arrays(self.current)
        index = int(np.argmin(np.abs(t_us / 1000.0 - event.xdata)))
        if len(self.selected_points) >= 2:
            self.clear_selection(redraw=False)
        self.selected_points.append((index, float(t_us[index]), float(mv[index])))
        self.draw_selection()
        self.update_measurement()

    def draw_selection(self):
        for artist in self.selection_artists:
            try: artist.remove()
            except ValueError: pass
        self.selection_artists = []
        colors = ("#d1495b", "#00798c")
        for number, (_index, t_us, mv) in enumerate(self.selected_points, start=1):
            color = colors[number-1]
            self.selection_artists.append(self.axis.axvline(t_us/1000.0, color=color, linewidth=1.8,
                                                            linestyle=":" if number == 1 else "-."))
            point, = self.axis.plot([t_us/1000.0], [mv], marker="o", markersize=8, color=color)
            self.selection_artists.append(point)
            note = self.axis.annotate(f"P{number}\n{t_us/1000:.3f} ms\n{mv:.0f} mV", (t_us/1000.0, mv),
                                      xytext=(8, 12), textcoords="offset points", color=color, fontsize=9,
                                      bbox={"boxstyle":"round,pad=0.2", "fc":"white", "ec":color, "alpha":0.9})
            self.selection_artists.append(note)
        self.canvas.draw_idle()

    def clear_selection(self, redraw: bool = True):
        self.selected_points = []
        for artist in self.selection_artists:
            try: artist.remove()
            except ValueError: pass
        self.selection_artists = []
        if redraw: self.canvas.draw_idle()
        self.update_measurement()

    def update_measurement(self):
        try:
            temperature = self.water_temperature()
            speed = corrected_sound_speed_mps(temperature)
        except ValueError as exc:
            self.speed_label.configure(text="補正音速: 入力エラー")
            self.measurement_label.configure(text=str(exc), foreground="#b42318")
            return
        self.speed_label.configure(text=f"補正音速: {speed:.2f} m/s")
        if len(self.selected_points) < 2:
            count = len(self.selected_points)
            text = "波形上の任意の2点をクリックしてください。" if count == 0 else "P1を選択しました。P2をクリックしてください。"
            self.measurement_label.configure(text=text, foreground="#174f78")
            return
        (_i1, t1_us, v1), (_i2, t2_us, v2) = self.selected_points
        metrics = two_point_metrics(t1_us, t2_us, temperature)
        self.measurement_label.configure(
            text=(f"P1={t1_us/1000:.3f} ms/{v1:.0f} mV, P2={t2_us/1000:.3f} ms/{v2:.0f} mV | "
                  f"Δt(P2-P1)={metrics['delta_us']:+.0f} µs, |Δt|={metrics['absolute_delta_us']:.0f} µs "
                  f"({metrics['absolute_delta_us']/1000:.3f} ms) | "
                  f"距離差: 1500 m/s → {metrics['reference_distance_m']:.3f} m, "
                  f"{metrics['corrected_speed_mps']:.2f} m/s → {metrics['corrected_distance_m']:.3f} m"),
            foreground="#174f78")

    def save_png(self):
        if not self.current: return
        path = self.filedialog.asksaveasfilename(defaultextension=".png", initialfile=f"{self.current.path.stem}_adc.png", filetypes=[("PNG", "*.png")])
        if path: self.figure.savefig(path, dpi=180)

    def save_csv(self):
        if not self.current: return
        path = self.filedialog.asksaveasfilename(defaultextension=".csv", initialfile=f"{self.current.path.stem}_all_adc.csv", filetypes=[("CSV", "*.csv")])
        if path: export_samples(self.current, Path(path))

    def save_two_point_csv(self):
        if not self.current or len(self.selected_points) != 2:
            self.messagebox.showwarning("2点未選択", "波形上でP1とP2を選択してください。")
            return
        try:
            temperature = self.water_temperature()
            corrected_sound_speed_mps(temperature)
        except ValueError as exc:
            self.messagebox.showerror("水温入力エラー", str(exc)); return
        path = self.filedialog.asksaveasfilename(defaultextension=".csv",
                                                 initialfile=f"{self.current.path.stem}_two_point_distance.csv",
                                                 filetypes=[("CSV", "*.csv")])
        if path:
            export_two_point_measurement(self.current, self.selected_points[0], self.selected_points[1],
                                         temperature, Path(path))

    def save_all_csv(self):
        path = self.filedialog.asksaveasfilename(defaultextension=".csv", initialfile="all_adc_samples.csv", filetypes=[("CSV", "*.csv")])
        if not path: return
        file_count, sample_count = export_all_samples(self.visible_files, Path(path))
        self.messagebox.showinfo("完了", f"{file_count}ファイル、{sample_count}サンプルを出力しました。")


def default_paths() -> tuple[Path, Path]:
    script_dir = Path(__file__).resolve().parent
    portable_index = script_dir / "measurement_index.csv"
    portable_input = script_dir.parent.parent
    if portable_index.exists() and any(portable_input.glob("S*.CSV")):
        return portable_input, portable_index
    root = script_dir.parents[0]
    return (root / "analysis_input" / "pool_20260810",
            root / "reports" / "20260810_153810_プール実験データ分析" / "measurement_index.csv")


def main() -> int:
    default_input, default_index = default_paths()
    parser = argparse.ArgumentParser(description="View/export every AquaBeacon ADC sample")
    parser.add_argument("--input", type=Path, default=default_input, help="Directory containing Sxxx.CSV files")
    parser.add_argument("--index", type=Path, default=default_index, help="measurement_index.csv used for condition labels")
    parser.add_argument("--export-preview", nargs=2, metavar=("SOURCE_CSV", "OUTPUT_PNG"), help="Headless single-waveform PNG export")
    parser.add_argument("--export-csv", nargs=2, metavar=("SOURCE_CSV", "OUTPUT_CSV"), help="Headless all-sample CSV export")
    parser.add_argument("--export-all-csv", metavar="OUTPUT_CSV", help="Export every ADC sample in --input to one CSV")
    args = parser.parse_args()
    if args.export_preview:
        export_png(read_shot(Path(args.export_preview[0])), Path(args.export_preview[1])); return 0
    if args.export_csv:
        export_samples(read_shot(Path(args.export_csv[0])), Path(args.export_csv[1])); return 0
    if args.export_all_csv:
        paths = sorted(args.input.glob("S*.CSV"), key=natural_file_key)
        files, samples = export_all_samples(paths, Path(args.export_all_csv))
        print(f"exported_files={files} exported_samples={samples}")
        return 0
    import tkinter as tk
    root = tk.Tk()
    Viewer(root, args.input, args.index if args.index.exists() else None)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
