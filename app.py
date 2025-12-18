import streamlit as st
import pandas as pd
import numpy as np
import math
import re
from ortools.sat.python import cp_model
from collections import defaultdict

# ======================================================
# UI
# ======================================================
st.set_page_config(page_title="Timetable Scheduler", layout="wide")
st.title("📅 ระบบจัดตารางเรียนอัตโนมัติ")

st.markdown("ระบบนี้ใช้ Constraint Programming (OR-Tools) สำหรับจัดตารางเรียน")

# ======================================================
# Load CSV files (from GitHub repo)
# ======================================================
@st.cache_data
def load_data():
    return (
        pd.read_csv("room.csv"),
        pd.read_csv("all_teachers.csv"),
        pd.read_csv("teacher_courses.csv"),
        pd.read_csv("ai_in_courses.csv"),
        pd.read_csv("ai_out_courses.csv"),
        pd.read_csv("students.csv"),
        pd.read_csv("cy_in_courses.csv"),
        pd.read_csv("cy_out_courses.csv"),
    )

(
    df_room,
    all_teacher,
    df_teacher_courses,
    df_ai_in,
    df_ai_out,
    student,
    df_cy_in,
    df_cy_out,
) = load_data()

# ======================================================
# Scheduling Mode
# ======================================================
st.subheader("⚙️ โหมดการจัดตาราง")

SCHEDULE_MODE = st.radio(
    "เลือกโหมด",
    [1, 2],
    format_func=lambda x: "Compact (09:00–16:00)" if x == 1 else "Flexible (08:30–19:00)"
)

# ======================================================
# Time Slots
# ======================================================
SLOT_MAP = {}
t_start = 8.5
idx = 0
LUNCH_START, LUNCH_END = 12.5, 13.0

while t_start < 19.0:
    h = int(t_start)
    m = int((t_start - h) * 60)
    SLOT_MAP[idx] = {
        "time": f"{h:02d}:{m:02d}",
        "val": t_start,
        "is_lunch": LUNCH_START <= t_start < LUNCH_END,
    }
    idx += 1
    t_start += 0.5

TOTAL_SLOTS = len(SLOT_MAP)
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]
SLOT_TO_INDEX = {v["time"]: k for k, v in SLOT_MAP.items()}

# ======================================================
# Helper Functions
# ======================================================
def time_to_slot_index(time_str):
    m = re.search(r"(\d{1,2})[:.](\d{2})", str(time_str))
    if not m:
        return -1
    h, m = m.groups()
    return SLOT_TO_INDEX.get(f"{int(h):02d}:{int(m):02d}", -1)

def parse_unavailable_time(unavailable_input):
    slots = {d: set() for d in range(len(DAYS))}
    if not unavailable_input:
        return slots

    for item in ([unavailable_input] if isinstance(unavailable_input, str) else unavailable_input):
        m = re.search(r"(\w{3})\s+(\d{1,2}[:.]\d{2})-(\d{1,2}[:.]\d{2})", str(item))
        if not m:
            continue
        d, s, e = m.groups()
        if d not in DAYS:
            continue
        ds, de = time_to_slot_index(s), time_to_slot_index(e)
        for i in range(ds, de):
            slots[DAYS.index(d)].add(i)
    return slots

# ======================================================
# Prepare Data
# ======================================================
room_list = df_room.to_dict("records")
room_list.append({"room": "Online", "capacity": 9999, "type": "virtual"})

df_courses = pd.concat([df_ai_in, df_cy_in], ignore_index=True).fillna(0)
for col in ["lec_online", "lab_online", "optional"]:
    if col not in df_courses:
        df_courses[col] = 0 if col != "optional" else 1

teacher_map = defaultdict(list)
for _, r in df_teacher_courses.iterrows():
    teacher_map[str(r["course_code"]).strip()].append(str(r["teacher_id"]).strip())

TEACHER_UNAVAILABLE = {
    row["teacher_id"]: parse_unavailable_time(row.get("unavailable_times"))
    for _, row in all_teacher.iterrows()
}

# ======================================================
# Run Solver
# ======================================================
if st.button("▶️ สร้างตารางเรียน"):
    with st.spinner("กำลังคำนวณตารางเรียน..."):
        model = cp_model.CpModel()
        tasks, schedule, is_scheduled, task_vars = [], {}, {}, {}

        for _, row in df_courses.iterrows():
            dur = int(math.ceil(row["lecture_hour"] * 2))
            if dur == 0:
                continue
            uid = f"{row['course_code']}_S{row['section']}_Lec"
            tasks.append({
                "uid": uid,
                "course": row["course_code"],
                "sec": row["section"],
                "dur": dur,
                "teachers": teacher_map[row["course_code"]],
                "std": row["enrollment_count"],
                "is_online": row["lec_online"] == 1
            })

        for t in tasks:
            uid = t["uid"]
            is_scheduled[uid] = model.NewBoolVar(uid)
            d = model.NewIntVar(0, 4, f"d_{uid}")
            s = model.NewIntVar(0, TOTAL_SLOTS - 1, f"s_{uid}")
            task_vars[uid] = (d, s)

            options = []
            for r in room_list:
                if t["is_online"] and r["room"] != "Online":
                    continue
                if not t["is_online"] and r["room"] == "Online":
                    continue
                if r["capacity"] < t["std"]:
                    continue

                for day in range(5):
                    for slot in range(TOTAL_SLOTS - t["dur"]):
                        sv = SLOT_MAP[slot]["val"]
                        ev = sv + t["dur"] * 0.5
                        if SCHEDULE_MODE == 1 and (sv < 9 or ev > 16):
                            continue
                        if any(SLOT_MAP[slot + i]["is_lunch"] for i in range(t["dur"])):
                            continue

                        ok = True
                        for tea in t["teachers"]:
                            if slot in TEACHER_UNAVAILABLE.get(tea, {}).get(day, set()):
                                ok = False
                        if not ok:
                            continue

                        v = model.NewBoolVar(f"{uid}_{r['room']}_{day}_{slot}")
                        schedule[(uid, r["room"], day, slot)] = v
                        model.Add(d == day).OnlyEnforceIf(v)
                        model.Add(s == slot).OnlyEnforceIf(v)
                        options.append(v)

            model.Add(sum(options) == is_scheduled[uid])

        model.Maximize(sum(is_scheduled.values()))
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 60
        status = solver.Solve(model)

        if status not in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            st.error("ไม่สามารถจัดตารางได้")
        else:
            results = []
            for (uid, r, d, s), v in schedule.items():
                if solver.Value(v):
                    dur = next(t["dur"] for t in tasks if t["uid"] == uid)
                    results.append({
                        "Day": DAYS[d],
                        "Start": SLOT_MAP[s]["time"],
                        "End": SLOT_MAP[s + dur]["time"],
                        "Room": r,
                        "Course": uid,
                    })

            df_res = pd.DataFrame(results)
            st.success("จัดตารางสำเร็จ 🎉")
            st.dataframe(df_res)

            st.download_button(
                "📥 ดาวน์โหลดตารางเรียน (CSV)",
                df_res.to_csv(index=False),
                "final_schedule.csv",
                "text/csv"
            )
