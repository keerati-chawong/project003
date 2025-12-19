import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import math
import re
from collections import defaultdict

# ==========================================
# 1. Page Config & CSS (แก้ไขเรื่องสีที่เคยมองไม่เห็น)
# ==========================================
st.set_page_config(page_title="Automatic Scheduler Pro", layout="wide")

st.markdown("""
<style>
    .tt-container { overflow-x: auto; font-family: 'Helvetica', sans-serif; margin-top: 20px; }
    .tt-table { width: 100%; border-collapse: collapse; min-width: 1200px; }
    
    /* แก้ไขหัวตาราง: พื้นหลังมืด ฟอนต์ขาวชัดเจน */
    .tt-table th { 
        border: 1px solid #444; 
        text-align: center; 
        padding: 8px; 
        font-size: 14px; 
        background-color: #343a40 !important; 
        color: #ffffff !important; 
    }
    
    .tt-table td { border: 1px solid #dee2e6; text-align: center; padding: 4px; height: 75px; }

    /* แก้ไขคอลัมน์ Day: พื้นหลังเทาอ่อน ฟอนต์ดำเข้มชัดเจน */
    .tt-day { 
        background-color: #f8f9fa !important; 
        color: #333333 !important; 
        font-weight: bold !important; 
        width: 80px; 
        position: sticky; 
        left: 0; 
        z-index: 10; 
        border-right: 2px solid #ccc !important; 
        font-size: 14px !important;
    }

    .class-box { 
        background-color: #e7f1ff; border: 1px solid #b6d4fe; border-radius: 6px;
        padding: 4px; height: 95%; display: flex; flex-direction: column; justify-content: center;
        color: #084298 !important; box-shadow: 1px 1px 3px rgba(0,0,0,0.1); font-size: 10px; line-height: 1.2;
    }
    .c-code { font-weight: bold; text-decoration: underline; font-size: 12px; color: #004085 !important; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. Helper Functions
# ==========================================
def get_slot_map():
    slots = {}
    t_start = 8.5
    idx = 0
    while t_start <= 19.0:
        h, m = int(t_start), int((t_start % 1) * 60)
        slots[idx] = {'time': f"{h:02d}:{m:02d}", 'val': t_start, 'is_lunch': (12.5 <= t_start < 13.0)}
        idx += 1; t_start += 0.5
    return slots

def time_to_slot_index(time_str, slot_inv):
    time_str = str(time_str).strip().replace('.', ':')
    match = re.search(r"(\d{1,2}):(\d{2})", time_str)
    if match:
        h, m = match.groups()
        formatted = f"{int(h):02d}:{int(m):02d}"
        return slot_inv.get(formatted, -1)
    return -1

# ==========================================
# 3. Main Scheduler Logic (Optimized Engine)
# ==========================================
def calculate_schedule(uploaded_files, mode, solver_time, penalty_score):
    logs = []
    SLOT_MAP = get_slot_map()
    DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
    SLOT_INV = {v['time']: k for k, v in SLOT_MAP.items()}
    TOTAL_SLOTS = len(SLOT_MAP)

    try:
        # Load Data from Streamlit Uploaders
        df_room = pd.read_csv(uploaded_files['room'])
        room_list = df_room.to_dict('records')
        room_list.append({'room': 'Online', 'capacity': 9999, 'type': 'virtual'})
        
        df_tc = pd.read_csv(uploaded_files['teacher_courses'])
        df_courses = pd.concat([pd.read_csv(uploaded_files['ai_in']), pd.read_csv(uploaded_files['cy_in'])], ignore_index=True).fillna(0)
        df_teacher = pd.read_csv(uploaded_files['all_teachers'])

        teacher_map = defaultdict(list)
        for _, row in df_tc.iterrows():
            teacher_map[str(row['course_code']).strip()].append(str(row['teacher_id']).strip())

        # Fixed Schedule Logic
        fixed_tasks = []
        occupied_fixed = defaultdict(lambda: defaultdict(set))
        for key in ['ai_out', 'cy_out']:
            if uploaded_files[key]:
                df_f = pd.read_csv(uploaded_files[key])
                for _, r in df_f.iterrows():
                    d_i = DAYS.index(str(r['day'])[:3]) if str(r['day'])[:3] in DAYS else -1
                    s_i = time_to_slot_index(r['start'], SLOT_INV)
                    dur = int(math.ceil((r.get('lecture_hour', 0) + r.get('lab_hour', 0)) * 2))
                    if d_i != -1 and s_i != -1:
                        fixed_tasks.append({
                            'uid': f"FIX_{r['course_code']}_S{r['section']}",
                            'id': str(r['course_code']), 'sec': int(r['section']), 'dur': dur,
                            'tea': teacher_map.get(str(r['course_code']).strip(), ['-']),
                            'fixed_room': True, 'target_room': str(r['room']), 'f_d': d_i, 'f_s': s_i
                        })
                        for i in range(dur): occupied_fixed[str(r['room'])][d_i].add(s_i + i)

        # Dynamic Tasks Prep
        tasks = []
        for _, r in df_courses.iterrows():
            c, s = str(r['course_code']).strip(), int(r['section'])
            tea = teacher_map.get(c, ['Unknown']); opt = r.get('optional', 1)
            # Lec Split
            curr_lec = int(math.ceil(r['lecture_hour'] * 2))
            p = 1
            while curr_lec > 0:
                dur = min(curr_lec, 6)
                uid = f"{c}_S{s}_Lec_P{p}"
                if not any(tk['uid'] == uid for tk in fixed_tasks):
                    tasks.append({'uid': uid, 'id': c, 'sec': s, 'type': 'Lec', 'dur': dur, 'std': r['enrollment_count'], 'tea': tea, 'opt': opt, 'online': r.get('lec_online')==1})
                curr_lec -= dur; p += 1
            # Lab
            lab_dur = int(math.ceil(r['lab_hour'] * 2))
            if lab_dur > 0:
                uid = f"{c}_S{s}_Lab"
                if not any(tk['uid'] == uid for tk in fixed_tasks):
                    tasks.append({'uid': uid, 'id': c, 'sec': s, 'type': 'Lab', 'dur': lab_dur, 'std': r['enrollment_count'], 'tea': tea, 'opt': opt, 'online': r.get('lab_online')==1})

        # OR-Tools Model
        model = cp_model.CpModel()
        is_sched, task_vars, vars = {}, {}, {}
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

            # บังคับวิชา Fixed ลงตามจุดเดิม
            if t.get('fixed_room'):
                model.Add(t_d == t['f_d'])
                model.Add(t_s == t['f_s'])

            cands = []
            for r in room_list:
                if t.get('online') and r['room'] != 'Online': continue
                if not t.get('online') and (r['room'] == 'Online' or r['capacity'] < t.get('std', 0)): continue
                if t.get('fixed_room') and r['room'] != t['target_room']: continue

                for d in range(5):
                    for s in range(TOTAL_SLOTS - t['dur']):
                        sv, ev = SLOT_MAP[s]['val'], SLOT_MAP[s]['val'] + (t['dur']*0.5)
                        if mode == 1 and (sv < 9.0 or ev > 16.0): continue
                        if any(SLOT_MAP[s+i]['is_lunch'] for i in range(t['dur'])): continue

                        v = model.NewBoolVar(f"{uid}_{r['room']}_{d}_{s}")
                        cands.append(v); vars[(uid, r['room'], d, s)] = v
                        model.Add(t_d == d).OnlyEnforceIf(v); model.Add(t_s == s).OnlyEnforceIf(v)
                        if mode == 2 and (sv < 9.0 or ev > 16.0): pen_terms.append(v * penalty_score)
                        for i in range(t['dur']):
                            room_lookup[r['room']][d][s+i].append(v)
                            for tid in t['tea']: tea_lookup[tid][d][s+i].append(v)

            if cands: model.Add(sum(cands) == 1).OnlyEnforceIf(is_sched[uid])
            else: model.Add(is_sched[uid] == 0)
            obj_terms.append(is_sched[uid] * (1000000 if t.get('fixed_room') else (1000 if t.get('opt')==0 else 100)))

        for lookup in [room_lookup, tea_lookup]:
            for k in lookup:
                for d in lookup[k]:
                    for s in lookup[k][d]:
                        if len(lookup[k][d][s]) > 1: model.Add(sum(lookup[k][d][s]) <= 1)

        model.Maximize(sum(obj_terms) - sum(pen_terms))
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = solver_time
        status = solver.Solve(model)

        res_final = []
        if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            for t in (fixed_tasks + tasks):
                if solver.Value(is_sched[t['uid']]):
                    d, s = solver.Value(task_vars[t['uid']]['d']), solver.Value(task_vars[t['uid']]['s'])
                    rm = next((k[1] for k, v in vars.items() if k[0] == t['uid'] and k[2] == d and k[3] == s and solver.Value(v)), "Unknown")
                    notes = (["Online"] if t.get('online') else []) + (["Ext.Time"] if SLOT_MAP[s]['val'] < 9.0 or (SLOT_MAP[s]['val'] + t['dur']*0.5) > 16.0 else [])
                    res_final.append({'Day': DAYS[d], 'Start': SLOT_MAP[s]['time'], 'End': SLOT_MAP[s+t['dur']]['time'], 'Room': rm, 'Course': t['id'], 'Sec': t['sec'], 'Type': t.get('type','-'), 'Teacher': ",".join(t['tea']), 'Note': ", ".join(notes)})
            return pd.DataFrame(res_final)
        return None
    except Exception as e: return None

# ==========================================
# 4. Streamlit UI (7 ไฟล์อัปโหลด)
# ==========================================
st.sidebar.header("📂 1. อัปโหลดข้อมูล (7 ไฟล์)")
up_files = {
    'room': st.sidebar.file_uploader("1. room.csv", type="csv"),
    'teacher_courses': st.sidebar.file_uploader("2. teacher_courses.csv", type="csv"),
    'ai_in': st.sidebar.file_uploader("3. ai_in_courses.csv", type="csv"),
    'cy_in': st.sidebar.file_uploader("4. cy_in_courses.csv", type="csv"),
    'all_teachers': st.sidebar.file_uploader("5. all_teachers.csv", type="csv"),
    'ai_out': st.sidebar.file_uploader("6. ai_out_courses.csv", type="csv"),
    'cy_out': st.sidebar.file_uploader("7. cy_out_courses.csv", type="csv"),
}

st.sidebar.divider()
st.sidebar.header("⚙️ 2. ตั้งค่า Solver")
mode_sel = st.sidebar.radio("โหมด:", [1, 2], format_func=lambda x: "Compact (09-16)" if x==1 else "Flexible (08:30-19)")
solver_t = st.sidebar.slider("เวลาคำนวณ (วินาที):", 10, 600, 120)
penalty_v = st.sidebar.slider("Penalty (Ext. Time):", 0, 100, 10)

if st.button("🚀 Run Automatic Scheduler", use_container_width=True):
    # บังคับอัปโหลด 5 ไฟล์แรก
    mandatory = ['room', 'teacher_courses', 'ai_in', 'cy_in', 'all_teachers']
    if any(up_files[k] is None for k in mandatory):
        st.error("❌ กรุณาอัปโหลดไฟล์หลักให้ครบถ้วนก่อนรัน")
    else:
        with st.status("🤖 กำลังจัดตารางที่ดีที่สุด...", expanded=True) as status:
            df_res = calculate_schedule(up_files, mode_sel, solver_t, penalty_v)
            if df_res is not None and not df_res.empty:
                st.session_state['res_df'] = df_res
                st.session_state['run_done'] = True
                status.update(label="✅ จัดตารางสำเร็จ!", state="complete")
            else: st.error("❌ ไม่สามารถจัดตารางได้ตามเงื่อนไข (ลองเพิ่มเวลาหรือลด Penalty)")

# ==========================================
# 5. ส่วนแสดงผล (ตาราง Blue Box)
# ==========================================
if st.session_state.get('run_done'):
    df_res = st.session_state['res_df']
    
    view_mode = st.radio("มุมมอง:", ["รายห้อง (Room View)", "รายอาจารย์ (Teacher View)"], horizontal=True)
    if view_mode == "รายห้อง (Room View)":
        target = st.selectbox("เลือกห้อง:", sorted(df_res['Room'].unique()))
        filt_df = df_res[df_res['Room'] == target]
    else:
        all_teachers = sorted(list(set([t.strip() for ts in df_res['Teacher'] for t in ts.split(',') if t.strip() != '-'])))
        target = st.selectbox("เลือกอาจารย์:", all_teachers)
        filt_df = df_res[df_res['Teacher'].str.contains(target, na=False)]

    days_map = {'Mon': 'จันทร์', 'Tue': 'อังคาร', 'Wed': 'พุธ', 'Thu': 'พฤหัสบดี', 'Fri': 'ศุกร์'}
    time_headers = [f"{h:02d}:{m:02d}" for h in range(8, 19) for m in [0, 30]][1:] # 08:30 - 18:30
    
    html = f"<div class='tt-container'><table class='tt-table'><tr class='tt-header'><th>Day</th>"
    for t in time_headers: html += f"<th>{t}</th>"
    html += "</tr>"

    for day_en in ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']:
        html += f"<tr><td class='tt-day'>{days_map[day_en]}</td>"
        d_data = filt_df[filt_df['Day'] == day_en]
        curr = 8.5
        while curr < 19.0:
            t_str = f"{int(curr):02d}:{int((curr%1)*60):02d}"
            match = d_data[d_data['Start'] == t_str]
            if not match.empty:
                row = match.iloc[0]
                sh, sm = map(int, row['Start'].split(':')); eh, em = map(int, row['End'].split(':'))
                span = int(((eh + em/60) - (sh + sm/60)) * 2)
                html += f"<td colspan='{span}'><div class='class-box'><span class='c-code'>{row['Course']}</span><span>(S{row['Sec']}) {row['Type']}</span><span>{row['Teacher']}</span>"
                if "Ext.Time" in str(row['Note']): html += f"<span style='color:red; font-size:9px'>Ext.Time</span>"
                html += "</div></td>"
                curr += (span * 0.5)
            else:
                html += "<td></td>"
                curr += 0.5
        html += "</tr>"
    st.markdown(html + "</table></div>", unsafe_allow_html=True)
    st.download_button("📥 Download CSV", df_res.to_csv(index=False).encode('utf-8'), "schedule.csv", "text/csv")
