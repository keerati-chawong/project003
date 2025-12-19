import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import math
import re
from collections import defaultdict

# --- ส่วน UI และ CSS เหมือนเดิม ---
st.set_page_config(page_title="Automatic Scheduler Pro", layout="wide")

# ==========================================
# 2. Helpers (แก้ไข parse_unavailable ให้แม่นยำขึ้น)
# ==========================================
def get_slot_map():
    slots = {}
    t, idx = 8.5, 0
    while t <= 19.0: # เปลี่ยนเป็น <= เพื่อให้มี slot 19:00 สำหรับเวลาเลิก
        h, m = int(t), int((t % 1) * 60)
        slots[idx] = {"time": f"{h:02d}:{m:02d}", "val": t, "is_lunch": 12.5 <= t < 13}
        t += 0.5; idx += 1
    return slots

def parse_unavailable_time(val, days, inv):
    res = {i: set() for i in range(len(days))}
    if pd.isna(val) or not val: return res
    items = val if isinstance(val, list) else [str(val)]
    for it in items:
        m = re.search(r"(\w{3})\s+(\d{1,2}[:.]\d{2})-(\d{1,2}[:.]\d{2})", str(it))
        if m:
            d, s, e = m.groups()
            # ทำความสะอาด format เวลาให้ตรงกับ inv (เช่น 8:30 -> 08:30)
            s_fmt = f"{int(s.split(':')[0] if ':' in s else s.split('.')[0]):02d}:{s.split(':')[1] if ':' in s else s.split('.')[1]}"
            e_fmt = f"{int(e.split(':')[0] if ':' in e else e.split('.')[0]):02d}:{e.split(':')[1] if ':' in e else e.split('.')[1]}"
            if d in days and s_fmt in inv and e_fmt in inv:
                for i in range(inv[s_fmt], inv[e_fmt]): res[days.index(d)].add(i)
    return res

# ==========================================
# 3. Solver (เพิ่มความปลอดภัยในการดึงข้อมูล)
# ==========================================
def calculate_schedule(files, mode, solver_time, penalty):
    DAYS = ["Mon","Tue","Wed","Thu","Fri"]
    SLOT_MAP = get_slot_map()
    SLOT_INV = {v["time"]:k for k,v in SLOT_MAP.items()}
    TOTAL = len(SLOT_MAP)

    try:
        # Load Data
        df_room = pd.read_csv(files["room"])
        room_list = df_room.to_dict("records")
        room_list.append({"room":"Online","capacity":9999,"type":"virtual"})

        df_tc = pd.read_csv(files["teacher_courses"])
        df_courses = pd.concat([pd.read_csv(files["ai_in"]), pd.read_csv(files["cy_in"])], ignore_index=True).fillna(0)
        df_t = pd.read_csv(files["all_teachers"])

        tmap = defaultdict(list)
        for _,r in df_tc.iterrows():
            tmap[str(r["course_code"]).strip()].append(str(r["teacher_id"]).strip())

        unavailable = {str(r["teacher_id"]).strip(): parse_unavailable_time(r.get("unavailable_times"), DAYS, SLOT_INV) for _,r in df_t.iterrows()}

        tasks = []
        for _,r in df_courses.iterrows():
            c, s = str(r["course_code"]).strip(), int(r["section"])
            tea = tmap.get(c, ["Unknown"])
            # Lec Split
            lec_slots = int(math.ceil(r["lecture_hour"]*2))
            p = 1
            while lec_slots > 0:
                dur = min(lec_slots, 6)
                tasks.append({"uid":f"{c}_S{s}_L{p}", "id":c, "sec":s, "type":"Lec", "dur":dur, "std":r["enrollment_count"], "tea":tea, "opt":r.get("optional",1), "online":r.get("lec_online")==1})
                lec_slots -= dur; p += 1
            # Lab
            lab_slots = int(math.ceil(r["lab_hour"]*2))
            if lab_slots > 0:
                tasks.append({"uid":f"{c}_S{s}_Lab", "id":c, "sec":s, "type":"Lab", "dur":lab_slots, "std":r["enrollment_count"], "tea":tea, "opt":r.get("optional",1), "online":r.get("lab_online")==1, "req_ai":r.get("require_lab_ai")==1})

        # --- OR-Tools Model ---
        model = cp_model.CpModel()
        vars, is_sched, task_var = {}, {}, {}
        room_use = defaultdict(lambda:defaultdict(lambda:defaultdict(list)))
        tea_use = defaultdict(lambda:defaultdict(lambda:defaultdict(list)))
        obj, pen = [], []

        for t in tasks:
            uid = t["uid"]
            is_sched[uid] = model.NewBoolVar(f"sc_{uid}")
            d_var = model.NewIntVar(0,4,f"d_{uid}")
            s_var = model.NewIntVar(0, TOTAL-2, f"s_{uid}") # -2 เพราะกันเลิกเกิน 19:00
            task_var[uid] = {"d": d_var, "s": s_var}

            cands = []
            for r in room_list:
                # กรองห้องแบบปลอดภัย
                if t["online"] and r["room"]!="Online": continue
                if not t["online"] and (r["room"]=="Online" or r["capacity"]<t["std"]): continue
                if t["type"]=="Lab" and "lab" not in str(r.get("type","")).lower(): continue
                if t.get("req_ai") and r["room"]!="lab_ai": continue

                for di in range(5):
                    for si in range(TOTAL - t["dur"]): # เช็คขอบเขตเวลา
                        sv = SLOT_MAP[si]["val"]
                        ev = sv + t["dur"]*0.5
                        if mode==1 and (sv<9 or ev>16): continue
                        if any(SLOT_MAP[si+i]["is_lunch"] for i in range(t["dur"])): continue
                        if any(tid in unavailable and si+i in unavailable[tid][di] for tid in t["tea"]): continue

                        v = model.NewBoolVar(f"{uid}_{r['room']}_{di}_{si}")
                        vars[(uid, r["room"], di, si)] = v
                        cands.append(v)
                        model.Add(d_var==di).OnlyEnforceIf(v)
                        model.Add(s_var==si).OnlyEnforceIf(v)

                        for i in range(t["dur"]):
                            room_use[r["room"]][di][si+i].append(v)
                            for tid in t["tea"]: tea_use[tid][di][si+i].append(v)
                        if mode==2 and (sv<9 or ev>16): pen.append(v*penalty)

            if cands: model.Add(sum(cands) == is_sched[uid])
            else: model.Add(is_sched[uid] == 0)
            obj.append(is_sched[uid] * (1000 if t["opt"]==0 else 100))

        for look in [room_use, tea_use]:
            for k in look:
                for d in look[k]:
                    for s in look[k][d]:
                        if len(look[k][d][s]) > 1: model.Add(sum(look[k][d][s]) <= 1)

        model.Maximize(sum(obj) - sum(pen))
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = solver_time
        status = solver.Solve(model)

        res_final = []
        if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            for t in tasks:
                uid = t["uid"]
                if solver.Value(is_sched[uid]):
                    di, si = solver.Value(task_var[uid]["d"]), solver.Value(task_var[uid]["s"])
                    room = next((r for (u,r,d2,s2),v in vars.items() if u==uid and d2==di and s2==si and solver.Value(v)), "-")
                    res_final.append({
                        "Day": DAYS[di], "Start": SLOT_MAP[si]["time"], "End": SLOT_MAP[si+t["dur"]]["time"],
                        "Room": room, "Course": t["id"], "Sec": t["sec"], "Type": t["type"],
                        "Teacher": ",".join(t["tea"]), "Note": "Online" if t["online"] else ""
                    })
            return pd.DataFrame(res_final), pd.DataFrame(), []
        return None, None, ["No solution found"]
    except Exception as e: return None, None, [str(e)]

# --- ส่วน UI สำหรับรัน (เพิ่มเข้าไปเพื่อให้รันได้จริง) ---
st.sidebar.header("Upload Files")
files = {k: st.sidebar.file_uploader(f"Upload {k}.csv", type="csv") for k in ["room", "teacher_courses", "ai_in", "cy_in", "all_teachers"]}
if st.sidebar.button("Run Scheduler"):
    if all(files.values()):
        df_res, df_uns, errs = calculate_schedule(files, 2, 120, 10)
        if df_res is not None:
            st.success("Success!")
            st.dataframe(df_res)
        else: st.error(errs)
    else: st.warning("Please upload all files")
