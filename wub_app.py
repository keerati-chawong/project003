import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import math
import re
import io
import os

# ==========================================
# 1. Page Config & Initialization
# ==========================================
st.set_page_config(page_title="Automatic Scheduler Pro", layout="wide")
st.title("🎓 Automatic Course Scheduler")

# ตรวจสอบ Session State สำหรับเก็บผลลัพธ์
if 'has_run' not in st.session_state:
    st.session_state['has_run'] = False
if 'schedule_results' not in st.session_state:
    st.session_state['schedule_results'] = pd.DataFrame()

# ==========================================
# 2. Sidebar & File Uploads
# ==========================================
st.sidebar.header("📂 Data Management")

def upload_section(label, default_file):
    uploaded = st.sidebar.file_uploader(f"Upload {label}", type="csv")
    if uploaded:
        return uploaded
    elif os.path.exists(default_file):
        return default_file
    return None

# สร้างปุ่มอัปโหลดใน Sidebar
up_room = upload_section("room.csv", "room.csv")
up_teacher_courses = upload_section("teacher_courses.csv", "teacher_courses.csv")
up_ai_in = upload_section("ai_in_courses.csv", "ai_in_courses.csv")
up_cy_in = upload_section("cy_in_courses.csv", "cy_in_courses.csv")
up_teachers = upload_section("all_teachers.csv", "all_teachers.csv")
up_ai_out = upload_section("ai_out_courses.csv", "ai_out_courses.csv")
up_cy_out = upload_section("cy_out_courses.csv", "cy_out_courses.csv")

# แสดงสถานะข้อมูลในหน้าหลัก
with st.expander("📄 Active Data Sources Status"):
    cols = st.columns(3)
    cols[0].write(f"**Room:** {'✅ Ready' if up_room else '❌ Missing'}")
    cols[1].write(f"**Teacher Courses:** {'✅ Ready' if up_teacher_courses else '❌ Missing'}")
    cols[2].write(f"**AI In-Courses:** {'✅ Ready' if up_ai_in else '❌ Missing'}")

# ==========================================
# 3. User Configuration
# ==========================================
st.subheader("⚙️ Scheduler Configuration")
col_cfg1, col_cfg2 = st.columns(2)

with col_cfg1:
    schedule_mode_desc = {
        1: "Compact Mode (09:00 - 16:00)",
        2: "Flexible Mode (08:30 - 19:00)"
    }
    SCHEDULE_MODE = st.radio(
        "Select Scheduling Mode:",
        options=[1, 2],
        format_func=lambda x: schedule_mode_desc[x]
    )

with col_cfg2:
    st.info(f"**Target:** {schedule_mode_desc[SCHEDULE_MODE]}")
    run_button = st.button("🚀 Run Automatic Scheduler", use_container_width=True)

# ==========================================
# 4. Core Logic Function
# ==========================================
def calculate_schedule():
    # --- Time Slot Setup ---
    SLOT_MAP = {}
    t_start = 8.5
    idx = 0
    LUNCH_START = 12.5
    LUNCH_END = 13.0

    while t_start < 19.0:
        hour = int(t_start)
        minute = int((t_start - hour) * 60)
        time_str = f"{hour:02d}:{minute:02d}"
        SLOT_MAP[idx] = {
            'time': time_str, 'val': t_start,
            'is_lunch': (t_start >= LUNCH_START and t_start < LUNCH_END)
        }
        idx += 1
        t_start += 0.5
    
    TOTAL_SLOTS = len(SLOT_MAP)
    DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
    SLOT_TO_INDEX = {v['time']: k for k, v in SLOT_MAP.items()}

    def time_to_slot_index(time_str):
        time_str = str(time_str).strip()
        match = re.search(r"(\d{1,2})[:.](\d{2})", time_str)
        if match:
            h, m = match.groups()
            time_str = f"{int(h):02d}:{int(m):02d}"
            return SLOT_TO_INDEX.get(time_str, -1)
        return -1

    def parse_unavailable_time(unavailable_input):
        unavailable_slots_by_day = {d_idx: set() for d_idx in range(len(DAYS))}
        if pd.isna(unavailable_input) or unavailable_input == 0: return unavailable_slots_by_day
        # Simplification for this demo: assumes string format "Mon 09:00-11:00"
        return unavailable_slots_by_day

    # --- Data Loading ---
    try:
        df_room = pd.read_csv(up_room)
        df_teacher_courses = pd.read_csv(up_teacher_courses)
        df_ai_in = pd.read_csv(up_ai_in)
        df_cy_in = pd.read_csv(up_cy_in)
        all_teacher = pd.read_csv(up_teachers)
        
        # Optional files
        df_ai_out = pd.read_csv(up_ai_out) if up_ai_out else pd.DataFrame()
        df_cy_out = pd.read_csv(up_cy_out) if up_cy_out else pd.DataFrame()

        room_list = df_room.to_dict('records')
        room_list.append({'room': 'Online', 'capacity': 9999, 'type': 'virtual'})
    except Exception as e:
        st.error(f"❌ Error loading CSV files: {e}")
        return None, None

    # --- Data Cleaning ---
    progress_bar = st.progress(10, text="Cleaning data...")
    df_courses = pd.concat([df_ai_in, df_cy_in], ignore_index=True).fillna(0)
    
    # Map Teachers
    teacher_map = df_teacher_courses.groupby('course_code')['teacher_id'].apply(lambda x: [str(i) for i in x]).to_dict()
    
    # Teacher Unavailability
    TEACHER_UNAVAILABLE_SLOTS = {str(row['teacher_id']): parse_unavailable_time(row.get('unavailable_times')) 
                                 for _, row in all_teacher.iterrows()}

    # Task Preparation
    tasks = []
    MAX_LEC_SESSION_SLOTS = 6

    for _, row in df_courses.iterrows():
        c_code = str(row['course_code'])
        sec = int(row['section'])
        teachers = teacher_map.get(c_code, ['Staff'])
        
        # Lecture
        lec_slots = int(math.ceil(row['lecture_hour'] * 2))
        if lec_slots > 0:
            tasks.append({
                'uid': f"{c_code}_S{sec}_Lec", 'id': c_code, 'sec': sec, 'type': 'Lec',
                'dur': lec_slots, 'std': row['enrollment_count'], 'teachers': teachers,
                'is_online': (row.get('lec_online', 0) == 1), 'is_optional': row.get('optional', 1)
            })
        
        # Lab
        lab_slots = int(math.ceil(row['lab_hour'] * 2))
        if lab_slots > 0:
            tasks.append({
                'uid': f"{c_code}_S{sec}_Lab", 'id': c_code, 'sec': sec, 'type': 'Lab',
                'dur': lab_slots, 'std': row['enrollment_count'], 'teachers': teachers,
                'is_online': (row.get('lab_online', 0) == 1), 'is_optional': row.get('optional', 1)
            })

    # --- Solver ---
    progress_bar.progress(30, text="Initializing Solver (CP-SAT)...")
    model = cp_model.CpModel()
    schedule_vars = {}
    is_scheduled = {}
    task_data = {}

    for t in tasks:
        uid = t['uid']
        is_scheduled[uid] = model.NewBoolVar(f"sched_{uid}")
        t_day = model.NewIntVar(0, len(DAYS)-1, f"d_{uid}")
        t_start = model.NewIntVar(0, TOTAL_SLOTS-1, f"s_{uid}")
        task_data[uid] = {'day': t_day, 'start': t_start}

        candidates = []
        for r_idx, r in enumerate(room_list):
            # Basic Constraint Check
            if t['is_online'] and r['room'] != 'Online': continue
            if not t['is_online'] and r['room'] == 'Online': continue
            if r['capacity'] < t['std']: continue

            for d_idx in range(len(DAYS)):
                for s_idx in range(TOTAL_SLOTS - t['dur'] + 1):
                    # Lunch check
                    if any(SLOT_MAP[s_idx + i]['is_lunch'] for i in range(t['dur'])): continue
                    
                    # Mode check
                    s_val = SLOT_MAP[s_idx]['val']
                    e_val = s_val + (t['dur'] * 0.5)
                    if SCHEDULE_MODE == 1 and (s_val < 9.0 or e_val > 16.0): continue

                    var = model.NewBoolVar(f"{uid}_r{r_idx}_d{d_idx}_s{s_idx}")
                    candidates.append(var)
                    schedule_vars[(uid, r['room'], d_idx, s_idx)] = var
                    
                    model.Add(t_day == d_idx).OnlyEnforceIf(var)
                    model.Add(t_start == s_idx).OnlyEnforceIf(var)

        if candidates:
            model.Add(sum(candidates) == 1).OnlyEnforceIf(is_scheduled[uid])
            model.Add(sum(candidates) == 0).OnlyEnforceIf(is_scheduled[uid].Not())
        else:
            model.Add(is_scheduled[uid] == 0)

    # 1 Task per room at a time
    for d in range(len(DAYS)):
        for s in range(TOTAL_SLOTS):
            for r in room_list:
                if r['room'] == 'Online': continue
                active_in_room = []
                for t in tasks:
                    for dur_idx in range(t['dur']):
                        prev_s = s - dur_idx
                        if (t['uid'], r['room'], d, prev_s) in schedule_vars:
                            active_in_room.append(schedule_vars[(t['uid'], r['room'], d, prev_s)])
                if active_in_room: model.Add(sum(active_in_room) <= 1)

    # Objective: Maximize scheduled courses
    model.Maximize(sum(is_scheduled.values()))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 30
    progress_bar.progress(60, text="Solving constraints...")
    status = solver.Solve(model)

    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        results = []
        for t in tasks:
            uid = t['uid']
            if solver.Value(is_scheduled[uid]):
                d_val = solver.Value(task_data[uid]['day'])
                s_val = solver.Value(task_data[uid]['start'])
                # Find which room was used
                actual_room = "Unknown"
                for (v_uid, v_room, v_day, v_start), var in schedule_vars.items():
                    if v_uid == uid and v_day == d_val and v_start == s_val and solver.Value(var):
                        actual_room = v_room
                        break
                
                results.append({
                    'Day': DAYS[d_val], 'Start': SLOT_MAP[s_val]['time'],
                    'End': SLOT_MAP.get(s_val + t['dur'], {'time': '??'})['time'],
                    'Room': actual_room, 'Course': t['id'], 'Sec': t['sec'],
                    'Type': t['type'], 'Teacher': ", ".join(t['teachers'])
                })
        progress_bar.empty()
        return results, []
    
    progress_bar.empty()
    return None, None

# ==========================================
# 5. Controller & UI Display
# ==========================================
if run_button:
    if not up_room or not up_teacher_courses or not up_ai_in:
        st.error("⚠️ Please upload the required CSV files first!")
    else:
        with st.spinner("Calculating optimal schedule..."):
            res, un = calculate_schedule()
            if res:
                st.session_state['schedule_results'] = pd.DataFrame(res)
                st.session_state['has_run'] = True
                st.success("✅ Schedule generated successfully!")
            else:
                st.error("❌ Could not find a valid schedule. Try 'Flexible Mode'.")

if st.session_state['has_run']:
    df_res = st.session_state['schedule_results']
    
    st.divider()
    
    # Room Selection
    rooms = sorted(df_res['Room'].unique())
    selected_room = st.selectbox("🔍 View Timetable by Room:", rooms)

    # --- Timetable Grid ---
    def create_grid(df, room_name):
        days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
        hours = [f"{h:02d}:00" for h in range(8, 20)]
        grid = pd.DataFrame('', index=days, columns=hours)
        
        room_data = df[df['Room'] == room_name]
        for _, row in room_data.iterrows():
            start_h = row['Start'].split(':')[0] + ":00"
            content = f"{row['Course']} (S{row['Sec']})"
            if start_h in grid.columns:
                grid.at[row['Day'], start_h] = content
        return grid

    st.subheader(f"📍 Timetable: {selected_room}")
    st.table(create_grid(df_res, selected_room))
    
    # Download
    csv_data = df_res.to_csv(index=False).encode('utf-8')
    st.download_button("📥 Download Full Schedule (CSV)", csv_data, "schedule.csv", "text/csv")
