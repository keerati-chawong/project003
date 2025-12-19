import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import math
import re
from collections import defaultdict

# ==========================================
# 1. Page Config & CSS (Blue Box Format)
# ==========================================
st.set_page_config(page_title="Automatic Scheduler Pro", layout="wide")

st.markdown("""
<style>
    .tt-container { overflow-x: auto; font-family: 'Helvetica', sans-serif; margin-top: 20px; }
    .tt-table { width: 100%; border-collapse: collapse; min-width: 1200px; }
    .tt-table th, .tt-table td { border: 1px solid #dee2e6; text-align: center; padding: 4px; font-size: 11px; height: 70px; }
    .tt-header { background-color: #343a40; color: white; position: sticky; left: 0; }
    .tt-day { background-color: #f8f9fa; font-weight: bold; width: 60px; position: sticky; left: 0; z-index: 10; border-right: 2px solid #ccc; }
    .class-box { 
        background-color: #e7f1ff; border: 1px solid #b6d4fe; border-radius: 6px;
        padding: 4px; height: 95%; display: flex; flex-direction: column; justify-content: center;
        color: #084298; box-shadow: 1px 1px 3px rgba(0,0,0,0.1); font-size: 10px; line-height: 1.2;
    }
    .c-code { font-weight: bold; text-decoration: underline; font-size: 12px; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. Helper Functions
# ==========================================
def get_slot_map():
    slots = {}
    t_start = 8.5
    idx = 0
    while t_start < 19.0:
        h, m = int(t_start), int((t_start % 1) * 60)
        slots[idx] = {'time': f"{h:02d}:{m:02d}", 'val': t_start, 'is_lunch': (12.5 <= t_start < 13.0)}
        idx += 1; t_start += 0.5
    return slots

def parse_unavailable_time(input_val, days_list, slot_index_map):
    un_slots = {d: set() for d in range(len(days_list))}
    if pd.isna(input_val) or not input_val: return un_slots
    items = input_val if isinstance(input_val, list) else [str(input_val)]
    for item in items:
        match = re.search(r"(\w{3})\s+(\d{1,2}[:.]\d{2})-(\d{1,2}[:.]\d{2})", str(item))
        if match:
            day_abbr, start_t, end_t = match.groups()
            if day_abbr in days_list:
                d_idx = days_list.index(day_abbr)
                s_idx = slot_index_map.get(start_t.replace('.', ':'), -1)
                e_idx = slot_index_map.get(end_t.replace('.', ':'), -1)
                if s_idx != -1 and e_idx != -1:
                    for s in range(s_idx, e_idx): un_slots[d_idx].add(s)
    return un_slots

# ==========================================
# 3. Optimized Scheduler Logic
# ==========================================
def calculate_schedule(files, mode, solver_time, penalty_val):
    logs = []
    SLOT_MAP = get_slot_map()
    DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
    SLOT_TO_INDEX = {v['time']: k for k, v in SLOT_MAP.items()}
    TOTAL_SLOTS = len(SLOT_MAP)

    try:
        # Load & Clean
        df_room = pd.read_csv(files['room'])
        room_list = df_room.to_dict('records')
        room_list.append({'room': 'Online', 'capacity': 9999, 'type': 'virtual'})
        
        df_tc = pd.read_csv(files['teacher_courses'])
        df_courses = pd.concat([pd.read_csv(files['ai_in']), pd.read_csv(files['cy_in'])], ignore_index=True).fillna(0)
        df_teacher = pd.read_csv(files['teachers'])
        
        t_map = defaultdict(list)
        for _, r in df_tc.iterrows(): t_map[str(r['course_code']).strip()].append(str(r['teacher_id']).strip())
        
        un_map = {str(r['teacher_id']).strip(): parse_unavailable_time(r.get('unavailable_times'), DAYS, SLOT_TO_INDEX) for _, r in df_teacher.iterrows()}

        # 1. Fixed Schedule
        fixed_tasks = []
        occupied_fixed = defaultdict(lambda: defaultdict(set))
        for key in ['ai_out', 'cy_out']:
            if files[key]:
                df_f = pd.read_csv(files[key])
                for _, r in df_f.iterrows():
                    d_idx = DAYS.index(r['day'][:3]) if r['day'][:3] in DAYS else -1
                    s_idx = SLOT_TO_INDEX.get(str(r['start']).replace('.', ':'), -1)
                    dur = int(math.ceil((r.get('lecture_hour', 0) + r.get('lab_hour', 0)) * 2))
                    if d_idx != -1 and s_idx != -1:
                        fixed_tasks.append({'id': str(r['course_code']), 'sec': int(r['section']), 'type': 'Fixed', 'room': str(r['room']), 'd': d_idx, 's': s_idx, 'dur': dur})
                        for i in range(dur): occupied_fixed[str(r['room'])][d_idx].add(s_idx + i)

        # 2. New Tasks
        tasks = []
        for _, r in df_courses.iterrows():
            c, s = str(r['course_code']).strip(), int(r['section'])
            t_list = t_map.get(c, ['Unknown'])
            opt = r.get('optional', 1)
            # Lec Split
            curr_lec = int(math.ceil(r['lecture_hour'] * 2))
            p = 1
            while curr_lec > 0:
                dur = min(curr_lec, 6)
                tasks.append({'uid': f"{c}_S{s}_Lec_P{p}", 'id': c, 'sec': s, 'type': 'Lec', 'dur': dur, 'std': r['enrollment_count'], 'tea': t_list, 'opt': opt, 'online': r.get('lec_online')==1})
                curr_lec -= dur; p += 1
            # Lab
            lab_dur = int(math.ceil(r['lab_hour'] * 2))
            if lab_dur > 0:
                tasks.append({'uid': f"{c}_S{s}_Lab", 'id': c, 'sec': s, 'type': 'Lab', 'dur': lab_dur, 'std': r['enrollment_count'], 'tea': t_list, 'opt': opt, 'online': r.get('lab_online')==1, 'req_ai': r.get('require_lab_ai')==1, 'req_net': r.get('require_lab_network')==1})

        # 3. Solver
        model = cp_model.CpModel()
        vars, is_sched, task_vars = {}, {}, {}
        room_lookup = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        tea_lookup = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        obj_terms, pen_terms, seq_penalties = [], [], []

        for t in tasks:
            uid = t['uid']
            is_sched[uid] = model.NewBoolVar(f"sc_{uid}")
            t_d = model.NewIntVar(0, 4, f"d_{uid}")
            t_s = model.NewIntVar(0, TOTAL_SLOTS-1, f"s_{uid}")
            t_e = model.NewIntVar(0, TOTAL_SLOTS, f"e_{uid}")
            model.Add(t_e == t_s + t['dur'])
            task_vars[uid] = {'d': t_d, 's': t_s, 'e': t_e}

            cands = []
            for r in room_list:
                if t['online'] and r['room'] != 'Online': continue
                if not t['online'] and (r['room'] == 'Online' or r['capacity'] < t['std']): continue
                if t['type'] == 'Lab' and 'lab' not in str(r.get('type','')).lower(): continue
                if t.get('req_ai') and r['room'] != 'lab_ai': continue
                if t.get('req_net') and r['room'] != 'lab_network': continue

                for d in range(5):
                    forbidden = occupied_fixed[r['room']][d]
                    for s in range(TOTAL_SLOTS - t['dur'] + 1):
                        s_v, e_v = SLOT_MAP[s]['val'], SLOT_MAP[s]['val'] + (t['dur']*0.5)
                        if mode == 1 and (s_v < 9.0 or e_v > 16.0): continue
                        if any(SLOT_MAP[s+i]['is_lunch'] for i in range(t['dur'])): continue
                        if any((s+i) in forbidden for i in range(t['dur'])): continue
                        # Teacher check
                        if any(tid in un_map and not set(range(s, s+t['dur'])).isdisjoint(un_map[tid][d]) for tid in t['tea']): continue

                        v = model.NewBoolVar(f"{uid}_{r['room']}_{d}_{s}")
                        cands.append(v); vars[(uid, r['room'], d, s)] = v
                        model.Add(t_d == d).OnlyEnforceIf(v)
                        model.Add(t_s == s).OnlyEnforceIf(v)
                        if mode == 2 and (s_v < 9.0 or e_v > 16.0): pen_terms.append(v * penalty_val)
                        # Fast Constraint Indexing
                        for i in range(t['dur']):
                            room_lookup[r['room']][d][s+i].append(v)
                            for tid in t['tea']: tea_lookup[tid][d][s+i].append(v)

            if cands: model.Add(sum(cands) == 1).OnlyEnforceIf(is_sched[uid])
            else: model.Add(is_scheduled[uid] == 0)
            obj_terms.append(is_sched[uid] * (1000 if t['opt']==0 else 100))

        # Constraints: No Overlap
        for lookup in [room_lookup, tea_lookup]:
            for key in lookup:
                for d in lookup[key]:
                    for s in lookup[key][d]:
                        if len(lookup[key][d][s]) > 1: model.Add(sum(lookup[key][d][s]) <= 1)

        model.Maximize(sum(obj_terms) - sum(pen_terms))
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = solver_time
        solver.parameters.num_search_workers = 8
        status = solver.Solve(model)

        # 4. Results
        res = []
        unsched = []
        cols = ['Day', 'Start', 'End', 'Room', 'Course', 'Sec', 'Type', 'Teacher', 'Note']
        
        if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            for f in fixed_tasks:
                res.append({'Day': DAYS[f['d']], 'Start': SLOT_MAP[f['s']]['time'], 'End': SLOT_MAP.get(f['s']+f['dur'],{'time':'19:00'})['time'], 'Room': f['room'], 'Course': f['id'], 'Sec': f['sec'], 'Type': 'Fixed', 'Teacher': '-', 'Note': 'Fixed'})
            for t in tasks:
                uid = t['uid']
                if solver.Value(is_sched[uid]):
                    d, s = solver.Value(task_vars[uid]['d']), solver.Value(task_vars[uid]['s'])
                    rm = next(k[1] for k, v in vars.items() if k[0] == uid and k[2] == d and k[3] == s and solver.Value(v))
                    notes = ([ "Online" ] if t['online'] else []) + ([ "Ext.Time" ] if SLOT_MAP[s]['val'] < 9.0 or (SLOT_MAP[s]['val'] + t['dur']*0.5) > 16.0 else [])
                    res.append({'Day': DAYS[d], 'Start': SLOT_MAP[s]['time'], 'End': SLOT_MAP.get(s+t['dur'],{'time':'19:00'})['time'], 'Room': rm, 'Course': t['id'], 'Sec': t['sec'], 'Type': t['type'], 'Teacher': ",".join(t['tea']), 'Note': ", ".join(notes)})
                else: unsched.append({'Course': t['id'], 'Sec': t['sec'], 'Type': t['type'], 'Reason': 'Constraints'})
            df_res = pd.DataFrame(res)
            df_res['DayIdx'] = df_res['Day'].apply(lambda x: DAYS.index(x))
            return df_res.sort_values(['DayIdx', 'Start']).drop(columns='DayIdx'), pd.DataFrame(unsched), logs
        return pd.DataFrame(columns=cols), pd.DataFrame(unsched), logs + ["No Solution Found"]
    except Exception as e: return None, None, [f"Error: {str(e)}"]

# ==========================================
# 4. Streamlit UI
# ==========================================
st.sidebar.header("📂 1. Upload Data")
up_files = {k: st.sidebar.file_uploader(f"{k}.csv", type="csv") for k in ['room', 'teacher_courses', 'ai_in', 'cy_in', 'teachers']}
st.sidebar.divider()
up_files['ai_out'] = st.sidebar.file_uploader("ai_out_courses.csv (Optional)", type="csv")
up_files['cy_out'] = st.sidebar.file_uploader("cy_out_courses.csv (Optional)", type="csv")

st.sidebar.divider()
st.sidebar.header("⚙️ 2. Configuration")
mode_sel = st.sidebar.radio("Mode:", [1, 2], format_func=lambda x: "Compact (09-16)" if x==1 else "Flexible (08:30-19:00)")
solver_t = st.sidebar.slider("Solver Time (Sec):", 10, 600, 120)
penalty_v = st.sidebar.slider("Penalty (Ext. Time):", 0, 100, 10)

if st.button("🚀 Run Automatic Scheduler", use_container_width=True):
    if any(up_files[k] is None for k in ['room', 'teacher_courses', 'ai_in', 'cy_in', 'teachers']):
        st.error("Please upload all required files.")
    else:
        with st.status("Solving (Optimized Engine)...", expanded=True) as status:
            df_r, df_u, l = calculate_schedule(up_files, mode_sel, solver_t, penalty_v)
            if df_r is not None and not df_r.empty:
                st.session_state['res'], st.session_state['un'] = df_r, df_u
                status.update(label="✅ Schedule Found!", state="complete")
            else: st.error("Failed to find schedule. Try reducing Penalty or increasing Time.")

if 'res' in st.session_state:
    df = st.session_state['res']
    view = st.radio("View Mode:", ["รายห้อง (Room View)", "รายอาจารย์ (Teacher View)"], horizontal=True)
    
    if view == "รายห้อง (Room View)":
        sel = st.selectbox("Select Room:", sorted(df['Room'].unique()))
        filt = df[df['Room'] == sel]
    else:
        all_t = sorted(list(set([i.strip() for s in df['Teacher'] for i in str(s).split(',') if i != '-'])))
        sel = st.selectbox("Select Teacher:", all_t)
        filt = df[df['Teacher'].str.contains(sel, na=False)]

    # HTML Table Generator
    days_map = {'Mon': 'จันทร์', 'Tue': 'อังคาร', 'Wed': 'พุธ', 'Thu': 'พฤหัสบดี', 'Fri': 'ศุกร์'}
    time_cols = [f"{h:02d}:{m:02d}" for h in range(8, 19) for m in [0, 30]][1:]
    html = f"<div class='tt-container'><table class='tt-table'><tr class='tt-header'><th>Day</th>"
    for t in time_cols: html += f"<th>{t}</th>"
    html += "</tr>"
    for day in ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']:
        html += f"<tr><td class='tt-day'>{days_map[day]}</td>"
        d_data = filt[filt['Day'] == day]
        curr = 8.5
        while curr < 19.0:
            t_str = f"{int(curr):02d}:{int((curr%1)*60):02d}"
            match = d_data[d_data['Start'] == t_str]
            if not match.empty:
                row = match.iloc[0]
                sh, sm = map(int, row['Start'].split(':'))
                eh, em = map(int, row['End'].split(':'))
                span = int(((eh + em/60) - (sh + sm/60)) * 2)
                html += f"<td colspan='{span}'><div class='class-box'><span class='c-code'>{row['Course']}</span><span>(S{row['Sec']}) {row['Type']}</span><span>{row.get('Teacher','-')}</span>"
                if row.get('Note'): html += f"<span style='color:red; font-size:9px'>{row['Note']}</span>"
                html += "</div></td>"
                curr += (span * 0.5)
            else: html += "<td></td>"; curr += 0.5
    st.markdown(html + "</table></div>", unsafe_allow_html=True)
    if not st.session_state['un'].empty:
        st.warning(f"Unscheduled: {len(st.session_state['un'])} items"); st.dataframe(st.session_state['un'])
