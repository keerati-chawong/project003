import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import math
import re

# ==========================================
# 1. UI & CSS Styling (ตารางรูปแบบ Blue Box)
# ==========================================
st.set_page_config(page_title="Course Scheduler Pro", layout="wide")

st.markdown("""
<style>
    .tt-container { overflow-x: auto; margin-top: 20px; }
    .tt-table { width: 100%; border-collapse: collapse; table-layout: fixed; min-width: 1200px; background-color: #1e1e1e; color: white; }
    .tt-table th, .tt-table td { border: 1px solid #444; text-align: center; padding: 2px; height: 80px; }
    .tt-header { background-color: #333; font-weight: bold; font-size: 12px; }
    .tt-day { background-color: #252525; font-weight: bold; width: 80px; font-size: 14px; }
    .class-box { 
        background-color: #e7f1ff; border-radius: 6px; padding: 6px; height: 90%; 
        display: flex; flex-direction: column; justify-content: center; align-items: center;
        color: #084298; border: 2px solid #b6d4fe; box-shadow: 0 2px 4px rgba(0,0,0,0.2);
    }
    .c-code { font-weight: bold; font-size: 14px; text-decoration: underline; margin-bottom: 2px; }
    .c-info { font-size: 11px; font-weight: 500; }
    .c-teacher { font-size: 11px; margin-top: 2px; color: #052c65; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. ฟังก์ชันช่วยประมวลผลเวลา
# ==========================================
def get_slot_map():
    slots = {}
    t, idx = 8.5, 0
    while t < 19.0:
        h, m = int(t), int((t % 1) * 60)
        slots[idx] = {'time': f"{h:02d}:{m:02d}", 'val': t, 'is_lunch': (12.5 <= t < 13.0)}
        t += 0.5; idx += 1
    return slots

def time_to_val(t_str):
    t_str = str(t_str).replace('.', ':').strip()
    if ':' in t_str:
        h, m = map(int, t_str.split(':'))
        return h + (0.5 if m >= 30 else 0)
    return float(t_str)

# ==========================================
# 3. Sidebar: Configuration
# ==========================================
st.sidebar.header("📁 Data Management")
up_r = st.sidebar.file_uploader("Upload room.csv", type="csv")
up_tc = st.sidebar.file_uploader("Upload teacher_courses.csv", type="csv")
up_ai_in = st.sidebar.file_uploader("Upload ai_in_courses.csv", type="csv")
up_cy_in = st.sidebar.file_uploader("Upload cy_in_courses.csv", type="csv")
up_ai_out = st.sidebar.file_uploader("Upload ai_out_courses.csv", type="csv")
up_cy_out = st.sidebar.file_uploader("Upload cy_out_courses.csv", type="csv")

st.sidebar.divider()
SOLVER_TIME = st.sidebar.slider("Solver Time (Sec)", 10, 300, 120)
PENALTY_EXT = st.sidebar.slider("Penalty: Avoid Ext. Time", 0, 100, 50)
SCH_MODE = st.radio("Scheduling Mode:", [1, 2], index=1, horizontal=True, 
                    format_func=lambda x: "Compact (09-16)" if x==1 else "Flexible (08:30-19)")

# ==========================================
# 4. Solver Core Logic
# ==========================================
def run_solver():
    slots = get_slot_map()
    days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
    
    # Load Data
    try:
        df_courses = pd.concat([pd.read_csv(up_ai_in), pd.read_csv(up_cy_in)], ignore_index=True).fillna(0)
        room_list = pd.read_csv(up_r).to_dict('records')
        room_list.append({'room': 'Online', 'capacity': 9999})
        t_map = pd.read_csv(up_tc).groupby('course_code')['teacher_id'].apply(lambda x: ",".join(x.astype(str))).to_dict()
        
        # ตรวจสอบวิชาที่ล็อกไว้ (Fixed)
        occupied = []
        for f in [up_ai_out, up_cy_out]:
            if f:
                df_f = pd.read_csv(f)
                for _, row in df_f.iterrows():
                    d_idx = days.index(row['day'][:3]) if str(row['day'])[:3] in days else -1
                    s_val = time_to_val(row['start'])
                    dur = int(math.ceil((row['lecture_hour'] + row['lab_hour']) * 2))
                    s_idx = next((k for k, v in slots.items() if v['val'] == s_val), -1)
                    if d_idx != -1 and s_idx != -1:
                        for i in range(dur): occupied.append((str(row['room']), d_idx, s_idx + i))
    except Exception as e: return None, None, f"Error: {e}"

    model = cp_model.CpModel()
    vars, is_sched, penalties = {}, {}, []
    tasks = []

    for _, row in df_courses.iterrows():
        c, sec = str(row['course_code']), int(row['section'])
        for ty in ['Lec', 'Lab']:
            hr = row.get(f'{ty.lower()}_hour', 0)
            if hr > 0:
                tasks.append({
                    'uid': f"{c}_S{sec}_{ty}", 'id': c, 'sec': sec, 'type': ty, 
                    'dur': int(math.ceil(hr*2)), 'std': row['enrollment_count'], 
                    'teachers': t_map.get(c, 'Unknown'), 
                    'is_on': row.get(f'{ty.lower()}_online', 0) == 1
                })

    # Constraints & Variables
    for t in tasks:
        uid = t['uid']
        is_sched[uid] = model.NewBoolVar(f"s_{uid}")
        cands = []
        for r in room_list:
            if (t['is_on'] and r['room'] != 'Online') or (not t['is_on'] and r['room'] == 'Online'): continue
            if r['capacity'] < t['std']: continue
            for d in range(5):
                for s in range(len(slots) - t['dur'] + 1):
                    s_v, e_v = slots[s]['val'], slots[s]['val'] + (t['dur']*0.5)
                    if any(slots[s+i]['is_lunch'] for i in range(t['dur'])): continue
                    if SCH_MODE == 1 and (s_v < 9.0 or e_v > 16.0): continue
                    if any((r['room'], d, s+i) in occupied for i in range(t['dur'])): continue

                    v = model.NewBoolVar(f"{uid}_{r['room']}_{d}_{s}")
                    vars[(uid, r['room'], d, s)] = v
                    cands.append(v)
                    if s_v < 9.0 or e_v > 16.0: penalties.append(v * PENALTY_EXT)
        
        if cands: model.Add(sum(cands) == 1).OnlyEnforceIf(is_sched[uid])
        else: model.Add(is_sched[uid] == 0)

    # Overlap Checks
    for d in range(5):
        for s in range(len(slots)):
            for r in [rm['room'] for rm in room_list if rm['room'] != 'Online']:
                usage = [vars[k] for k in vars if k[1] == r and k[2] == d and k[3] <= s < k[3] + next(x['dur'] for x in tasks if x['uid'] == k[0])]
                if usage: model.Add(sum(usage) <= 1)

    model.Maximize(sum(is_sched.values()) * 1000 - sum(penalties))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = SOLVER_TIME
    status = solver.Solve(model)

    res, unres = [], []
    for t in tasks:
        found = False
        if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            for k, v in vars.items():
                if k[0] == t['uid'] and solver.Value(v):
                    found = True
                    res.append({
                        'Day': days[k[2]], 'Start': slots[k[3]]['time'], 
                        'End': slots.get(k[3]+t['dur'], {'time':'19:00'})['time'], 
                        'Room': k[1], 'Course': t['id'], 'Sec': t['sec'], 
                        'Type': t['type'], 'Teacher': t['teachers']
                    })
        if not found: unres.append(t)

    return pd.DataFrame(res), pd.DataFrame(unres), "Success"

# ==========================================
# 5. Display & Table Rendering
# ==========================================
if st.button("🚀 Run Scheduler", use_container_width=True):
    with st.spinner("กำลังคำนวณตารางเรียน..."):
        df_res, df_un, msg = run_solver()
        if df_res is not None:
            st.session_state['res'], st.session_state['un'] = df_res, df_un
            st.success("จัดตารางสำเร็จ!")

if 'res' in st.session_state:
    res_df = st.session_state['res']
    
    # ตัวเลือกฟิลเตอร์
    target_room = st.selectbox("เลือกห้องที่ต้องการดู:", sorted(res_df['Room'].unique()))
    filtered = res_df[res_df['Room'] == target_room]

    # Render HTML Table
    days_map = {'Mon': 'จันทร์', 'Tue': 'อังคาร', 'Wed': 'พุธ', 'Thu': 'พฤหัสบดี', 'Fri': 'ศุกร์'}
    time_headers = [f"{h:02d}:{m:02d}" for h in range(8, 19) for m in [0, 30]][1:-1]
    
    html = f"<div class='tt-container'><table class='tt-table'><tr class='tt-header'><th>Day/Time</th>"
    for th in time_headers: html += f"<th>{th}</th>"
    html += "</tr>"

    for d_en in ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']:
        html += f"<tr><td class='tt-day'>{days_map[d_en]}</td>"
        d_df = filtered[filtered['Day'] == d_en]
        curr = 8.5
        while curr < 18.5:
            c_str = f"{int(curr):02d}:{'30' if curr%1!=0 else '00'}"
            match = d_df[d_df['Start'] == c_str]
            if not match.empty:
                r = match.iloc[0]
                s_val = time_to_val(r['Start'])
                e_val = time_to_val(r['End'])
                span = int((e_val - s_val) * 2)
                html += f"<td colspan='{span}'><div class='class-box'><div class='c-code'>{r['Course']}</div><div class='c-info'>(S{r['Sec']}) {r['Type']}</div><div class='c-teacher'>{r['Teacher']}</div></div></td>"
                curr += (span * 0.5)
            else:
                html += "<td></td>"; curr += 0.5
        html += "</tr>"
    
    st.markdown(html + "</table></div>", unsafe_allow_html=True)
