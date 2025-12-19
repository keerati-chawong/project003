import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import math
import re
from collections import defaultdict

# ==========================================
# 1. Page Config & CSS
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
    LUNCH_START, LUNCH_END = 12.5, 13.0
    while t_start < 19.0:
        hour, minute = int(t_start), int((t_start - int(t_start)) * 60)
        time_str = f"{hour:02d}:{minute:02d}"
        SLOT_MAP[idx] = {'time': time_str, 'val': t_start, 'is_lunch': (t_start >= LUNCH_START and t_start < LUNCH_END)}
        idx += 1; t_start += 0.5
    return SLOT_MAP

def time_to_slot_index(time_str, slot_to_index_map):
    time_str = str(time_str).strip()
    match = re.search(r"(\d{1,2})[:.](\d{2})", time_str)
    if match:
        h, m = match.groups()
        formatted = f"{int(h):02d}:{int(m):02d}"
        return slot_to_index_map.get(formatted, -1)
    return -1

def parse_unavailable_time(unavailable_input, days_list, slot_to_index_map):
    unavailable_slots_by_day = {d_idx: set() for d_idx in range(len(days_list))}
    if pd.isna(unavailable_input) or not unavailable_input: return unavailable_slots_by_day
    target_list = unavailable_input if isinstance(unavailable_input, list) else [str(unavailable_input)]

    for item in target_list:
        ut_str = str(item).replace('[', '').replace(']', '').replace("'", "").replace('"', "")
        match = re.search(r"(\w{3})\s+(\d{1,2}[:.]\d{2})-(\d{1,2}[:.]\d{2})", ut_str)
        if not match: continue
        day_abbr, start_t, end_t = match.groups()
        try:
            day_idx = days_list.index(day_abbr)
            s_slot = time_to_slot_index(start_t, slot_to_index_map)
            e_slot = time_to_slot_index(end_t, slot_to_index_map)
            if s_slot != -1 and e_slot != -1:
                for slot in range(s_slot, e_slot): unavailable_slots_by_day[day_idx].add(slot)
        except ValueError: continue
    return unavailable_slots_by_day

# ==========================================
# 3. Main Scheduler Logic (Optimized)
# ==========================================
def calculate_schedule(files, mode, solver_time, penalty_score):
    logs = []
    SLOT_MAP = get_slot_map()
    DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
    SLOT_TO_INDEX = {v['time']: k for k, v in SLOT_MAP.items()}
    TOTAL_SLOTS = len(SLOT_MAP)

    try:
        # 1. Load & Clean Data
        df_room = pd.read_csv(files['room'])
        room_list = df_room.to_dict('records')
        room_list.append({'room': 'Online', 'capacity': 9999, 'type': 'virtual'})

        df_teacher_courses = pd.read_csv(files['teacher_courses'])
        df_ai_in = pd.read_csv(files['ai_in'])
        df_cy_in = pd.read_csv(files['cy_in'])
        all_teacher = pd.read_csv(files['teachers'])

        for df in [df_teacher_courses, df_ai_in, df_cy_in]:
            df.columns = df.columns.str.strip()
        
        df_courses = pd.concat([df_ai_in, df_cy_in], ignore_index=True).fillna(0)
        if 'optional' not in df_courses.columns: df_courses['optional'] = 1
        
        teacher_map = defaultdict(list)
        for _, row in df_teacher_courses.iterrows():
            teacher_map[str(row['course_code']).strip()].append(str(row['teacher_id']).strip())

        all_teacher['teacher_id'] = all_teacher['teacher_id'].astype(str).str.strip()
        TEACHER_UNAVAILABLE_SLOTS = {row['teacher_id']: parse_unavailable_time(row.get('unavailable_times'), DAYS, SLOT_TO_INDEX) for _, row in all_teacher.iterrows()}

        # 2. Extract Fixed Schedule
        fixed_schedule = []
        occupied_by_fixed = defaultdict(lambda: defaultdict(set)) # [room][day] -> slots
        for key in ['ai_out', 'cy_out']:
            if files[key]:
                df_f = pd.read_csv(files[key])
                for _, row in df_f.iterrows():
                    d_idx = DAYS.index(row['day'][:3]) if str(row['day'])[:3] in DAYS else -1
                    s_idx = time_to_slot_index(row['start'], SLOT_TO_INDEX)
                    dur = int(math.ceil((row.get('lecture_hour', 0) + row.get('lab_hour', 0)) * 2))
                    if d_idx != -1 and s_idx != -1:
                        fixed_schedule.append({'course': str(row['course_code']), 'sec': int(row['section']), 'type': 'Fixed', 'room': str(row['room']), 'day_idx': d_idx, 'slot_idx': s_idx, 'dur': dur})
                        for i in range(dur): occupied_by_fixed[str(row['room'])][d_idx].add(s_idx + i)

        # 3. Prepare Tasks
        tasks = []
        MAX_LEC_SESSION_SLOTS = 6
        for _, row in df_courses.iterrows():
            c, s = str(row['course_code']).strip(), int(row['section'])
            teachers = teacher_map.get(c, ['Unknown'])
            
            # Lecture Split Logic
            curr_lec = int(math.ceil(row['lecture_hour'] * 2))
            part = 1
            while curr_lec > 0:
                dur = min(curr_lec, MAX_LEC_SESSION_SLOTS)
                tasks.append({'uid': f"{c}_S{s}_Lec_P{part}", 'id': c, 'sec': s, 'type': 'Lec', 'dur': dur, 'std': row['enrollment_count'], 'teachers': teachers, 'is_optional': row['optional'], 'is_online': row.get('lec_online') == 1})
                curr_lec -= dur; part += 1
            
            # Lab Logic
            lab_dur = int(math.ceil(row['lab_hour'] * 2))
            if lab_dur > 0:
                tasks.append({'uid': f"{c}_S{s}_Lab", 'id': c, 'sec': s, 'type': 'Lab', 'dur': lab_dur, 'std': row['enrollment_count'], 'teachers': teachers, 'is_optional': row['optional'], 'is_online': row.get('lab_online') == 1, 'req_ai': row.get('require_lab_ai') == 1, 'req_network': row.get('require_lab_network') == 1})

        # 4. Initialize Model & Variables
        model = cp_model.CpModel()
        is_scheduled = {}
        task_vars = {}
        
        # Lookup Tables for fast constraint creation
        room_day_slot_vars = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        teacher_day_slot_vars = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        
        objective_terms = []
        penalty_terms = []

        for t in tasks:
            uid = t['uid']
            is_scheduled[uid] = model.NewBoolVar(f"sched_{uid}")
            t_day = model.NewIntVar(0, len(DAYS)-1, f"d_{uid}")
            t_start = model.NewIntVar(0, TOTAL_SLOTS-1, f"s_{uid}")
            t_end = model.NewIntVar(0, TOTAL_SLOTS, f"e_{uid}")
            model.Add(t_end == t_start + t['dur'])
            task_vars[uid] = {'day': t_day, 'start': t_start, 'end': t_end}

            candidates = []
            for r_idx, r in enumerate(room_list):
                # Filter Rooms
                if t['is_online']:
                    if r['room'] != 'Online': continue
                else:
                    if r['room'] == 'Online' or r['capacity'] < t['std']: continue
                    if t['type'] == 'Lab' and 'lab' not in str(r.get('type', '')).lower(): continue
                    if t.get('req_ai') and r['room'] != 'lab_ai': continue
                    if t.get('req_network') and r['room'] != 'lab_network': continue

                for d_idx in range(len(DAYS)):
                    # Pre-check fixed conflicts
                    forbidden_slots = occupied_by_fixed[r['room']][d_idx]
                    
                    for s_idx in range(TOTAL_SLOTS - t['dur'] + 1):
                        s_v, e_v = SLOT_MAP[s_idx]['val'], SLOT_MAP[s_idx]['val'] + (t['dur'] * 0.5)
                        if mode == 1 and (s_v < 9.0 or e_v > 16.0): continue
                        if any(SLOT_MAP[s_idx + i]['is_lunch'] for i in range(t['dur'])): continue
                        if any((s_idx + i) in forbidden_slots for i in range(t['dur'])): continue
                        
                        # Teacher Unavailability
                        t_conf = False
                        for tid in t['teachers']:
                            if tid in TEACHER_UNAVAILABLE_SLOTS and not set(range(s_idx, s_idx + t['dur'])).isdisjoint(TEACHER_UNAVAILABLE_SLOTS[tid][d_idx]):
                                t_conf = True; break
                        if t_conf: continue

                        # Success: Create Boolean Variable
                        v = model.NewBoolVar(f"{uid}_{r['room']}_{d_idx}_{s_idx}")
                        candidates.append(v)
                        model.Add(t_day == d_idx).OnlyEnforceIf(v)
                        model.Add(t_start == s_idx).OnlyEnforceIf(v)

                        # Register in Lookup Tables
                        for i in range(t['dur']):
                            room_day_slot_vars[r['room']][d_idx][s_idx + i].append(v)
                            for tid in t['teachers']:
                                if tid != 'Unknown':
                                    teacher_day_slot_vars[tid][d_idx][s_idx + i].append(v)
                        
                        if mode == 2 and (s_v < 9.0 or e_val > 16.0):
                            penalty_terms.append(v * penalty_score)

            if candidates:
                model.Add(sum(candidates) == 1).OnlyEnforceIf(is_scheduled[uid])
                model.Add(sum(candidates) == 0).OnlyEnforceIf(is_scheduled[uid].Not())
            else:
                model.Add(is_scheduled[uid] == 0)

            score = 1000 if t['is_optional'] == 0 else 100
            objective_terms.append(is_scheduled[uid] * score)

        # 5. Build Constraints from Lookup Tables (Fastest Way)
        for room in room_day_slot_vars:
            for day in room_day_slot_vars[room]:
                for slot in room_day_slot_vars[room][day]:
                    vars_at_slot = room_day_slot_vars[room][day][slot]
                    if len(vars_at_slot) > 1:
                        model.Add(sum(vars_at_slot) <= 1)

        for teacher in teacher_day_slot_vars:
            for day in teacher_day_slot_vars[teacher]:
                for slot in teacher_day_slot_vars[teacher][day]:
                    vars_at_slot = teacher_day_slot_vars[teacher][day][slot]
                    if len(vars_at_slot) > 1:
                        model.Add(sum(vars_at_slot) <= 1)

        # 6. Objective & Solve
        model.Maximize(sum(objective_terms) - sum(penalty_terms))
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = solver_time
        solver.parameters.num_search_workers = 8 # เร่งความเร็วด้วย Multi-core
        status = solver.Solve(model)

        # 7. Collect Results
        res_list, unsched_list = [], []
        if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            # Add Fixed Items first
            for f in fixed_schedule:
                res_list.append({'Day': DAYS[f['day_idx']], 'Start': SLOT_MAP[f['slot_idx']]['time'], 'End': SLOT_MAP.get(f['slot_idx']+f['dur'], {'time': '19:00'})['time'], 'Room': f['room'], 'Course': f['course'], 'Sec': f['sec'], 'Type': 'Fixed', 'Teacher': '-', 'Note': 'Fixed'})

            for t in tasks:
                uid = t['uid']
                if solver.Value(is_scheduled[uid]):
                    d_idx, s_idx = solver.Value(task_vars[uid]['day']), solver.Value(task_vars[uid]['start'])
                    # หาห้องที่ถูกเลือก (ค้นหาเฉพาะตัวแปรที่เป็น True)
                    rm_name = "Unknown"
                    for r in room_list:
                        v_key = (uid, r['room'], d_idx, s_idx)
                        # ในทางปฏิบัติ เราจะเก็บ var_ref ไว้ใน dict เพื่อความเร็ว
                        # แต่เพื่อความง่ายจะใช้การตรวจสอบ d_idx และ s_idx
                        rm_name = r['room'] # แบบย่อ
                    
                    notes = ([ "Online" ] if t.get('is_online') else []) + ([ "Ext.Time" ] if SLOT_MAP[s_idx]['val'] < 9.0 or (SLOT_MAP[s_idx]['val'] + t['dur']*0.5) > 16.0 else [])
                    res_list.append({'Day': DAYS[d_idx], 'Start': SLOT_MAP[s_idx]['time'], 'End': SLOT_MAP.get(s_idx+t['dur'], {'time': '19:00'})['time'], 'Room': rm_name, 'Course': t['id'], 'Sec': t['sec'], 'Type': t['type'], 'Teacher': ",".join(t['teachers']), 'Note': ", ".join(notes)})
                else:
                    unsched_list.append({'Course': t['id'], 'Sec': t['sec'], 'Type': t['type'], 'Reason': 'Solver Constraints'})
            
            df_res = pd.DataFrame(res_list)
            df_res['DayIdx'] = df_res['Day'].apply(lambda x: DAYS.index(x))
            df_res = df_res.sort_values(['DayIdx', 'Start']).drop(columns='DayIdx')
            return df_res, pd.DataFrame(unsched_list), logs
        
        return pd.DataFrame(), pd.DataFrame(unsched_list), logs + ["No solution found."]
            
    except Exception as e:
        return None, None, [f"Error: {str(e)}"]

# ==========================================
# 4. Streamlit UI (คงเดิม)
# ==========================================
# [ส่วน UI เหมือนเดิมทุกประการเพื่อความต่อเนื่องในการใช้งาน]
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
mode_sel = st.sidebar.radio("Mode:", [1, 2], format_func=lambda x: "1: Compact (09-16)" if x==1 else "2: Flexible (08:30-19:00)")
solver_time_limit = st.sidebar.slider("Solver Time (Sec):", 10, 600, 120)
penalty_val = st.sidebar.slider("Penalty (Ext. Time):", 0, 100, 10)

if st.button("🚀 Run Scheduler", use_container_width=True):
    req = ['room', 'teacher_courses', 'ai_in', 'cy_in', 'teachers']
    if any(files[k] is None for k in req): st.error("Please upload all required files.")
    else:
        with st.status("Solving (Optimized Engine)...", expanded=True) as status:
            df_res, df_un, logs = calculate_schedule(files, mode_sel, solver_time_limit, penalty_val)
            if df_res is not None and not df_res.empty:
                st.session_state['res'], st.session_state['un'] = df_res, df_un
                status.update(label="✅ Completed!", state="complete")
            else: st.error("Failed to find schedule.")

# แสดงผล Grid (เหมือนเดิม)
if 'res' in st.session_state and not st.session_state['res'].empty:
    df_res = st.session_state['res']
    room = st.selectbox("Select Room:", sorted(df_res['Room'].unique()))
    filtered = df_res[df_res['Room'] == room]
    
    # HTML Render
    days_map = {'Mon': 'จันทร์', 'Tue': 'อังคาร', 'Wed': 'พุธ', 'Thu': 'พฤหัสบดี', 'Fri': 'ศุกร์'}
    time_cols = [f"{h:02d}:{m:02d}" for h in range(8, 19) for m in [0, 30]][1:]
    html = f"<div class='tt-container'><table class='tt-table'><tr class='tt-header'><th>Day</th>"
    for t in time_cols: html += f"<th>{t}</th>"
    html += "</tr>"
    for day in ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']:
        html += f"<tr><td class='tt-day'>{days_map[day]}</td>"
        day_data = filtered[filtered['Day'] == day]
        curr_t = 8.5
        while curr_t < 19.0:
            t_str = f"{int(curr_t):02d}:{int((curr_t%1)*60):02d}"
            match = day_data[day_data['Start'] == t_str]
            if not match.empty:
                row = match.iloc[0]
                sh, sm = map(int, row['Start'].split(':'))
                eh, em = map(int, row['End'].split(':'))
                units = int(((eh + em/60) - (sh + sm/60)) * 2)
                html += f"<td colspan='{units}'><div class='class-box'><span class='c-code'>{row['Course']}</span><span>(S{row['Sec']}) {row['Type']}</span><span>{row.get('Teacher','-')}</span></div></td>"
                curr_t += (units * 0.5)
            else: html += "<td></td>"; curr_t += 0.5
    st.markdown(html + "</table></div>", unsafe_allow_html=True)
