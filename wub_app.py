import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import math
import re
import os

# ==========================================
# 1. Page Config
# ==========================================
st.set_page_config(page_title="Automatic Scheduler Pro", layout="wide")
st.title("🎓 Automatic Course Scheduler (Full Version)")

if 'has_run' not in st.session_state:
    st.session_state['has_run'] = False

# ==========================================
# 2. Sidebar: File Uploads
# ==========================================
st.sidebar.header("📂 1. Data Management")
def upload_section(label, default_file):
    uploaded = st.sidebar.file_uploader(f"Upload {label}", type="csv")
    if uploaded: return uploaded
    if os.path.exists(default_file): return default_file
    return None

up_room = upload_section("room.csv", "room.csv")
up_teacher_courses = upload_section("teacher_courses.csv", "teacher_courses.csv")
up_ai_in = upload_section("ai_in_courses.csv", "ai_in_courses.csv")
up_cy_in = upload_section("cy_in_courses.csv", "cy_in_courses.csv")
up_ai_out = upload_section("ai_out_courses.csv", "ai_out_courses.csv")
up_cy_out = upload_section("cy_out_courses.csv", "cy_out_courses.csv")

# ==========================================
# 3. Sidebar: Solver Config (User Adjustable)
# ==========================================
st.sidebar.divider()
st.sidebar.header("⚙️ 2. Solver Configuration")
SOLVER_TIME_LIMIT = st.sidebar.slider("Solver Time Limit (Seconds):", 10, 300, 120)
PENALTY_EXT_TIME = st.sidebar.slider("Penalty Score (Avoid Ext. Hours):", 0, 100, 50, 
                                    help="ยิ่งสูง Solver จะยิ่งพยายามไม่ลงวิชาในช่วง 08:30 หรือหลัง 16:00")

# ==========================================
# 4. Main Configuration
# ==========================================
st.subheader("🛠️ Scheduling Mode")
SCHEDULE_MODE = st.radio("Select Mode:", [1, 2], index=1, horizontal=True,
                         format_func=lambda x: "Compact (09:00-16:00)" if x==1 else "Flexible (08:30-19:00)")
run_button = st.button("🚀 Run Automatic Scheduler", use_container_width=True)

# ==========================================
# 5. Core Logic
# ==========================================
def calculate_schedule():
    logs = []
    # --- Time & Slot Setup ---
    SLOT_MAP = {}
    t_curr = 8.5
    idx = 0
    while t_curr < 19.0:
        h, m = int(t_curr), int((t_curr % 1) * 60)
        SLOT_MAP[idx] = {'time': f"{h:02d}:{m:02d}", 'val': t_curr, 'is_lunch': (12.5 <= t_curr < 13.0)}
        idx += 1
        t_curr += 0.5
    
    TOTAL_SLOTS = len(SLOT_MAP)
    DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
    SLOT_TO_INDEX = {v['time']: k for k, v in SLOT_MAP.items()}

    def time_to_idx(t_str):
        t_str = str(t_str).replace('.', ':').strip()
        match = re.search(r"(\d{1,2}):(\d{2})", t_str)
        if match:
            h, m = match.groups()
            return SLOT_TO_INDEX.get(f"{int(h):02d}:{int(m):02d}", -1)
        return -1

    # --- Loading Data ---
    try:
        df_courses = pd.concat([pd.read_csv(up_ai_in), pd.read_csv(up_cy_in)], ignore_index=True).fillna(0)
        room_list = pd.read_csv(up_room).to_dict('records')
        room_list.append({'room': 'Online', 'capacity': 9999})
        teacher_map = pd.read_csv(up_teacher_courses).groupby('course_code')['teacher_id'].apply(lambda x: ",".join(x.astype(str))).to_dict()
        
        occupied_slots = []
        for f in [up_ai_out, up_cy_out]:
            if f:
                df_f = pd.read_csv(f)
                for _, row in df_f.iterrows():
                    d_idx = DAYS.index(row['day'][:3]) if str(row['day'])[:3] in DAYS else -1
                    s_idx = time_to_idx(row['start'])
                    dur = int(math.ceil((row['lecture_hour'] + row['lab_hour']) * 2))
                    if d_idx != -1 and s_idx != -1:
                        for i in range(dur): occupied_slots.append((str(row['room']), d_idx, s_idx + i))
        logs.append(f"✅ Loaded {len(df_courses)} courses and fixed existing schedules.")
    except Exception as e:
        return None, None, [f"❌ Error loading files: {e}"]

    # --- Solver & Variables ---
    model = cp_model.CpModel()
    vars = {}
    is_scheduled = {}
    penalty_terms = []

    tasks = []
    for _, row in df_courses.iterrows():
        c_code, sec = str(row['course_code']), int(row['section'])
        for t_type in ['Lec', 'Lab']:
            hours = row.get(f'{t_type.lower()}_hour', 0)
            if hours > 0:
                tasks.append({
                    'uid': f"{c_code}_S{sec}_{t_type}", 'id': c_code, 'sec': sec, 'type': t_type,
                    'dur': int(math.ceil(hours * 2)), 'std': row['enrollment_count'], 
                    'teachers': teacher_map.get(c_code, 'Unknown'), 
                    'is_online': row.get(f'{t_type.lower()}_online', 0) == 1
                })

    for t in tasks:
        uid = t['uid']
        is_scheduled[uid] = model.NewBoolVar(f"s_{uid}")
        candidates = []
        for r in room_list:
            if (t['is_online'] and r['room'] != 'Online') or (not t['is_online'] and r['room'] == 'Online'): continue
            if r['capacity'] < t['std']: continue
            for d_idx in range(len(DAYS)):
                for s_idx in range(TOTAL_SLOTS - t['dur'] + 1):
                    # Basic Constraints
                    s_val, e_val = SLOT_MAP[s_idx]['val'], SLOT_MAP[s_idx]['val'] + (t['dur']*0.5)
                    if any(SLOT_MAP[s_idx+i]['is_lunch'] for i in range(t['dur'])): continue
                    if SCHEDULE_MODE == 1 and (s_val < 9.0 or e_val > 16.0): continue
                    if any((r['room'], d_idx, s_idx + i) in occupied_slots for i in range(t['dur'])): continue

                    v = model.NewBoolVar(f"{uid}_{r['room']}_{d_idx}_{s_idx}")
                    vars[(uid, r['room'], d_idx, s_idx)] = v
                    candidates.append(v)

                    # Penalty Score Logic: Avoid 08:30 or 16:00+
                    if s_val < 9.0 or e_val > 16.0:
                        penalty_terms.append(v * PENALTY_EXT_TIME)
        
        if candidates: model.Add(sum(candidates) == 1).OnlyEnforceIf(is_scheduled[uid])
        else: model.Add(is_scheduled[uid] == 0)

    # Room & Teacher Constraints
    for d in range(len(DAYS)):
        for s in range(TOTAL_SLOTS):
            for r in [rm['room'] for rm in room_list if rm['room'] != 'Online']:
                room_usage = [vars[k] for k in vars if k[1] == r and k[2] == d and k[3] <= s < k[3] + next(x['dur'] for x in tasks if x['uid'] == k[0])]
                if room_usage: model.Add(sum(room_usage) <= 1)

    # Objective: Maximize scheduled tasks - Minimize penalty
    model.Maximize(sum(is_scheduled.values()) * 1000 - sum(penalty_terms))
    
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = SOLVER_TIME_LIMIT
    status = solver.Solve(model)

    # --- Processing Result ---
    sched, unsched = [], []
    for t in tasks:
        uid = t['uid']
        found = False
        if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            for k, v in vars.items():
                if k[0] == uid and solver.Value(v):
                    found = True
                    notes = []
                    if t['is_online']: notes.append("Online")
                    if SLOT_MAP[k[3]]['val'] < 9.0 or (SLOT_MAP[k[3]]['val'] + t['dur']*0.5) > 16.0: notes.append("Ext.Time")
                    
                    sched.append({
                        'Day': DAYS[k[2]], 'Start': SLOT_MAP[k[3]]['time'], 
                        'End': SLOT_MAP.get(k[3] + t['dur'], {'time': '19:00'})['time'],
                        'Room': k[1], 'Course': t['id'], 'Sec': t['sec'], 
                        'Type': t['type'], 'Teacher': t['teachers'], 'Note': ", ".join(notes)
                    })
                    break
        if not found:
            unsched.append({'Course': t['id'], 'Sec': t['sec'], 'Type': t['type'], 'Teachers': t['teachers'], 'Status': 'Unscheduled'})

    return pd.DataFrame(sched), pd.DataFrame(unsched), logs

# ==========================================
# 6. UI Display
# ==========================================
if run_button:
    with st.status("🔍 Processing Schedule...", expanded=True) as status:
        df_res, df_un, logs = calculate_schedule()
        for log in logs: st.write(log)
        if df_res is not None:
            st.session_state['df_res'], st.session_state['df_un'] = df_res, df_un
            st.session_state['has_run'] = True
            status.update(label="✅ Scheduling Completed!", state="complete")

if st.session_state['has_run']:
    df_res, df_un = st.session_state['df_res'], st.session_state['df_un']

    tab_room, tab_teacher, tab_unsched = st.tabs(["🏠 View by Room", "👨‍🏫 View by Teacher", "⚠️ Unscheduled Tasks"])

    with tab_room:
        room = st.selectbox("Select Room:", sorted(df_res['Room'].unique()))
        st.dataframe(df_res[df_res['Room'] == room], use_container_width=True)

    with tab_teacher:
        all_t = sorted(list(set([i.strip() for s in df_res['Teacher'] for i in str(s).split(',')])))
        teacher = st.selectbox("Select Teacher:", all_t)
        st.dataframe(df_res[df_res['Teacher'].str.contains(teacher)], use_container_width=True)

    with tab_unsched:
        if not df_un.empty:
            st.error(f"Found {len(df_un)} tasks that could not be scheduled.")
            st.table(df_un)
        else:
            st.success("All tasks scheduled successfully!")

    st.download_button("📥 Download CSV", df_res.to_csv(index=False), "schedule.csv")
