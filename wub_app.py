import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import math
import re
import os
from collections import defaultdict

# ==========================================
# 1. Page Config & CSS (แก้ไขสีหัวตารางและกล่องวิชาให้อ่านออกชัดเจน)
# ==========================================
st.set_page_config(page_title="Automatic Scheduler Pro", layout="wide")

st.markdown("""
<style>
    .tt-container { overflow-x: auto; font-family: 'Helvetica', sans-serif; margin-top: 20px; }
    .tt-table { width: 100%; border-collapse: collapse; min-width: 1200px; }
    
    /* หัวตาราง: พื้นหลังมืด ฟอนต์ขาวชัดเจน */
    .tt-table th { 
        background-color: #343a40 !important; 
        color: #ffffff !important; 
        border: 1px solid #444; 
        text-align: center; 
        padding: 8px; 
        font-size: 14px; 
    }
    
    .tt-table td { border: 1px solid #dee2e6; text-align: center; padding: 4px; height: 75px; }

    /* คอลัมน์ Day: พื้นหลังเทาอ่อน ฟอนต์ดำเข้ม */
    .tt-day { 
        background-color: #f8f9fa !important; 
        color: #333333 !important; 
        font-weight: bold !important; 
        width: 85px; 
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
# 2. Helper Functions (Python 3.13 Compatible)
# ==========================================
def get_slot_map():
    slots = {}
    t_start = 8.5
    idx = 0
    while t_start <= 19.5: # ขยายครอบคลุมเวลาเลิก คาบ 19:00
        h, m = int(t_start), round((t_start % 1) * 60)
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
                s_i = time_to_slot_index(start_t, slot_inv)
                e_i = time_to_slot_index(end_t, slot_inv)
                if s_i != -1 and e_i != -1:
                    for s in range(s_i, e_i): un_slots[d_idx].add(s)
    return un_slots

# ==========================================
# 3. Solver Engine (Optimized & Resource Efficient)
# ==========================================
def calculate_schedule(data_dict, mode, solver_time, penalty_val):
    SLOT_MAP = get_slot_map()
    DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
    SLOT_INV = {v['time']: k for k, v in SLOT_MAP.items()}
    TOTAL_SLOTS = len(SLOT_MAP)

    try:
        room_list = data_dict['room'].to_dict('records')
        room_list.append({'room': 'Online', 'capacity': 9999, 'type': 'virtual'})
        
        df_tc = data_dict['teacher_courses']
        df_courses = pd.concat([data_dict['ai_in'], data_dict['cy_in']], ignore_index=True).fillna(0)
        df_teacher = data_dict['all_teachers']

        teacher_map = defaultdict(list)
        for _, row in df_tc.iterrows():
            teacher_map[str(row['course_code']).strip()].append(str(row['teacher_id']).strip())
        
        un_map = {str(row['teacher_id']).strip(): parse_unavailable_time(row.get('unavailable_times'), DAYS, SLOT_INV) for _, row in df_teacher.iterrows()}

        # 1. Fixed Schedule (Force Room 9127 and others)
        fixed_tasks = []
        for key in ['ai_out', 'cy_out']:
            if data_dict[key] is not None:
                df_f = data_dict[key]
                for _, r in df_f.iterrows():
                    d_i = DAYS.index(str(r['day'])[:3]) if str(r['day'])[:3] in DAYS else -1
                    s_i = time_to_slot_index(r['start'], SLOT_INV)
                    dur = int(math.ceil((r.get('lecture_hour', 0) + r.get('lab_hour', 0)) * 2))
                    if d_i != -1 and s_i != -1:
                        r_name = str(r['room'])
                        if not any(rm['room'] == r_name for rm in room_list):
                            room_list.append({'room': r_name, 'capacity': 999, 'type': 'lecture'})
                        
                        fixed_tasks.append({
                            'uid': f"FIX_{r['course_code']}_S{r['section']}_{r_name}",
                            'id': str(r['course_code']), 'sec': int(r['section']), 'dur': dur, 'type': 'Fixed',
                            'tea': teacher_map.get(str(r['course_code']).strip(), ['-']),
                            'fixed_room': True, 'target_room': r_name, 'f_d': d_i, 'f_s': s_i
                        })

        # 2. Dynamic Tasks
        tasks = []
        for _, r in df_courses.iterrows():
            c, s = str(r['course_code']).strip(), int(r['section'])
            tea, opt = teacher_map.get(c, ['Unknown']), r.get('optional', 1)
            lec_slots = int(math.ceil(r['lecture_hour'] * 2))
            p = 1
            while lec_slots > 0:
                dur = min(lec_slots, 6)
                uid = f"{c}_S{s}_Lec_P{p}"
                if not any(tk['uid'].startswith(f"FIX_{c}_S{s}") for tk in fixed_tasks):
                    tasks.append({'uid': uid, 'id': c, 'sec': s, 'type': 'Lec', 'dur': dur, 'std': r.get('enrollment_count', 0), 'tea': tea, 'opt': opt, 'online': r.get('lec_online')==1})
                lec_slots -= dur; p += 1
            lab_dur = int(math.ceil(r['lab_hour'] * 2))
            if lab_dur > 0:
                uid = f"{c}_S{s}_Lab"
                if not any(tk['uid'].startswith(f"FIX_{c}_S{s}") for tk in fixed_tasks):
                    tasks.append({'uid': uid, 'id': c, 'sec': s, 'type': 'Lab', 'dur': lab_dur, 'std': r.get('enrollment_count', 0), 'tea': tea, 'opt': opt, 'online': r.get('lab_online')==1})

        # 3. Solver Setup
        model = cp_model.CpModel()
        vars, is_sched, task_vars = {}, {}, {}
        room_lookup = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        tea_lookup = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        obj_terms, pen_terms = [], []

        for t in (fixed_tasks + tasks):
            uid = t['uid']
            is_sched[uid] = model.NewBoolVar(f"sc_{uid}")
            t_d = model.NewIntVar(0, 4, f"d_{uid}"); t_s = model.NewIntVar(0, TOTAL_SLOTS-1, f"s_{uid}")
            model.Add(model.NewIntVar(0, TOTAL_SLOTS+2, f"e_{uid}") == t_s + t['dur'])
            task_vars[uid] = {'d': t_d, 's': t_s}
            if t.get('fixed_room'):
                model.Add(t_d == t['f_d']); model.Add(t_s == t['f_s'])

            task_cands = []
            for r in room_list:
                if t.get('online') and r['room'] != 'Online': continue
                if not t.get('online') and (r['room'] == 'Online' or r['capacity'] < t.get('std', 0)): continue
                if t.get('fixed_room') and r['room'] != t['target_room']: continue
                if 'lab' in t.get('type','').lower() and 'lab' not in str(r.get('type','')).lower(): continue

                for d in range(5):
                    for s in range(TOTAL_SLOTS - t['dur']):
                        sv = SLOT_MAP[s]['val']
                        if mode == 1 and (sv < 9.0 or (sv + t['dur']*0.5) > 16.0): continue
                        if any(SLOT_MAP[s+i]['is_lunch'] for i in range(t['dur'])): continue
                        if any(tid in un_map and s+i in un_map[tid][d] for tid in t['tea'] for i in range(t['dur'])): continue

                        v = model.NewBoolVar(f"{uid}_{r['room']}_{d}_{s}")
                        vars[(uid, r['room'], d, s)] = v
                        task_cands.append(v)
                        model.Add(t_d == d).OnlyEnforceIf(v); model.Add(t_s == s).OnlyEnforceIf(v)
                        
                        if mode == 2 and (sv < 9.0 or (sv + t['dur']*0.5) > 16.0):
                            pen_terms.append(v * penalty_val)
                            
                        for i in range(t['dur']):
                            room_lookup[r['room']][d][s+i].append(v)
                            for tid in t['tea']: tea_lookup[tid][d][s+i].append(v)

            if task_cands: model.Add(sum(task_cands) == is_sched[uid])
            else: model.Add(is_sched[uid] == 0)
            obj_terms.append(is_sched[uid] * (1000000 if t.get('fixed_room') else (1000 if t.get('opt')==0 else 100)))

        # No overlap constraints (Optimized Speed)
        for lookup in [room_lookup, tea_lookup]:
            for k in lookup:
                for d in lookup[k]:
                    for s in lookup[k][d]:
                        if len(lookup[k][d][s]) > 1: model.Add(sum(lookup[k][d][s]) <= 1)

        model.Maximize(sum(obj_terms) - sum(pen_terms))
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = solver_time
        solver.parameters.num_search_workers = 4 # กินทรัพยากรน้อยลงแต่เสถียร
        status = solver.Solve(model)

        res_final = []
        if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            for t in (fixed_tasks + tasks):
                uid = t['uid']
                if solver.Value(is_sched[uid]):
                    d_val, s_val = solver.Value(task_vars[uid]['d']), solver.Value(task_vars[uid]['s'])
                    # Safe Python 3.13 loop logic (Avoid 'i' scope issue)
                    room_name = "Unknown"
                    for k, v in vars.items():
                        if k[0] == uid and k[2] == d_val and k[3] == s_val and solver.Value(v):
                            room_name = k[1]; break
                    
                    notes = (["Online"] if t.get('online') else []) + (["Ext.Time"] if SLOT_MAP[s_val]['val'] < 9.0 or (SLOT_MAP[s_val]['val'] + t['dur']*0.5) > 16.0 else [])
                    res_final.append({'Day': DAYS[d_val], 'Start': SLOT_MAP[s_val]['time'], 'End': SLOT_MAP[s_val+t['dur']]['time'], 'Room': room_name, 'Course': t['id'], 'Sec': t['sec'], 'Type': t.get('type','-'), 'Teacher': ",".join(t['tea']), 'Note': ", ".join(notes)})
            return pd.DataFrame(res_final)
        return None
    except Exception as e:
        st.error(f"❌ Solver Error: {e}")
        return None

# ==========================================
# 4. UI Layout (Sidebar, Upload & Config)
# ==========================================
st.sidebar.header("📂 1. อัปโหลดข้อมูล (7 ไฟล์)")

def load_data_file(file_key, default_name):
    # ตรวจสอบการอัปโหลดไฟล์
    up = st.sidebar.file_uploader(f"Upload {default_name}", type="csv")
    if up: return pd.read_csv(up)
    # ระบบ Fallback: ถ้านำไฟล์ไปวางใน Repo จะรันค่าเดิมได้ทันที
    if os.path.exists(default_name): return pd.read_csv(default_name)
    return None

df_dict = {
    'room': load_data_file('room', 'room.csv'),
    'teacher_courses': load_data_file('teacher_courses', 'teacher_courses.csv'),
    'ai_in': load_data_file('ai_in', 'ai_in_courses.csv'),
    'cy_in': load_data_file('cy_in', 'cy_in_courses.csv'),
    'all_teachers': load_data_file('all_teachers', 'all_teachers.csv'),
    'ai_out': load_data_file('ai_out', 'ai_out_courses.csv'),
    'cy_out': load_data_file('cy_out', 'cy_out_courses.csv'),
}

st.sidebar.divider()
st.sidebar.header("⚙️ 2. ตั้งค่า Solver")
mode_sel = st.sidebar.radio("โหมดการจัด:", [1, 2], format_func=lambda x: "Compact (09-16)" if x==1 else "Flexible (08:30-19)")
solver_t = st.sidebar.slider("เวลาประมวลผล (วินาที):", 10, 600, 120)
penalty_v = st.sidebar.slider("Penalty Score (หักคะแนน Ext.Time):", 0, 100, 10)

# ==========================================
# 5. Main Controller
# ==========================================
if st.button("🚀 Run Automatic Scheduler", use_container_width=True):
    mandatory = ['room', 'teacher_courses', 'ai_in', 'cy_in', 'all_teachers']
    if any(df_dict[k] is None for k in mandatory):
        st.error("❌ ข้อมูลไม่ครบ กรุณาอัปโหลดไฟล์ หรือตรวจสอบว่ามีไฟล์เริ่มต้นอยู่ใน Repo")
    else:
        with st.status("🤖 AI กำลังคำนวณตารางที่ดีที่สุด...", expanded=True) as status:
            df_res = calculate_schedule(df_dict, mode_sel, solver_t, penalty_v)
            if df_res is not None and not df_res.empty:
                st.session_state['res_df'], st.session_state['run_done'] = df_res, True
                status.update(label="✅ จัดตารางสำเร็จ!", state="complete")
            else: st.error("❌ หาคำตอบไม่ได้ (ลองเพิ่มเวลาหรือลด Penalty)")

# ==========================================
# 6. Visualization (Room & Teacher View)
# ==========================================
if st.session_state.get('run_done'):
    df_res = st.session_state['res_df']
    
    # เงื่อนไขข้อ 2: มุมมองตารางสอนอาจารย์
    view_mode = st.radio("เลือกมุมมอง:", ["รายห้อง (Room View)", "รายอาจารย์ (Teacher View)"], horizontal=True)
    
    if view_mode == "รายห้อง (Room View)":
        target = st.selectbox("เลือกห้อง:", sorted(df_res['Room'].unique()))
        filt_df = df_res[df_res['Room'] == target]
    else:
        # ดึงรายชื่ออาจารย์จากคอลัมน์ผลลัพธ์
        all_t = sorted(list(set([i.strip() for s in df_res['Teacher'] for i in str(s).split(',') if i.strip() != '-'])))
        target = st.selectbox("เลือกรายชื่ออาจารย์:", all_t)
        filt_df = df_res[df_res['Teacher'].str.contains(target, na=False)]

    # HTML Timetable Generator (Blue Box Style)
    days_map = {'Mon': 'จันทร์', 'Tue': 'อังคาร', 'Wed': 'พุธ', 'Thu': 'พฤหัสบดี', 'Fri': 'ศุกร์'}
    time_headers = [f"{h:02d}:{m:02d}" for h in range(8, 19) for m in [0, 30]][1:]
    
    html = f"<div class='tt-container'><table class='tt-table'><tr class='tt-header'><th>Day</th>"
    for t in time_headers: html += f"<th>{t}</th>"
    html += "</tr>"

    for d_en in ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']:
        html += f"<tr><td class='tt-day'>{days_map[d_en]}</td>"
        d_data = filt_df[filt_df['Day'] == d_en]
        curr = 8.5
        while curr < 19.0:
            t_str = f"{int(curr):02d}:{round((curr%1)*60):02d}"
            match = d_data[d_data['Start'] == t_str]
            if not match.empty:
                r = match.iloc[0]
                sh, sm = map(int, r['Start'].split(':')); eh, em = map(int, r['End'].split(':'))
                span = int(((eh + em/60) - (sh + sm/60)) * 2)
                html += f"<td colspan='{span}'><div class='class-box'><span class='c-code'>{r['Course']}</span><span>(S{r['Sec']}) {r['Type']}</span><span>{r['Teacher']}</span>"
                if "Ext.Time" in str(r['Note']): html += f"<span style='color:red; font-size:9px'>Ext.Time</span>"
                html += "</div></td>"; curr += (span * 0.5)
            else: html += "<td></td>"; curr += 0.5
        html += "</tr>"
    st.markdown(html + "</table></div>", unsafe_allow_html=True)
    st.download_button("📥 Download CSV", df_res.to_csv(index=False).encode('utf-8'), "schedule.csv", "text/csv")
