import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import math
import re
import os
from collections import defaultdict

# ==========================================
# 1. Page Config & CSS (Visuals)
# ==========================================
st.set_page_config(page_title="Automatic Scheduler Pro", layout="wide")

st.markdown("""
<style>
    .tt-container { overflow-x: auto; font-family: 'Helvetica', sans-serif; margin-top: 20px; }
    .tt-table { width: 100%; border-collapse: collapse; min-width: 1200px; }
    .tt-table th, .tt-table td { border: 1px solid #dee2e6; text-align: center; padding: 4px; font-size: 11px; height: 60px; }
    .tt-header { background-color: #343a40; color: white; position: sticky; left: 0; }
    .tt-day { background-color: #f8f9fa; font-weight: bold; width: 60px; position: sticky; left: 0; z-index: 10; border-right: 2px solid #ccc; }
    .class-box { 
        background-color: #e7f1ff; border: 1px solid #b6d4fe; border-radius: 4px;
        padding: 2px; height: 100%; display: flex; flex-direction: column; justify-content: center;
        color: #084298; box-shadow: 1px 1px 2px rgba(0,0,0,0.1); font-size: 10px; line-height: 1.2;
    }
    .c-code { font-weight: bold; text-decoration: underline; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. Helper Functions
# ==========================================
def get_slot_map():
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
    return SLOT_MAP

def time_to_slot_index(time_str, slot_to_index_map):
    time_str = str(time_str).strip()
    match = re.search(r"(\d{1,2})[:.](\d{2})", time_str)
    if match:
        h, m = match.groups()
        time_str = f"{int(h):02d}:{int(m):02d}"
        if time_str in slot_to_index_map:
            return slot_to_index_map[time_str]
    return -1

def parse_unavailable_time(unavailable_input, days_list, slot_to_index_map):
    unavailable_slots_by_day = {d_idx: set() for d_idx in range(len(days_list))}
    target_list = []
    if isinstance(unavailable_input, list): target_list = unavailable_input
    elif isinstance(unavailable_input, str): target_list = [unavailable_input]
    else: return unavailable_slots_by_day

    for item in target_list:
        if isinstance(item, list): ut_str = item[0] if len(item) > 0 else ""
        else: ut_str = str(item)

        ut_str = ut_str.replace('[', '').replace(']', '').replace("'", "").replace('"', "")
        match = re.search(r"(\w{3})\s+(\d{1,2}[:.]\d{2})-(\d{1,2}[:.]\d{2})", ut_str)
        if not match: continue

        day_abbr, start_time_str, end_time_str = match.groups()
        start_time_str = start_time_str.replace('.', ':')
        end_time_str = end_time_str.replace('.', ':')

        try: day_idx = days_list.index(day_abbr)
        except ValueError: continue

        start_slot = time_to_slot_index(start_time_str, slot_to_index_map)
        end_slot = time_to_slot_index(end_time_str, slot_to_index_map)

        if start_slot == -1 or end_slot == -1 or start_slot >= end_slot: continue
        for slot in range(start_slot, end_slot):
            unavailable_slots_by_day[day_idx].add(slot)
    return unavailable_slots_by_day

# ==========================================
# 3. Main Scheduler Logic
# ==========================================
# เพิ่ม Parameter: solver_time, penalty_score
def calculate_schedule(files, mode, solver_time, penalty_score):
    logs = []
    
    # 1. Setup Time Slots
    SLOT_MAP = get_slot_map()
    TOTAL_SLOTS = len(SLOT_MAP)
    DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
    SLOT_TO_INDEX = {v['time']: k for k, v in SLOT_MAP.items()}
    
    # 2. Load Data
    try:
        df_room = pd.read_csv(files['room'])
        room_list = df_room.to_dict('records')
        room_list.append({'room': 'Online', 'capacity': 9999, 'type': 'virtual'})

        df_teacher_courses = pd.read_csv(files['teacher_courses'])
        df_ai_in = pd.read_csv(files['ai_in'])
        df_cy_in = pd.read_csv(files['cy_in'])
        all_teacher = pd.read_csv(files['teachers'])
        
        # Clean Data
        df_teacher_courses.columns = df_teacher_courses.columns.str.strip()
        df_ai_in.columns = df_ai_in.columns.str.strip()
        df_cy_in.columns = df_cy_in.columns.str.strip()

        df_courses = pd.concat([df_ai_in, df_cy_in], ignore_index=True)
        if 'lec_online' not in df_courses.columns: df_courses['lec_online'] = 0
        if 'lab_online' not in df_courses.columns: df_courses['lab_online'] = 0
        if 'optional' not in df_courses.columns: df_courses['optional'] = 1
        df_courses = df_courses.fillna(0)

        df_teacher_courses['course_code'] = df_teacher_courses['course_code'].astype(str).str.strip()
        df_courses['course_code'] = df_courses['course_code'].astype(str).str.strip()

        teacher_map = {}
        for _, row in df_teacher_courses.iterrows():
            c_code = row['course_code']
            t_id = str(row['teacher_id']).strip()
            if c_code not in teacher_map: teacher_map[c_code] = []
            teacher_map[c_code].append(t_id)

        all_teacher['teacher_id'] = all_teacher['teacher_id'].astype(str).str.strip()
        TEACHER_UNAVAILABLE_SLOTS = {}
        for _, row in all_teacher.iterrows():
            parsed = parse_unavailable_time(row.get('unavailable_times'), DAYS, SLOT_TO_INDEX)
            TEACHER_UNAVAILABLE_SLOTS[row['teacher_id']] = parsed

        fixed_schedule = []
        fixed_files_map = []
        if files['ai_out']: fixed_files_map.append(('ai_out_courses.csv', files['ai_out']))
        if files['cy_out']: fixed_files_map.append(('cy_out_courses.csv', files['cy_out']))

        for fname, fobj in fixed_files_map:
            try:
                df_fixed = pd.read_csv(fobj)
                logs.append(f"Found fixed data in {fname}: {len(df_fixed)} items")
                for _, row in df_fixed.iterrows():
                    day_str = str(row['day']).strip()[:3]
                    course_code = str(row['course_code']).strip()
                    sec = int(row['section']) if str(row['section']).isdigit() else 0
                    room = str(row['room']).strip()
                    start_time = str(row['start']).strip()
                    lec_h = row.get('lecture_hour', 0)
                    lab_h = row.get('lab_hour', 0)
                    if pd.isna(lec_h): lec_h = 0
                    if pd.isna(lab_h): lab_h = 0

                    if lec_h > 0:
                         fixed_schedule.append({'course': course_code, 'sec': sec, 'type': 'Lec', 
                                                'room': room, 'day': day_str, 'start': start_time, 'duration': int(math.ceil(lec_h * 2))})
                    if lab_h > 0:
                         fixed_schedule.append({'course': course_code, 'sec': sec, 'type': 'Lab', 
                                                'room': room, 'day': day_str, 'start': start_time, 'duration': int(math.ceil(lab_h * 2))})
            except Exception as e:
                logs.append(f"Error reading fixed file {fname}: {e}")

    except Exception as e:
        return None, None, [f"Critical Data Error: {e}"]

    # 3. Task Preparation
    tasks = []
    MAX_LEC_SESSION_SLOTS = 6
    course_optional_map = df_courses.set_index(['course_code', 'section'])['optional'].to_dict()

    for lock in fixed_schedule:
        uid = f"{lock['course']}_S{lock['sec']}_{lock['type']}"
        course_match = df_courses[(df_courses['course_code'] == lock['course']) & (df_courses['section'] == lock['sec'])]
        is_online_lec = course_match['lec_online'].iloc[0] == 1 if not course_match.empty else False
        is_online_lab = course_match['lab_online'].iloc[0] == 1 if not course_match.empty else False
        is_task_online = is_online_lec if lock['type'] == 'Lec' else is_online_lab
        
        tasks.append({
            'uid': uid, 'id': lock['course'], 'sec': lock['sec'], 'type': lock['type'],
            'dur': lock['duration'], 
            'std': course_match['enrollment_count'].iloc[0] if not course_match.empty else 50,
            'teachers': teacher_map.get(lock['course'], ['External_Faculty']),
            'is_online': is_task_online,
            'is_optional': course_optional_map.get((lock['course'], lock['sec']), 1),
            'fixed_room': True
        })

    for _, row in df_courses.iterrows():
        teachers = teacher_map.get(row['course_code'], ['Unknown'])
        curr_lec = int(math.ceil(row['lecture_hour'] * 2))
        part = 1
        while curr_lec > 0:
            dur = min(curr_lec, MAX_LEC_SESSION_SLOTS)
            uid = f"{row['course_code']}_S{row['section']}_Lec_P{part}"
            if not any(t['uid'] == uid for t in tasks):
                tasks.append({
                    'uid': uid, 'id': row['course_code'], 'sec': row['section'], 'type': 'Lec',
                    'dur': dur, 'std': row['enrollment_count'], 'teachers': teachers,
                    'is_online': (row['lec_online'] == 1), 'is_optional': row['optional']
                })
            curr_lec -= dur
            part += 1
        
        lab_slots = int(math.ceil(row['lab_hour'] * 2))
        if lab_slots > 0:
            uid = f"{row['course_code']}_S{row['section']}_Lab"
            if not any(t['uid'] == uid for t in tasks):
                tasks.append({
                    'uid': uid, 'id': row['course_code'], 'sec': row['section'], 'type': 'Lab',
                    'dur': lab_slots, 'std': row['enrollment_count'], 'teachers': teachers,
                    'is_online': (row['lab_online'] == 1), 'is_optional': row['optional'],
                    'req_ai': (row.get('require_lab_ai', 0) == 1),
                    'req_network': (row.get('require_lab_network', 0) == 1)
                })

    # 4. Solver Model
    model = cp_model.CpModel()
    schedule = {}
    is_scheduled = {}
    task_vars = {}
    penalty_vars = []
    objective_terms = []

    SCORE_FIXED = 1000000 
    SCORE_CORE = 1000
    SCORE_ELEC = 100

    for t in tasks:
        uid = t['uid']
        is_scheduled[uid] = model.NewBoolVar(f"sched_{uid}")
        t_day = model.NewIntVar(0, len(DAYS)-1, f"d_{uid}")
        t_start = model.NewIntVar(0, TOTAL_SLOTS-1, f"s_{uid}")
        model.Add(model.NewIntVar(0, TOTAL_SLOTS+10, f"e_{uid}") == t_start + t['dur'])
        task_vars[uid] = {'day': t_day, 'start': t_start}

        candidates = []
        for r in room_list:
            if t['is_online']: 
                if r['room'] != 'Online': continue
            else:
                if r['room'] == 'Online': continue
                if r['capacity'] < t['std']: continue
                if t['type'] == 'Lab' and 'lab' not in r['type']: continue
                if t.get('req_ai', False) and r['room'] != 'lab_ai': continue
                if t.get('req_network', False) and r['room'] != 'lab_network': continue

            for d_idx in range(len(DAYS)):
                for s_idx in SLOT_MAP:
                    s_val = SLOT_MAP[s_idx]['val']
                    e_val = s_val + (t['dur'] * 0.5)

                    if mode == 1:
                        if s_val < 9.0 or e_val > 16.0: continue
                    else: # Mode 2
                        if s_idx + t['dur'] > TOTAL_SLOTS: continue

                    if any(SLOT_MAP.get(s_idx + i, {}).get('is_lunch', False) for i in range(t['dur'])): continue

                    conflict = False
                    for tid in t['teachers']:
                        if tid in ['External_Faculty', 'Unknown']: continue
                        if tid in TEACHER_UNAVAILABLE_SLOTS:
                            unavail = TEACHER_UNAVAILABLE_SLOTS[tid].get(d_idx, set())
                            task_s = set(range(s_idx, s_idx + t['dur']))
                            if not task_s.isdisjoint(unavail): 
                                conflict = True; break
                    if conflict: continue

                    var = model.NewBoolVar(f"{uid}_{r['room']}_{d_idx}_{s_idx}")
                    schedule[(uid, r['room'], d_idx, s_idx)] = var
                    candidates.append(var)
                    
                    model.Add(t_day == d_idx).OnlyEnforceIf(var)
                    model.Add(t_start == s_idx).OnlyEnforceIf(var)

                    # ✅ ใช้ตัวแปร penalty_score ที่รับเข้ามา
                    if mode == 2 and (s_val < 9.0 or e_val > 16.0):
                        # คูณคะแนนด้วย penalty_score (ถ้าผู้ใช้ตั้งสูง โทษก็จะหนักขึ้น)
                        penalty_vars.append(var * penalty_score)

        if not candidates:
            model.Add(is_scheduled[uid] == 0)
            logs.append(f"Warning: Task {uid} has no valid placement options.")
        else:
            model.Add(sum(candidates) == 1).OnlyEnforceIf(is_scheduled[uid])
            model.Add(sum(candidates) == 0).OnlyEnforceIf(is_scheduled[uid].Not())

        if 'fixed_room' in t: objective_terms.append(is_scheduled[uid] * SCORE_FIXED)
        elif t.get('is_optional') == 0: objective_terms.append(is_scheduled[uid] * SCORE_CORE)
        else: objective_terms.append(is_scheduled[uid] * SCORE_ELEC)

    # 5. Overlap Constraints
    for d in range(len(DAYS)):
        for s in range(TOTAL_SLOTS):
            for r in [rm['room'] for rm in room_list if rm['room'] != 'Online']:
                active = []
                for t in tasks:
                    for offset in range(t['dur']):
                        if (t['uid'], r, d, s - offset) in schedule:
                            active.append(schedule[(t['uid'], r, d, s - offset)])
                if active: model.Add(sum(active) <= 1)
            
            all_t = set(tea for t in tasks for tea in t['teachers'] if tea != 'Unknown')
            for tea in all_t:
                active = []
                for t in tasks:
                    if tea in t['teachers']:
                        for r in [rm['room'] for rm in room_list]:
                             for offset in range(t['dur']):
                                if (t['uid'], r, d, s - offset) in schedule:
                                    active.append(schedule[(t['uid'], r, d, s - offset)])
                if active: model.Add(sum(active) <= 1)

    # 6. Solve
    model.Maximize(sum(objective_terms) - sum(penalty_vars))
    solver = cp_model.CpSolver()
    
    # ✅ ใช้ตัวแปร solver_time ที่รับเข้ามา
    solver.parameters.max_time_in_seconds = solver_time 
    
    status = solver.Solve(model)

    res, unsched = [], []
    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        for t in tasks:
            uid = t['uid']
            if uid in is_scheduled and solver.Value(is_scheduled[uid]):
                d = solver.Value(task_vars[uid]['day'])
                s = solver.Value(task_vars[uid]['start'])
                r_name = "Unknown"
                for (tid, r, dx, sx), var in schedule.items():
                    if tid == uid and dx == d and sx == s and solver.Value(var):
                        r_name = r; break
                
                start_str = SLOT_MAP[s]['time']
                end_str = SLOT_MAP.get(s + t['dur'], {'time': '19:00'})['time']
                
                notes = []
                if t['is_online']: notes.append("Online")
                s_val = SLOT_MAP[s]['val']
                e_val = s_val + (t['dur'] * 0.5)
                if s_val < 9.0 or e_val > 16.0: notes.append("Ext.Time")
                
                res.append({
                    'Day': DAYS[d], 'Start': start_str, 'End': end_str, 'Room': r_name,
                    'Course': t['id'], 'Sec': t['sec'], 'Type': t['type'], 
                    'Teacher': ",".join(t['teachers']), 'Note': ", ".join(notes)
                })
            else:
                unsched.append({'Course': t['id'], 'Sec': t['sec'], 'Type': t['type'], 'Reason': 'Solver/Constraints'})
    
    return pd.DataFrame(res), pd.DataFrame(unsched), logs

# ==========================================
# 4. Streamlit UI
# ==========================================
st.sidebar.header("📂 1. Upload Data")
files = {
    'room': st.sidebar.file_uploader("room.csv", type="csv"),
    'teacher_courses': st.sidebar.file_uploader("teacher_courses.csv", type="csv"),
    'ai_in': st.sidebar.file_uploader("ai_in_courses.csv", type="csv"),
    'cy_in': st.sidebar.file_uploader("cy_in_courses.csv", type="csv"),
    'teachers': st.sidebar.file_uploader("all_teachers.csv", type="csv"),
    'ai_out': st.sidebar.file_uploader("ai_out_courses.csv (Optional)", type="csv"),
    'cy_out': st.sidebar.file_uploader("cy_out_courses.csv (Optional)", type="csv"),
}

st.sidebar.divider()
st.sidebar.header("⚙️ 2. Configuration")

# ✅ เพิ่มตัวแปรปรับได้ใน Sidebar ตามข้อ 3
mode_sel = st.sidebar.radio("Scheduling Mode:", [1, 2], 
                            format_func=lambda x: "1: Compact (09:00-16:00)" if x==1 else "2: Flexible (08:30-19:00)")

solver_time_limit = st.sidebar.slider("Solver Time Limit (Seconds):", 10, 300, 120)
penalty_val = st.sidebar.slider("Penalty Score (Ext. Time):", 0, 100, 10, help="Higher penalty means solver will strongly avoid 08:30 or 16:30+")

if st.button("🚀 Run Scheduler", use_container_width=True):
    missing = [k for k, v in files.items() if v is None and 'out' not in k]
    if missing:
        st.error(f"Missing required files: {', '.join(missing)}")
    else:
        with st.spinner("Running Logic..."):
            # ✅ ส่งค่า solver_time_limit และ penalty_val ไปยังฟังก์ชัน
            df_res, df_un, logs = calculate_schedule(files, mode_sel, solver_time_limit, penalty_val)
            
            with st.expander("📝 Process Logs", expanded=False):
                for log in logs: st.write(log)

            if df_res is not None and not df_res.empty:
                st.session_state['res'], st.session_state['un'] = df_res, df_un
                st.success(f"Scheduled {len(df_res)} classes!")
            else:
                st.error("No schedule generated. Check logs or constraints.")

# --- Visualization ---
if 'res' in st.session_state:
    df_res = st.session_state['res']
    df_un = st.session_state['un']

    # ✅ เพิ่มมุมมองอาจารย์ ตามข้อ 2
    view_mode = st.radio("View By:", ["Room", "Teacher"], horizontal=True)
    
    if view_mode == "Room":
        target = st.selectbox("Select Room:", sorted(df_res['Room'].unique()))
        filtered = df_res[df_res['Room'] == target]
    else:
        all_t = sorted(list(set([t.strip() for ts in df_res['Teacher'] for t in str(ts).split(',')])))
        target = st.selectbox("Select Teacher:", all_t)
        filtered = df_res[df_res['Teacher'].str.contains(target)]

    # HTML Render (Blue Box)
    days_map = {'Mon': 'จันทร์', 'Tue': 'อังคาร', 'Wed': 'พุธ', 'Thu': 'พฤหัสบดี', 'Fri': 'ศุกร์'}
    time_cols = [f"{h:02d}:{m:02d}" for h in range(8, 19) for m in [0, 30]][1:] # 08:30 start
    
    html = f"<div class='tt-container'><table class='tt-table'><tr class='tt-header'><th>Day</th>"
    for t in time_cols: html += f"<th>{t}</th>"
    html += "</tr>"

    for day in ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']:
        html += f"<tr><td class='tt-day'>{days_map[day]}</td>"
        day_data = filtered[filtered['Day'] == day]
        
        curr_t = 8.5
        while curr_t < 19.0:
            h, m = int(curr_t), int((curr_t%1)*60)
            t_str = f"{h:02d}:{m:02d}"
            
            # Find class starting here
            match = day_data[day_data['Start'] == t_str]
            if not match.empty:
                row = match.iloc[0]
                # Calc duration
                sh, sm = map(int, row['Start'].split(':'))
                eh, em = map(int, row['End'].split(':'))
                dur_units = int(((eh + em/60) - (sh + sm/60)) * 2)
                
                html += f"<td colspan='{dur_units}'><div class='class-box'>"
                html += f"<span class='c-code'>{row['Course']}</span>"
                html += f"<span>(S{row['Sec']}) {row['Type']}</span>"
                html += f"<span>{row['Teacher']}</span>"
                if row['Note']: html += f"<span style='color:red; font-size:9px'>{row['Note']}</span>"
                html += "</div></td>"
                curr_t += (dur_units * 0.5)
            else:
                html += "<td></td>"
                curr_t += 0.5
        html += "</tr>"
    html += "</table></div>"
    
    st.markdown(html, unsafe_allow_html=True)

    if not df_un.empty:
        st.warning(f"Unscheduled Tasks: {len(df_un)}")
        st.dataframe(df_un)
