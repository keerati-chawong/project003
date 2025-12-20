import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import math
import re
from collections import defaultdict

# ตั้งค่าหน้าเว็บ
st.set_page_config(page_title="Automatic Scheduler Pro", layout="wide")
st.title("🎓 Automatic Course Scheduler")

# ==========================================
# 1. User Config & CSS (แก้ไขสีหัวตารางและกล่องวิชา)
# ==========================================
st.markdown("""
<style>
    .tt-container { overflow-x: auto; font-family: 'Helvetica', sans-serif; margin-top: 20px; }
    .tt-table { width: 100%; border-collapse: collapse; min-width: 1200px; }
    /* แก้ไขหัวตารางให้เป็นสีเข้ม ฟอนต์ขาวชัดเจน */
    .tt-table th { 
        background-color: #343a40 !important; 
        color: #ffffff !important; 
        border: 1px solid #444; 
        text-align: center; 
        padding: 8px; 
    }
    .tt-table td { border: 1px solid #dee2e6; text-align: center; padding: 4px; height: 75px; }
    .tt-day { background-color: #f8f9fa !important; color: #333 !important; font-weight: bold; width: 80px; position: sticky; left: 0; z-index: 10; border-right: 2px solid #ccc; }
    .class-box { 
        background-color: #e7f1ff; border: 1px solid #b6d4fe; border-radius: 6px;
        padding: 4px; height: 95%; display: flex; flex-direction: column; justify-content: center;
        color: #084298 !important; box-shadow: 1px 1px 3px rgba(0,0,0,0.1); font-size: 10px; line-height: 1.2;
    }
    .c-code { font-weight: bold; text-decoration: underline; font-size: 12px; color: #004085 !important; }
</style>
""", unsafe_allow_html=True)

schedule_mode_desc = {1: "Compact Mode (09:00 - 16:00)", 2: "Flexible Mode (08:30 - 19:00)"}
SCHEDULE_MODE = st.radio("Select Scheduling Mode:", options=[1, 2], format_func=lambda x: schedule_mode_desc[x])

# ==========================================
# 2. Helper Functions
# ==========================================
def get_slot_map():
    slots = {}
    t_start = 8.5
    idx = 0
    while t_start <= 19.0: # เพิ่มเป็น <= เพื่อให้มีช่องเวลา 19:00 สำหรับ End time
        h, m = int(t_start), int((t_start - int(t_start)) * 60)
        slots[idx] = {'time': f"{h:02d}:{m:02d}", 'val': t_start, 'is_lunch': (12.5 <= t_start < 13.0)}
        idx += 1; t_start += 0.5
    return slots

def parse_unavailable_time(input_val, days_list, slot_inv):
    un_slots = {d: set() for d in range(len(days_list))}
    if pd.isna(input_val) or not input_val: return un_slots
    items = input_val if isinstance(input_val, list) else [str(input_val)]
    for item in items:
        match = re.search(r"(\w{3})\s+(\d{1,2}[:.]\d{2})-(\d{1,2}[:.]\d{2})", str(item))
        if match:
            day_abbr, start_t, end_t = match.groups()
            if day_abbr in days_list:
                d_idx = days_list.index(day_abbr)
                s_i = slot_inv.get(start_t.replace('.', ':'), -1)
                e_i = slot_inv.get(end_t.replace('.', ':'), -1)
                if s_i != -1 and e_i != -1:
                    for s in range(s_i, e_i): un_slots[d_idx].add(s)
    return un_slots

# ==========================================
# 3. Main Solver Logic (Optimized)
# ==========================================
def calculate_schedule():
    SLOT_MAP = get_slot_map()
    DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
    SLOT_INV = {v['time']: k for k, v in SLOT_MAP.items()}
    TOTAL_SLOTS = len(SLOT_MAP)

    try:
        # Load Data (ใช้ Path ตามโครงสร้างที่คุณให้มา)
        path = 'Web_schedule-main/Web_schedule-main/'
        df_room = pd.read_csv(f'{path}room.csv')
        df_tc = pd.read_csv(f'{path}teacher_courses.csv')
        df_ai_in = pd.read_csv(f'{path}ai_in_courses.csv')
        df_cy_in = pd.read_csv(f'{path}cy_in_courses.csv')
        df_teacher = pd.read_csv(f'{path}all_teachers.csv')
        df_ai_out = pd.read_csv(f'{path}ai_out_courses.csv')
        df_cy_out = pd.read_csv(f'{path}cy_out_courses.csv')

        room_list = df_room.to_dict('records')
        room_list.append({'room': 'Online', 'capacity': 9999, 'type': 'virtual'})
        
        teacher_map = defaultdict(list)
        for _, r in df_tc.iterrows():
            teacher_map[str(r['course_code']).strip()].append(str(r['teacher_id']).strip())

        # แก้ไขจุดที่เคย overwrite unavailable_times เป็น None
        un_map = {str(r['teacher_id']).strip(): parse_unavailable_time(r.get('unavailable_times'), DAYS, SLOT_INV) for _, r in df_teacher.iterrows()}

        # 1. เตรียม Fixed Tasks (ลดภาระ Solver)
        fixed_tasks = []
        for df_f in [df_ai_out, df_cy_out]:
            for _, r in df_f.iterrows():
                d_idx = DAYS.index(r['day'][:3]) if r['day'][:3] in DAYS else -1
                s_idx = SLOT_INV.get(str(r['start']).replace('.', ':'), -1)
                dur = int(math.ceil((r.get('lecture_hour', 0) + r.get('lab_hour', 0)) * 2))
                if d_idx != -1 and s_idx != -1:
                    fixed_tasks.append({
                        'uid': f"FIX_{r['course_code']}_{r['section']}", 'id': str(r['course_code']), 
                        'sec': int(r['section']), 'dur': dur, 'fixed_room': True, 'target_room': str(r['room']),
                        'f_d': d_idx, 'f_s': s_idx, 'tea': teacher_map.get(str(r['course_code']).strip(), ['-']), 'opt': 0
                    })

        # 2. เตรียม Dynamic Tasks
        df_courses = pd.concat([df_ai_in, df_cy_in], ignore_index=True).fillna(0)
        tasks = []
        for _, r in df_courses.iterrows():
            c, s = str(r['course_code']).strip(), int(r['section'])
            tea = teacher_map.get(c, ['Unknown'])
            lec_slots = int(math.ceil(r['lecture_hour'] * 2))
            if lec_slots > 0:
                tasks.append({'uid': f"{c}_S{s}_Lec", 'id': c, 'sec': s, 'type': 'Lec', 'dur': lec_slots, 'std': r['enrollment_count'], 'tea': tea, 'opt': r['optional'], 'online': r.get('lec_online')==1})
            lab_slots = int(math.ceil(r['lab_hour'] * 2))
            if lab_slots > 0:
                tasks.append({'uid': f"{c}_S{s}_Lab", 'id': c, 'sec': s, 'type': 'Lab', 'dur': lab_slots, 'std': r['enrollment_count'], 'tea': tea, 'opt': r['optional'], 'online': r.get('lab_online')==1, 'req_ai': r.get('require_lab_ai')==1})

        # --- Solver Setup ---
        model = cp_model.CpModel()
        vars, is_sched, task_vars = {}, {}, {}
        room_lookup = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        tea_lookup = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        obj_terms, pen_terms = [], []

        for t in (fixed_tasks + tasks):
            uid = t['uid']
            is_sched[uid] = model.NewBoolVar(f"sc_{uid}")
            t_d = model.NewIntVar(0, 4, f"d_{uid}")
            t_s = model.NewIntVar(0, TOTAL_SLOTS-1, f"s_{uid}")
            model.Add(model.NewIntVar(0, TOTAL_SLOTS, f"e_{uid}") == t_s + t['dur'])
            task_vars[uid] = {'d': t_d, 's': t_s}
            
            if t.get('fixed_room'):
                model.Add(t_d == t['f_d']); model.Add(t_s == t['f_s'])

            cands = []
            for r in room_list:
                if t.get('online') and r['room'] != 'Online': continue
                if not t.get('online') and (r['room'] == 'Online' or r['capacity'] < t.get('std', 0)): continue
                if t.get('fixed_room') and r['room'] != t['target_room']: continue
                if t.get('req_ai') and r['room'] != 'lab_ai': continue

                for di in range(5):
                    for si in range(TOTAL_SLOTS - t['dur']):
                        sv = SLOT_MAP[si]['val']
                        if SCHEDULE_MODE == 1 and (sv < 9.0 or (sv + t['dur']*0.5) > 16.0): continue
                        if any(SLOT_MAP[si+k]['is_lunch'] for k in range(t['dur'])): continue
                        if any(tid in un_map and si+k in un_map[tid][di] for tid in t.get('tea', []) for k in range(t['dur'])): continue

                        v = model.NewBoolVar(f"{uid}_{r['room']}_{di}_{si}")
                        cands.append(v); vars[(uid, r['room'], di, si)] = v
                        model.Add(t_d == di).OnlyEnforceIf(v); model.Add(t_s == si).OnlyEnforceIf(v)
                        if SCHEDULE_MODE == 2 and (sv < 9.0 or (sv + t['dur']*0.5) > 16.0): pen_terms.append(v)
                        
                        for k in range(t['dur']):
                            room_lookup[r['room']][di][si+k].append(v)
                            for tid in t.get('tea', []): tea_lookup[tid][di][si+k].append(v)

            if cands: model.Add(sum(cands) == 1).OnlyEnforceIf(is_sched[uid])
            else: model.Add(is_sched[uid] == 0)
            obj_terms.append(is_sched[uid] * (1000000 if t.get('fixed_room') else (1000 if t.get('opt')==0 else 100)))

        # Constraints: No Overlap (Lookup based - Very Fast)
        for lookup in [room_lookup, tea_lookup]:
            for k in lookup:
                for d in lookup[k]:
                    for s in lookup[k][d]:
                        if len(lookup[k][d][s]) > 1: model.Add(sum(lookup[k][d][s]) <= 1)

        model.Maximize(sum(obj_terms) - sum(pen_terms) * 10)
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 120
        status = solver.Solve(model)

        res_final = []
        if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            for t in (fixed_tasks + tasks):
                if solver.Value(is_sched[t['uid']]):
                    d, s = solver.Value(task_vars[t['uid']]['d']), solver.Value(task_vars[t['uid']]['s'])
                    rm = next((k[1] for k, v in vars.items() if k[0] == t['uid'] and k[2] == d and k[3] == s and solver.Value(v)), "Unknown")
                    res_final.append({
                        'Day': DAYS[d], 'Start': SLOT_MAP[s]['time'], 'End': SLOT_MAP[s+t['dur']]['time'],
                        'Room': rm, 'Course': t['id'], 'Sec': t['sec'], 'Type': t.get('type','-'), 
                        'Teacher': ",".join(t.get('tea', [])), 'Note': "Ext.Time" if SLOT_MAP[s]['val'] < 9.0 or SLOT_MAP[s+t['dur']-1]['val'] >= 16.0 else ""
                    })
            return res_final, []
        return None, None
    except Exception as e:
        st.error(f"Solver Error: {e}"); return None, None

# ==========================================
# 4. Controller & Display
# ==========================================
if st.button("🚀 Run Automatic Scheduler", use_container_width=True):
    with st.spinner("🤖 AI is calculating the best schedule..."):
        res, uns = calculate_schedule()
        if res:
            st.session_state['res'], st.session_state['has_run'] = pd.DataFrame(res), True
            st.success("✅ Schedule Found!")
        else: st.error("❌ Failed to find schedule. Try Flexible Mode or check constraints.")

if st.session_state.get('has_run'):
    df_res = st.session_state['res']
    st.divider()
    room = st.selectbox("🔍 Select Room:", sorted(df_res['Room'].unique()))
    filtered = df_res[df_res['Room'] == room]

    # --- HTML Table Generator (Blue Box style) ---
    days_th = {'Mon': 'จันทร์', 'Tue': 'อังคาร', 'Wed': 'พุธ', 'Thu': 'พฤหัสบดี', 'Fri': 'ศุกร์'}
    time_headers = [f"{h:02d}:{m:02d}" for h in range(8, 19) for m in [0, 30]][1:]
    
    html = f"<div class='tt-container'><table class='tt-table'><tr class='tt-header'><th>Day</th>"
    for t in time_headers: html += f"<th>{t}</th>"
    html += "</tr>"
    for day in ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']:
        html += f"<tr><td class='tt-day'>{days_th[day]}</td>"
        d_data = filtered[filtered['Day'] == day]
        curr = 8.5
        while curr < 19.0:
            t_str = f"{int(curr):02d}:{int(round((curr%1)*60)):02d}"
            match = d_data[d_data['Start'] == t_str]
            if not match.empty:
                r = match.iloc[0]
                sh, sm = map(int, r['Start'].split(':')); eh, em = map(int, r['End'].split(':'))
                span = int(((eh + em/60) - (sh + sm/60)) * 2)
                html += f"<td colspan='{span}'><div class='class-box'><span class='c-code'>{r['Course']}</span><span>(S{r['Sec']}) {r['Type']}</span><span>{r['Teacher']}</span>"
                if r['Note']: html += f"<span style='color:red; font-size:9px'>{r['Note']}</span>"
                html += "</div></td>"; curr += (span * 0.5)
            else: html += "<td></td>"; curr += 0.5
    st.markdown(html + "</table></div>", unsafe_allow_html=True)
    st.download_button("📥 Download CSV", df_res.to_csv(index=False).encode('utf-8'), "schedule.csv", "text/csv")
