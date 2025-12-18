from ortools.sat.python import cp_model
import pandas as pd
import math
import re
from collections import defaultdict

# ======================================================
# Helper functions
# ======================================================
def build_slot_map():
    SLOT_MAP = {}
    t = 8.5
    idx = 0
    while t < 19:
        SLOT_MAP[idx] = {
            "time": f"{int(t):02d}:{int((t % 1)*60):02d}",
            "val": t,
            "is_lunch": 12.5 <= t < 13.0
        }
        idx += 1
        t += 0.5
    return SLOT_MAP

def parse_unavailable_time(unavailable, days):
    result = {d: set() for d in range(len(days))}
    if not unavailable:
        return result

    items = unavailable if isinstance(unavailable, list) else [unavailable]
    for item in items:
        m = re.search(r"(\w{3})\s+(\d{1,2}[:.]\d{2})-(\d{1,2}[:.]\d{2})", str(item))
        if not m:
            continue
        day, s, e = m.groups()
        if day not in days:
            continue
        s_h, s_m = map(int, s.replace(".", ":").split(":"))
        e_h, e_m = map(int, e.replace(".", ":").split(":"))
        start = int(((s_h + s_m/60) - 8.5) * 2)
        end = int(((e_h + e_m/60) - 8.5) * 2)
        for i in range(start, end):
            result[days.index(day)].add(i)
    return result

# ======================================================
# MAIN SOLVER
# ======================================================
def solve_timetable(data, mode):
    DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    SLOT_MAP = build_slot_map()
    TOTAL_SLOTS = len(SLOT_MAP)

    # ---------- Load data ----------
    df_room = data["room"]
    df_teacher = data["teacher"]
    df_teacher_courses = data["teacher_courses"]
    df_courses = pd.concat([data["ai_in"], data["cy_in"]], ignore_index=True).fillna(0)

    room_list = df_room.to_dict("records")
    room_list.append({"room": "Online", "capacity": 9999, "type": "virtual"})

    # ---------- Teacher map ----------
    teacher_map = defaultdict(list)
    for _, r in df_teacher_courses.iterrows():
        teacher_map[str(r.course_code)].append(str(r.teacher_id))

    # ---------- Unavailable ----------
    teacher_unavail = {
        row.teacher_id: parse_unavailable_time(row.get("unavailable_times"), DAYS)
        for _, row in df_teacher.iterrows()
    }

    # ---------- Tasks ----------
    tasks = []
    for _, r in df_courses.iterrows():
        lec = int(math.ceil(r.lecture_hour * 2))
        lab = int(math.ceil(r.lab_hour * 2))

        if lec > 0:
            tasks.append({
                "uid": f"{r.course_code}_S{r.section}_Lec",
                "course": r.course_code,
                "sec": r.section,
                "dur": lec,
                "teachers": teacher_map.get(r.course_code, []),
                "std": r.enrollment_count,
                "type": "Lec",
                "online": r.lec_online == 1,
                "optional": r.optional
            })

        if lab > 0:
            tasks.append({
                "uid": f"{r.course_code}_S{r.section}_Lab",
                "course": r.course_code,
                "sec": r.section,
                "dur": lab,
                "teachers": teacher_map.get(r.course_code, []),
                "std": r.enrollment_count,
                "type": "Lab",
                "online": r.lab_online == 1,
                "optional": r.optional
            })

    # ---------- Model ----------
    model = cp_model.CpModel()
    schedule = {}
    is_scheduled = {}

    for t in tasks:
        uid = t["uid"]
        is_scheduled[uid] = model.NewBoolVar(uid)
        options = []

        for r in room_list:
            if t["online"] and r["room"] != "Online":
                continue
            if not t["online"] and r["room"] == "Online":
                continue
            if r["capacity"] < t["std"]:
                continue

            for d in range(len(DAYS)):
                for s in range(TOTAL_SLOTS - t["dur"]):
                    sv = SLOT_MAP[s]["val"]
                    ev = sv + t["dur"] * 0.5

                    if mode == 1 and (sv < 9 or ev > 16):
                        continue
                    if any(SLOT_MAP[s+i]["is_lunch"] for i in range(t["dur"])):
                        continue

                    conflict = False
                    for tea in t["teachers"]:
                        if s in teacher_unavail.get(tea, {}).get(d, set()):
                            conflict = True
                            break
                    if conflict:
                        continue

                    v = model.NewBoolVar(f"{uid}_{r['room']}_{d}_{s}")
                    schedule[(uid, r["room"], d, s)] = v
                    options.append(v)

        model.Add(sum(options) == is_scheduled[uid])

    # ---------- Objective ----------
    model.Maximize(sum(is_scheduled.values()))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 120
    status = solver.Solve(model)

    if status not in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        return pd.DataFrame()

    # ---------- Result ----------
    result = []
    for (uid, room, d, s), v in schedule.items():
        if solver.Value(v):
            dur = next(t["dur"] for t in tasks if t["uid"] == uid)
            result.append({
                "Course": uid,
                "Day": DAYS[d],
                "Start": SLOT_MAP[s]["time"],
                "End": SLOT_MAP[s+dur]["time"],
                "Room": room
            })

    return pd.DataFrame(result)
