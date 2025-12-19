import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import math
import re
import os
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
    if not unavailable_input: return unavailable_slots_by_day
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
# 3. Main Scheduler Logic
# ==========================================
def calculate_schedule(files, mode, solver_time, penalty_score):
    logs = []
    SLOT_MAP = get_slot_map()
    DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
    SLOT_TO_INDEX = {v['time']: k for k, v in SLOT_MAP.items()}
    TOTAL_SLOTS = len(SLOT_MAP)

    try:
        # Load Data
        df_room = pd.read_csv(files['room'])
        room_list = df_room.to_dict('records')
        room_list.append({'room': 'Online', 'capacity': 9999, 'type': 'virtual'})

        df_teacher_courses = pd.read_csv(files['teacher_courses'])
        df_ai_in = pd.read_csv(files['ai_in'])
        df_cy_in = pd.read_csv(files['cy_in'])
        all_teacher = pd.read_csv(files['teachers'])

        # Data Cleaning
        for df in [df_teacher_courses, df_ai_in, df_cy_in]:
            df.columns = df.columns.str.strip()
        
        df_courses = pd.concat([df_ai_in, df_cy_in], ignore_index=True).fillna(0)
        if 'optional' not in df_courses.columns: df_courses['optional'] = 1
        
        teacher_map = defaultdict(list)
        for _, row in df_teacher_courses.iterrows():
            teacher_map[str(row['course_code']).strip()].append(str(row['teacher_id']).strip())

        all_teacher['teacher_id'] = all_teacher['teacher_id'].astype(str).str.strip()
        TEACHER_UNAVAILABLE_SLOTS = {row['teacher_id']: parse_unavailable_time(row.get('unavailable_times'), DAYS, SLOT_TO_INDEX) for _, row in all_teacher.iterrows()}

        # Fixed Schedule
        fixed_schedule = []
        for key in ['ai_out', 'cy_out']:
            if files[key]:
                df_f = pd.read_csv(files[key])
                logs.append(f"Found fixed data in {key}: {len(df_f)} items")
                for _, row in df_f.iterrows():
                    day_str = str(row['day']).strip()[:3]
                    course_code = str(row['course_code']).strip()
                    sec = int(row['section']) if str(row['section']).isdigit() else 0
                    lec_h = row.get('lecture_hour', 0)
                    lab_h = row.get('lab_hour', 0)
                    if lec_h > 0:
                        fixed_schedule.append({'course': course_code, 'sec': sec, 'type': 'Lec', 'room': str(row['room']), 'day': day_str, 'start': str(row['start']), 'duration': int(math.ceil(lec_h * 2))})
                    if lab_h > 0:
                        fixed_schedule.append({'course': course_code, 'sec': sec, 'type': 'Lab', 'room': str(row['room']), 'day': day_str, 'start': str(row['start']), 'duration': int(math.ceil(lab_h * 2))})

        # Task Prep
        tasks = []
        MAX_LEC_SESSION_SLOTS = 6
        course_opt_map = df_courses.set_index(['course_code', 'section'])['optional'].to_dict()

        for lock in fixed_schedule:
            uid = f"{lock['course']}_S{lock['sec']}_{lock['type']}"
            tasks.append({
                'uid': uid, 'id': lock['course'], 'sec': lock['sec'], 'type': lock['type'], 'dur': lock['duration'],
                'std': 50, 'teachers': teacher_map.get(lock['course'], ['External_Faculty']),
                'fixed_room': True, 'target_room': lock['room'], 'is_optional': course_opt_map.get((lock['course'], lock['sec']), 1)
            })

        for _, row in df_courses.iterrows():
            c, s = str(row['course_code']).strip(), int(row['section'])
            # Lec Split
            curr_lec = int(math.ceil(row['lecture_hour'] * 2))
            part = 1
            while curr_lec > 0:
                dur = min(curr_lec, MAX_LEC_SESSION_SLOTS)
                uid = f"{c}_S{s}_Lec_P{part}"
                if not any(t['uid'] == uid for t in tasks):
                    tasks.append({'uid': uid, 'id': c, 'sec': s, 'type': 'Lec', 'dur': dur, 'std': row['enrollment_count'], 'teachers': teacher_map.get(c, ['Unknown']), 'is_optional': row['optional'], 'is_online': row.get('lec_online') == 1})
                curr_lec -= dur; part += 1
            # Lab
            lab_dur = int(math.ceil(row['lab_hour'] * 2))
            if lab_dur > 0:
                uid = f"{c}_S{s}_Lab"
                if not any(t['uid'] == uid for t in tasks):
                    tasks.append({'uid': uid, 'id': c, 'sec': s, 'type': 'Lab', 'dur': lab_dur, 'std': row['enrollment_count'], 'teachers': teacher_map.get(c, ['Unknown']), 'is_optional': row['optional'], 'is_online': row.get('lab_online') == 1, 'req_ai': row.get('require_lab_ai') == 1, 'req_network': row.get('require_lab_network') == 1})

        # Model Building
        model = cp_model.CpModel()
        schedule_vars, is_scheduled, task_vars = {}, {}, {}
        penalty_vars, lec_lab_penalties, objective_terms = [], [], []

        for t in tasks:
            uid = t['uid']
            is_scheduled[uid] = model.NewBoolVar(f"sched_{uid}")
            t_day = model.NewIntVar(0, len(DAYS)-1, f"d_{uid}")
            t_start = model.NewIntVar(0, TOTAL_SLOTS-1, f"s_{uid}")
            t_end = model.NewIntVar(0, TOTAL_SLOTS+10, f"e_{uid}")
            model.Add(t_end == t_start + t['dur'])
            task_vars[uid] = {'day': t_day, 'start': t_start, 'end': t_end}

            candidates = []
            for r in room_list:
                if t.get('is_online'):
                    if r['room'] != 'Online': continue
                else:
                    if r['room'] == 'Online': continue
                    if r['capacity'] < t['std']: continue
                    if t['type'] == 'Lab' and 'lab' not in str(r.get('type', '')).lower(): continue
                    if t.get('req_ai') and r['room'] != 'lab_ai': continue
                    if t.get('req_network') and r['room'] != 'lab_network': continue
                
                # FIXED ROOM CONSTRAINT
                if t.get('fixed_room') and r['room'] != t['target_room']: continue

                for d_idx in range(len(DAYS)):
                    for s_idx in range(TOTAL_SLOTS - t['dur'] + 1):
                        s_v, e_v = SLOT_MAP[s_idx]['val'], SLOT_MAP[s_idx]['val'] + (t['dur'] * 0.5)
                        if mode == 1 and (s_v < 9.0 or e_v > 16.0): continue
                        if any(SLOT_MAP[s_idx + i]['is_lunch'] for i in range(t['dur'])): continue
                        
                        # Teacher Unavailability
                        t_conf = False
                        for tid in t['teachers']:
                            if tid in TEACHER_UNAVAILABLE_SLOTS:
                                if not set(range(s_idx, s_idx + t['dur'])).isdisjoint(TEACHER_UNAVAILABLE_SLOTS[tid][d_idx]):
                                    t_conf = True; break
                        if t_conf: continue

                        v = model.NewBoolVar(f"{uid}_{r['room']}_{d_idx}_{s_idx}")
                        schedule_vars[(uid, r['room'], d_idx, s_idx)] = v
                        candidates.append(v)
                        model.Add(t_day == d_idx).OnlyEnforceIf(v)
                        model.Add(t_start == s_idx).OnlyEnforceIf(v)
                        if mode == 2 and (s_v < 9.0 or e_v > 16.0): penalty_vars.append(v * penalty_score)

            if candidates:
                model.Add(sum(candidates) == 1).OnlyEnforceIf(is_scheduled[uid])
                model.Add(sum(candidates) == 0).OnlyEnforceIf(is_scheduled[uid].Not())
            else:
                model.Add(is_scheduled[uid] == 0)
                logs.append(f"Warning: Task {uid} has no valid placement.")

            score = 1000000 if t.get('fixed_room') else (1000 if t['is_optional'] == 0 else 100)
            objective_terms.append(is_scheduled[uid] * score)

        # Overlap Constraints (Room & Teacher)
        for d in range(len(DAYS)):
            for s in range(TOTAL_SLOTS):
                for r in [rm['room'] for rm in room_list if rm['room'] != 'Online']:
                    active = [schedule_vars[k] for k in schedule_vars if k[1] == r and k[2] == d and k[3] <= s < k[3] + next(x['dur'] for x in tasks if x['uid'] == k[0])]
                    if active: model.Add(sum(active) <= 1)
                
                all_tea = set(tea for tk in tasks for tea in tk['teachers'] if tea != 'Unknown')
                for tea in all_tea:
                    active = [schedule_vars[k] for k in schedule_vars if tea in next(x['teachers'] for x in tasks if x['uid'] == k[0]) and k[2] == d and k[3] <= s < k[3] + next(x['dur'] for x in tasks if x['uid'] == k[0])]
                    if active: model.Add(sum(active) <= 1)

        # Lec/Lab Order Penalty
        course_sec_map = defaultdict(lambda: {'Lec': [], 'Lab': []})
        for t in tasks: course_sec_map[f"{t['id']}_S{t['sec']}"][t['type']].append(t['uid'])
        for cid, tlists in course_sec_map.items():
            if tlists['Lec'] and tlists['Lab']:
                for lec_u in tlists['Lec']:
                    for lab_u in tlists['Lab']:
                        both = model.NewBoolVar(f"both_{lec_u}_{lab_u}")
                        model.AddBoolAnd([is_scheduled[lec_u], is_scheduled[lab_u]]).OnlyEnforceIf(both)
                        wrong_day = model.NewBoolVar(f"wd_{lec_u}_{lab_u}")
                        model.Add(task_vars[lec_u]['day'] > task_vars[lab_u]['day']).OnlyEnforceIf([both, wrong_day])
                        lec_lab_penalties.append(wrong_day)
                        same_day = model.NewBoolVar(f"sd_{lec_u}_{lab_u}")
                        model.Add(task_vars[lec_u]['day'] == task_vars[lab_u]['day']).OnlyEnforceIf([both, same_day])
                        wrong_time = model.NewBoolVar(f"wt_{lec_u}_{lab_u}")
                        model.Add(task_vars[lec_u]['end'] > task_vars[lab_u]['start']).OnlyEnforceIf([both, same_day, wrong_time])
                        lec_lab_penalties.append(wrong_time)

        model.Maximize(sum(objective_terms) - sum(penalty_vars) - sum(lec_lab_penalties) * 10)
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = solver_time
        status = solver.Solve(model)

        res_list, unsched_list = [], []
        cols = ['Day', 'Start', 'End', 'Room', 'Course', 'Sec', 'Type', 'Teacher', 'Note']
        
        if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            for t in tasks:
                uid = t['uid']
                if solver.Value(is_scheduled[uid]):
                    d_idx, s_idx = solver.Value(task_vars[uid]['day']), solver.Value(task_vars[uid]['start'])
                    rm_name = next(k[1] for k, v in schedule_vars.items() if k[0] == uid and k[2] == d_idx and k[3] == s_idx and solver.Value(v))
                    notes = ([ "Online" ] if t.get('is_online') else []) + ([ "Ext.Time" ] if SLOT_MAP[s_idx]['val'] < 9.0 or (SLOT_MAP[s_idx]['val'] + t['dur']*0.5) > 16.0 else [])
                    res_list.append({'Day': DAYS[d_idx], 'Start': SLOT_MAP[s_idx]['time'], 'End': SLOT_MAP.get(s_idx+t['dur'], {'time': '19:00'})['time'], 'Room': rm_name, 'Course': t['id'], 'Sec': t['sec'], 'Type': t['type'], 'Teacher': ",".join(t['teachers']), 'Note': ", ".join(notes)})
                else:
                    unsched_list.append({'Course': t['id'], 'Sec': t['sec'], 'Type': t['type'], 'Reason': 'Constraints'})
            df_res = pd.DataFrame(res_list)
            if not df_res.empty:
                df_res['DayIdx'] = df_res['Day'].apply(lambda x: DAYS.index(x))
                df_res = df_res.sort_values(['DayIdx', 'Start']).drop(columns='DayIdx')
            else: df_res = pd.DataFrame(columns=cols)
            return df_res, pd.DataFrame(unsched_list), logs
        else:
            return pd.DataFrame(columns=cols), pd.DataFrame(unsched_list), logs + ["Solver could not find a solution."]
            
    except Exception as e: return None, None, [f"Critical Error: {e}"]

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
mode_sel = st.sidebar.radio("Mode:", [1, 2], format_func=lambda x: "1: Compact (09-16)" if x==1 else "2: Flexible (08:30-19:00)")
solver_time_limit = st.sidebar.slider("Solver Time (Sec):", 10, 300, 120)
penalty_val = st.sidebar.slider("Penalty (Ext. Time):", 0, 100, 10)

if st.button("🚀 Run Scheduler", use_container_width=True):
    req = ['room', 'teacher_courses', 'ai_in', 'cy_in', 'teachers']
    if any(files[k] is None for k in req): st.error("Please upload all required files.")
    else:
        with st.status("Solving...", expanded=True) as status:
            df_res, df_un, logs = calculate_schedule(files, mode_sel, solver_time_limit, penalty_val)
            for l in logs: st.write(l)
            if df_res is not None:
                st.session_state['res'], st.session_state['un'] = df_res, df_un
                status.update(label="✅ Completed!", state="complete")

if 'res' in st.session_state and not st.session_state['res'].empty:
    df_res, df_un = st.session_state['res'], st.session_state['un']
    view_mode = st.radio("View By:", ["Room", "Teacher"], horizontal=True)
    if view_mode == "Room":
        target = st.selectbox("Select Room:", sorted(df_res['Room'].unique()))
        filtered = df_res[df_res['Room'] == target]
    else:
        all_t = sorted(list(set([t.strip() for ts in df_res['Teacher'] for t in str(ts).split(',')])))
        target = st.selectbox("Select Teacher:", all_t)
        filtered = df_res[df_res['Teacher'].str.contains(target, na=False)]

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
                html += f"<td colspan='{units}'><div class='class-box'><span class='c-code'>{row['Course']}</span><span>(S{row['Sec']}) {row['Type']}</span><span>{row.get('Teacher','Unknown')}</span>"
                if row.get('Note'): html += f"<span style='color:red; font-size:9px'>{row['Note']}</span>"
                html += "</div></td>"
                curr_t += (units * 0.5)
            else: html += "<td></td>"; curr_t += 0.5
    st.markdown(html + "</table></div>", unsafe_allow_html=True)
    if not df_un.empty: st.warning(f"Unscheduled: {len(df_un)} tasks"); st.dataframe(df_un)
elif 'res' in st.session_state:
    st.error("No schedule found. Please try increasing Solver Time or reducing Penalty.")
