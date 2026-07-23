"""단계 A — 2010 전국 교통량 상시조사 집계.

산출물 2개:
  1) 도로 등급별 기준 교통량 표 (문서 §5-3 N* 재료)
  2) 24시간 수요 프로파일 곡선 (문서 §14-A 시간대 프로파일)

주의: 각 지점은 '관측 1일(평일)' 24시간 스냅샷 → 진짜 AADT(연평균) 아님.
      절대 총량이 아니라 (a)시간대 비율 (b)등급별 상대 기준값으로만 사용.
      절대치 오차는 사용자 배율 n×이 흡수 (문서 §5-2).
"""
import glob
import os
import re
from collections import defaultdict

import openpyxl

RAW = "data/raw/traffic_survey"
OUT = "data/processed/traffic_survey"
os.makedirs(OUT, exist_ok=True)

# 차종 (car1..car10) — PDF 데이터 딕셔너리
VEH = ["일반승용차", "승합차", "택시", "중형버스", "대형버스",
       "이륜차", "소형화물차", "중형화물차", "대형화물차", "컨테이너트레일러"]


def classify_grade(jj_kind: str) -> str:
    """jj_kind(도로명/종류) → 표준 도로 등급 버킷."""
    s = str(jj_kind or "")
    if "고속" in s:
        return "고속국도"
    if "국가지원지방도" in s or "국지도" in s:
        return "국가지원지방도"
    if "일반국도" in s or re.search(r"국도\s*\d", s) or s.startswith("국도"):
        return "일반국도"
    if "지방도" in s:
        return "지방도"
    if "기타도로" in s or "지역도로" in s:
        return "기타도로"
    # 서울 등 도로명 표기(강변북로·통일로…) → 특별시도/간선으로 분류
    return "시도·간선"


def parse_hour(josa_time) -> int:
    """'08~09' → 8. 실패 시 -1."""
    m = re.match(r"\s*(\d{1,2})", str(josa_time or ""))
    return int(m.group(1)) if m else -1


# 지점별 시간별 대수 누적: point -> {hour -> [값들(여러 날 있으면)]},  point -> (grade, region)
pt_hour = defaultdict(lambda: defaultdict(list))
pt_hour_veh = defaultdict(lambda: defaultdict(lambda: [0.0] * 10))  # 차종별 (일 합계용)
pt_meta = {}

xlsxs = sorted(glob.glob(f"{RAW}/*/교통량*.xlsx"))
print(f"[집계] xlsx {len(xlsxs)}개 지역")

for path in xlsxs:
    region = os.path.basename(os.path.dirname(path))
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb.active
    n = 0
    for row in ws.iter_rows(min_row=2, values_only=True):
        code = row[0]
        if code is None:
            continue
        code = str(code).strip()
        kind = row[1]
        hour = parse_hour(row[4])
        if hour < 0 or hour > 23:
            continue
        cars = [float(v) if isinstance(v, (int, float)) else 0.0 for v in row[5:15]]
        total = sum(cars)
        pt = f"{region}|{code}"
        pt_hour[pt][hour].append(total)
        for i in range(10):
            pt_hour_veh[pt][hour][i] += cars[i]
        if pt not in pt_meta:
            pt_meta[pt] = (classify_grade(kind), region)
        n += 1
    wb.close()

print(f"[집계] 총 지점 수: {len(pt_meta)}")

# ── 지점별 일교통량(24h 합, 여러 날이면 시간대별 평균 후 합) ──────────────────
pt_daily = {}
pt_daily_veh = {}
pt_hour_frac = {}  # point -> [24] 시간대 비율
for pt, hours in pt_hour.items():
    hour_mean = {h: (sum(v) / len(v)) for h, v in hours.items()}
    daily = sum(hour_mean.values())
    pt_daily[pt] = daily
    # 차종별 일합 (관측일 하루 기준; 여러 날이면 그대로 합산되지만 대부분 1일)
    vsum = [0.0] * 10
    for h, arr in pt_hour_veh[pt].items():
        for i in range(10):
            vsum[i] += arr[i]
    pt_daily_veh[pt] = vsum
    if daily > 0:
        frac = [0.0] * 24
        for h in range(24):
            frac[h] = hour_mean.get(h, 0.0) / daily
        pt_hour_frac[pt] = frac

# ── ① 등급별 기준 교통량 표 ────────────────────────────────────────────────
GRADE_ORDER = ["고속국도", "일반국도", "국가지원지방도", "지방도", "시도·간선", "기타도로"]
grade_pts = defaultdict(list)
for pt, (grade, region) in pt_meta.items():
    grade_pts[grade].append(pt)

rows_grade = []
for grade in GRADE_ORDER:
    pts = grade_pts.get(grade, [])
    if not pts:
        continue
    dailies = [pt_daily[p] for p in pts if p in pt_daily]
    mean_daily = sum(dailies) / len(dailies)
    med_daily = sorted(dailies)[len(dailies) // 2]
    # 차종 구성(평균 일합 비율)
    vtot = [0.0] * 10
    for p in pts:
        for i in range(10):
            vtot[i] += pt_daily_veh[p][i]
    vall = sum(vtot) or 1.0
    passenger = (vtot[0] + vtot[2]) / vall          # 승용+택시
    van = vtot[1] / vall                            # 승합
    bus = (vtot[3] + vtot[4]) / vall                # 버스
    truck = (vtot[6] + vtot[7] + vtot[8] + vtot[9]) / vall  # 화물
    moto = vtot[5] / vall                           # 이륜
    rows_grade.append((grade, len(pts), round(mean_daily), round(med_daily),
                       round(passenger, 3), round(van, 3), round(bus, 3),
                       round(truck, 3), round(moto, 3)))

with open(f"{OUT}/등급별_기준교통량.csv", "w", encoding="utf-8-sig") as f:
    f.write("도로등급,지점수,평균일교통량_대일,중앙값일교통량_대일,"
            "승용택시비율,승합비율,버스비율,화물비율,이륜비율\n")
    for r in rows_grade:
        f.write(",".join(str(x) for x in r) + "\n")

print("\n=== ① 도로 등급별 기준 교통량 (관측일 기준, AADT 아님) ===")
print(f"{'등급':<14}{'지점수':>6}{'평균일교통량':>14}{'중앙값':>10}  차종(승용/버스/화물)")
for r in rows_grade:
    print(f"{r[0]:<14}{r[1]:>6}{r[2]:>14,}{r[3]:>10,}   {r[4]:.0%}/{r[6]:.0%}/{r[7]:.0%}")

# ── ② 24시간 수요 프로파일 (전체 + 등급별) ──────────────────────────────────
def profile_for(points):
    """볼륨 가중 평균 시간대 비율 [24]."""
    hour_sum = [0.0] * 24
    for p in points:
        hours = pt_hour[p]
        for h, vals in hours.items():
            hour_sum[h] += sum(vals) / len(vals)
    tot = sum(hour_sum) or 1.0
    return [x / tot for x in hour_sum]

prof_all = profile_for(list(pt_meta.keys()))
prof_by_grade = {g: profile_for(grade_pts[g]) for g in GRADE_ORDER if grade_pts.get(g)}

with open(f"{OUT}/시간대_프로파일.csv", "w", encoding="utf-8-sig") as f:
    grades = [g for g in GRADE_ORDER if g in prof_by_grade]
    f.write("시각,전체," + ",".join(grades) + "\n")
    for h in range(24):
        vals = [f"{prof_all[h]:.5f}"] + [f"{prof_by_grade[g][h]:.5f}" for g in grades]
        f.write(f"{h:02d}~{h+1:02d}," + ",".join(vals) + "\n")

# 첨두 정보
peak_h = max(range(24), key=lambda h: prof_all[h])
peak_frac = prof_all[peak_h]
top3 = sorted(range(24), key=lambda h: -prof_all[h])[:3]
print("\n=== ② 24시간 수요 프로파일 (전체, 볼륨가중) ===")
bar_max = max(prof_all)
for h in range(24):
    bar = "█" * round(prof_all[h] / bar_max * 40)
    mark = " ← 첨두" if h == peak_h else ""
    print(f"  {h:02d}~{h+1:02d}  {prof_all[h]*100:4.1f}%  {bar}{mark}")
print(f"\n첨두시: {peak_h:02d}~{peak_h+1:02d}시, 하루의 {peak_frac*100:.1f}% "
      f"(= 실측 K계수 {peak_frac:.3f})   상위3: {['%02d시'%h for h in top3]}")
print(f"\n[저장] {OUT}/등급별_기준교통량.csv, 시간대_프로파일.csv")
