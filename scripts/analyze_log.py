#!/usr/bin/env python3
"""
AquaBeacon RX ログ解析・合否判定スクリプト
仕様書 Section 19 (合格基準) に基づく

使い方:
  python scripts/analyze_log.py logs/rx_xxxxxxxx.csv
"""

import sys
import csv
import argparse
import statistics
from pathlib import Path

# ==============================
# 合格基準 (仕様書19章)
# ==============================
VALID_RATE_THRESHOLD = 0.90   # VALID率 >= 90%
SCORE_MIN = 7                  # score >= 7
TOF_MIN_US = 200               # 0.3m相当
TOF_MAX_US = 20000             # 30m相当（25m実験 + 5m余裕）
STD_TARGET_M = 0.10            # 距離再現性 標準偏差10cm以下
MIN_SAMPLES = 10              # 合否判定に必要な最小測定回数 (仕様: 10回中9回以上)

def _read_data_rows(path: Path):
    """コメント(#)・空行を除いたデータ行を返す"""
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for line in f:
            line = line.strip().lstrip("\ufeff")
            if line.startswith("#") or not line:
                continue
            if line.startswith("seq,t_sync_us,t_detect_us,tof_us,"):
                continue
            rows.append(line)
    return rows

def _parse_records(rows):
    reader = csv.DictReader(
        rows,
        fieldnames=["seq","t_sync_us","t_detect_us","tof_us",
                    "range_m","score","rx_peak","noise","status"]
    )
    return list(reader)

def analyze_no_tx(filepath: str):
    """無送信(誤検出)テスト: 無送信時にVALIDが1件も出ないこと (仕様19.1)"""
    path = Path(filepath)
    if not path.exists():
        print(f"[ERROR] ファイルが見つかりません: {filepath}")
        sys.exit(2)

    records = _parse_records(_read_data_rows(path))
    total = len(records)
    valid_rows = [r for r in records if r.get("status") == "VALID"]
    valid_count = len(valid_rows)

    print("=" * 50)
    print(f"  無送信(誤検出)テスト: {path.name}")
    print("=" * 50)
    print(f"  総測定回数 : {total}  (0でも可: 無送信ならログが出ないのが正常)")
    print(f"  VALID件数  : {valid_count}")

    print("\n" + "-" * 50)
    print("  合否判定 (無送信時にVALIDが出ないこと)")
    print("-" * 50)
    if valid_count == 0:
        print("  [PASS]  無送信時 VALID=0")
        print("-" * 50)
        print("  >> 総合判定: PASS")
        sys.exit(0)
    else:
        print(f"  [FAIL]  無送信なのに VALID={valid_count} 件 (誤検出)")
        for r in valid_rows[:5]:
            print(f"      seq={r.get('seq')} score={r.get('score')} range={r.get('range_m')}")
        print("-" * 50)
        print("  >> 総合判定: FAIL  → 誤検出。閾値/配線/フローティング入力を確認")
        sys.exit(1)

def analyze(filepath: str):
    path = Path(filepath)
    if not path.exists():
        print(f"[ERROR] ファイルが見つかりません: {filepath}")
        sys.exit(2)

    rows = _read_data_rows(path)

    if not rows:
        print("[ERROR] データ行がありません")
        sys.exit(2)

    records = _parse_records(rows)
    total = len(records)

    if total == 0:
        print("[ERROR] パース可能な行がありません")
        sys.exit(2)

    # ---- 集計 ----
    valid_rows   = [r for r in records if r["status"] == "VALID"]
    noise_rows   = [r for r in records if r["status"] == "NOISE_HIGH"]
    invalid_rows = [r for r in records if r["status"] == "INVALID"]
    tentative_rows = [r for r in records if r["status"] == "TENTATIVE"]
    oor_rows     = [r for r in records if r["status"] == "OUT_OF_RANGE"]

    valid_count  = len(valid_rows)
    valid_rate   = valid_count / total

    scores = []
    for r in records:
        try:
            scores.append(int(r["score"]))
        except (ValueError, KeyError):
            pass

    ranges = []
    for r in valid_rows:
        try:
            ranges.append(float(r["range_m"]))
        except (ValueError, KeyError):
            pass

    tofs = []
    for r in valid_rows:
        try:
            tofs.append(int(r["tof_us"]))
        except (ValueError, KeyError):
            pass

    # ---- 表示 ----
    print("=" * 50)
    print(f"  AquaBeacon RX ログ解析: {path.name}")
    print("=" * 50)
    print(f"  総測定回数   : {total}")
    print(f"  VALID        : {valid_count} ({valid_rate*100:.1f}%)")
    print(f"  TENTATIVE    : {len(tentative_rows)}")
    print(f"  INVALID      : {len(invalid_rows)}")
    print(f"  OUT_OF_RANGE : {len(oor_rows)}")
    print(f"  NOISE_HIGH   : {len(noise_rows)}")

    if scores:
        print(f"\n  平均スコア   : {statistics.mean(scores):.2f}")
        print(f"  最低スコア   : {min(scores)}")

    if ranges:
        mean_r = statistics.mean(ranges)
        std_r  = statistics.stdev(ranges) if len(ranges) > 1 else 0.0
        print(f"\n  平均距離     : {mean_r:.3f} m")
        print(f"  距離標準偏差 : {std_r*100:.1f} cm")
        print(f"  距離レンジ   : {min(ranges):.3f} 〜 {max(ranges):.3f} m")

    if tofs:
        print(f"\n  平均TOF      : {statistics.mean(tofs):.0f} us")

    # ---- 合否判定 ----
    print("\n" + "-" * 50)
    print("  合否判定")
    print("-" * 50)

    passed = True
    checks = []

    # 0. 最小測定回数 (仕様: 10回中9回以上 → 最低10回必要)
    ok = total >= MIN_SAMPLES
    checks.append((f"測定回数 >= {MIN_SAMPLES}",
                   f"{total}回", ok))
    if not ok: passed = False

    # 1. VALID率
    ok = valid_rate >= VALID_RATE_THRESHOLD
    checks.append(("VALID率 >= 90%",
                   f"{valid_rate*100:.1f}%", ok))
    if not ok: passed = False

    # 2. スコア (VALID判定された行のスコアが全て >= 7 であること)
    valid_scores = []
    for r in valid_rows:
        try:
            valid_scores.append(int(r["score"]))
        except (ValueError, KeyError):
            pass
    if valid_scores:
        ok = min(valid_scores) >= SCORE_MIN
        checks.append(("VALIDスコア >= 7",
                       f"min={min(valid_scores)}", ok))
        if not ok: passed = False

    # 3. TOF範囲
    if tofs:
        ok = all(TOF_MIN_US <= t <= TOF_MAX_US for t in tofs)
        checks.append(("TOF物理範囲内",
                       f"{min(tofs)}〜{max(tofs)} us", ok))
        if not ok: passed = False

    # 4. 距離再現性
    if len(ranges) > 1:
        ok = std_r <= STD_TARGET_M
        checks.append(("距離再現性 std <= 10cm",
                       f"{std_r*100:.1f} cm", ok))
        if not ok: passed = False

    for name, val, ok in checks:
        mark = "[PASS]" if ok else "[FAIL]"
        print(f"  {mark}  {name}: {val}")

    print("-" * 50)
    if passed:
        print("  >> 総合判定: PASS")
        sys.exit(0)
    else:
        print("  >> 総合判定: FAIL  → コードを修正してください")
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="AquaBeacon RX ログ解析・合否判定 (仕様書19章)")
    parser.add_argument("logfile", help="解析するRXログCSV")
    parser.add_argument("--no-tx", action="store_true", dest="no_tx",
                        help="無送信(誤検出)テスト: VALIDが0件ならPASS")
    args = parser.parse_args()

    if args.no_tx:
        analyze_no_tx(args.logfile)
    else:
        analyze(args.logfile)
