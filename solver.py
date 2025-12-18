from ortools.sat.python import cp_model
import math
import pandas as pd
from collections import defaultdict

def solve_timetable(data, mode):
    DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]

    # ---------- SLOT MAP ----------
    SLOT_MAP = {}
    t = 8.5
    idx = 0
    while t < 19:
        SLOT_MAP[idx] = {"val": t}
        idx += 1
        t += 0.5
    TOTAL_SLOTS = len(SLOT_MAP)

    # ---------- PREP ----------
    df_courses = pd.concat([data["ai_in"], data["cy_in"]]).fillna(0)
    room_list = data["room"].to_dict("records")
    room_list.append({"room": "Online", "capacity": 9999, "type": "virtual"})

    teacher_map = defaultdict(list)
    for _, r in data["teacher_courses"].iterrows():
        teacher_map[str(r["course_code"])].append(str(r["teacher_id"]))

    # ---------- TASKS ----------
    tasks = []
    for _, r in df_courses.iterrows():
        lec = int(math.ceil(r["lecture_hour"] * 2))
        lab = int(math.ceil(r["lab_hour"] * 2))

        if lec > 0:
            tasks.append({
                "uid": f"{r.course_code}_S{r.section}_Lec",
                "dur": lec,
                "std": r.enrollment_count,
                "teachers": teacher_map[r.course_code],
                "is_online": r.lec_online == 1,
                "type": "Lec",
                "optional": r.optional
            })

        if lab > 0:
            tasks.append({
                "uid": f"{r.course_code}_S{r.section}_Lab",
                "dur": lab,
                "std": r.enrollment_count,
                "teachers": teacher_map[r.course_code],
                "is_online": r.lab_online == 1,
                "type": "Lab",
                "optional": r.optional
            })

    # ---------- MODEL ----------
    model = cp_model.CpModel()
    schedule = {}
    is_scheduled = {}

    for t in tasks:
        uid = t["uid"]
        is_scheduled[uid] = model.NewBoolVar(uid)

        options = []
        for r in room_list:
            if t["is_online"] and r["room"] != "Online": continue
            if not t["is_online"] and r["room"] == "Online": continue
            if r["capacity"] < t["std"]: continue

            for d in range(5):
                for s in range(TOTAL_SLOTS - t["dur"]):
                    sv = SLOT_MAP[s]["val"]
                    ev = sv + t["dur"] * 0.5

                    if mode == 1 and (sv < 9 or ev > 16): continue

                    v = model.NewBoolVar(f"{uid}_{r['room']}_{d}_{s}")
                    schedule[(uid, r["room"], d, s)] = v
                    options.append(v)

        model.Add(sum(options) == is_scheduled[uid])

    # ---------- OBJECTIVE ----------
    SCORE_CORE = 1000
    SCORE_OPT = 100

    model.Maximize(
        sum(
            is_scheduled[t["uid"]] *
            (SCORE_CORE if t["optional"] == 0 else SCORE_OPT)
            for t in tasks
        )
    )

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 120
    status = solver.Solve(model)

    if status not in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        return None

    # ---------- RESULT ----------
    results = []
    for (uid, room, d, s), v in schedule.items():
        if solver.Value(v):
            dur = next(t["dur"] for t in tasks if t["uid"] == uid)
            results.append({
                "Course": uid,
                "Day": DAYS[d],
                "Start": SLOT_MAP[s]["val"],
                "End": SLOT_MAP[s + dur]["val"],
                "Room": room
            })

    return pd.DataFrame(results)
