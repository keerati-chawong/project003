import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import math
import re
from collections import defaultdict
import io

# ==========================================
# PAGE CONFIG
# ==========================================
st.set_page_config(
    page_title="Automatic Scheduler Pro - Teacher View", 
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS
st.markdown("""
<style>
    .main-header {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 2rem;
        border-radius: 10px;
        margin-bottom: 2rem;
        color: white;
        text-align: center;
    }
    .upload-section {
        background-color: #f8f9fa;
        padding: 1.5rem;
        border-radius: 8px;
        margin-bottom: 1rem;
    }
    .tt-container { 
        overflow-x: auto; 
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        margin-top: 20px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        border-radius: 8px;
    }
    .tt-table { 
        width: 100%; 
        border-collapse: collapse; 
        min-width: 1200px;
        background: white;
    }
    .tt-table th { 
        background-color: #2c3e50 !important; 
        color: white !important; 
        border: 1px solid #34495e; 
        text-align: center; 
        padding: 12px 8px; 
        font-size: 13px;
        font-weight: 600;
        position: sticky;
        top: 0;
        z-index: 11;
    }
    .tt-table td { 
        border: 1px solid #dee2e6; 
        text-align: center; 
        padding: 4px; 
        height: 80px;
        background-color: #ffffff;
    }
    .tt-day { 
        background-color: #34495e !important; 
        color: white !important; 
        font-weight: bold !important; 
        width: 100px; 
        position: sticky; 
        left: 0; 
        z-index: 12; 
        border-right: 3px solid #2c3e50 !important; 
        font-size: 14px;
    }
    .class-box { 
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        border-radius: 8px; 
        padding: 8px; 
        height: 95%; 
        display: flex; 
        flex-direction: column; 
        justify-content: center; 
        color: white !important; 
        box-shadow: 0 2px 8px rgba(0,0,0,0.15);
        font-size: 11px; 
        line-height: 1.4;
        transition: transform 0.2s;
    }
    .class-box:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(0,0,0,0.2);
    }
    .c-code { 
        font-weight: 700; 
        font-size: 13px; 
        color: #ffffff !important;
        text-shadow: 1px 1px 2px rgba(0,0,0,0.2);
        margin-bottom: 4px;
    }
    .teacher-badge {
        background-color: rgba(255,255,255,0.2);
        padding: 2px 6px;
        border-radius: 4px;
        font-size: 10px;
        margin-top: 4px;
    }
    .ext-time-badge {
        background-color: #e74c3c;
        color: white;
        padding: 2px 6px;
        border-radius: 3px;
        font-size: 9px;
        font-weight: bold;
        margin-top: 4px;
    }
    .success-box {
        background-color: #d4edda;
        border: 1px solid #c3e6cb;
        color: #155724;
        padding: 1rem;
        border-radius: 8px;
        margin: 1rem 0;
    }
    .info-box {
        background-color: #d1ecf1;
        border: 1px solid #bee5eb;
        color: #0c5460;
        padding: 1rem;
        border-radius: 8px;
        margin: 1rem 0;
    }
    .stButton>button {
        width: 100%;
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        border: none;
        padding: 0.75rem 1.5rem;
        font-size: 16px;
        font-weight: 600;
        border-radius: 8px;
        transition: all 0.3s;
    }
    .stButton>button:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 20px rgba(102, 126, 234, 0.4);
    }
</style>
""", unsafe_allow_html=True)

# ==========================================
# HELPER FUNCTIONS
# ==========================================
def get_slot_map():
    """สร้าง mapping ของช่วงเวลา 30 นาที"""
    slots = {}
    t_start = 8.5
    idx = 0
    while t_start <= 19.5:
        h, m = int(t_start), round((t_start % 1) * 60)
        slots[idx] = {
            'time': f"{h:02d}:{m:02d}", 
            'val': t_start, 
            'is_lunch': (12.5 <= t_start < 13.0)
        }
        idx += 1
        t_start += 0.5
    return slots

def time_to_slot_index(time_str, slot_inv):
    """แปลงเวลาเป็น slot index"""
    time_str = str(time_str).strip().replace('.', ':')
    match = re.search(r"(\d{1,2}):(\d{2})", time_str)
    if match:
        h, m = match.groups()
        formatted = f"{int(h):02d}:{int(m):02d}"
        return slot_inv.get(formatted, -1)
    return -1

def parse_unavailable_time(input_val, days_list, slot_inv):
    """แปลงเวลาว่างของอาจารย์"""
    un_slots = {d: set() for d in range(len(days_list))}
    if pd.isna(input_val) or not input_val:
        return un_slots
    
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
                    for s in range(s_i, e_i):
                        un_slots[d_idx].add(s)
    return un_slots

# ==========================================
# SOLVER ENGINE
# ==========================================
def calculate_schedule(files, mode, solver_time, penalty_val):
    """คำนวณตารางเรียนโดยใช้ OR-Tools CP-SAT Solver"""
    
    SLOT_MAP = get_slot_map()
    DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
    SLOT_INV = {v['time']: k for k, v in SLOT_MAP.items()}
    TOTAL_SLOTS = len(SLOT_MAP)

    try:
        # โหลดข้อมูล
        df_room = pd.read_csv(files['room'])
        room_list = df_room.to_dict('records')
        room_list.append({'room': 'Online', 'capacity': 9999, 'type': 'virtual'})
        
        df_tc = pd.read_csv(files['teacher_courses'])
        df_courses = pd.concat([
            pd.read_csv(files['ai_in']), 
            pd.read_csv(files['cy_in'])
        ], ignore_index=True).fillna(0)
        
        df_teacher = pd.read_csv(files['all_teachers'])

        # สร้าง mapping อาจารย์-วิชา
        t_map = defaultdict(list)
        for _, row in df_tc.iterrows():
            t_map[str(row['course_code']).strip()].append(
                str(row['teacher_id']).strip()
            )
        
        # สร้าง mapping เวลาว่างของอาจารย์
        un_map = {
            str(row['teacher_id']).strip(): parse_unavailable_time(
                row.get('unavailable_times'), DAYS, SLOT_INV
            ) 
            for _, row in df_teacher.iterrows()
        }

        # 1. Fixed Tasks (จากไฟล์ ai_out, cy_out)
        fixed_tasks = []
        for key in ['ai_out', 'cy_out']:
            if files[key]:
                df_f = pd.read_csv(files[key])
                for _, r in df_f.iterrows():
                    d_i = DAYS.index(str(r['day'])[:3]) if str(r['day'])[:3] in DAYS else -1
                    s_i = time_to_slot_index(r['start'], SLOT_INV)
                    dur = int(math.ceil((r.get('lecture_hour', 0) + r.get('lab_hour', 0)) * 2))
                    
                    if d_i != -1 and s_i != -1:
                        fixed_tasks.append({
                            'uid': f"FIX_{r['course_code']}_S{r['section']}",
                            'id': str(r['course_code']), 
                            'sec': int(r['section']), 
                            'dur': dur, 
                            'type': 'Fixed',
                            'tea': t_map.get(str(r['course_code']).strip(), ['-']),
                            'fixed_room': True, 
                            'target_room': str(r['room']), 
                            'f_d': d_i, 
                            'f_s': s_i
                        })

        # 2. Dynamic Tasks
        tasks = []
        for _, row in df_courses.iterrows():
            c, s = str(row['course_code']).strip(), int(row['section'])
            tea = t_map.get(c, ['Unknown'])
            opt = row.get('optional', 1)
            
            # Lecture
            lec_slots = int(math.ceil(row['lecture_hour'] * 2))
            p = 1
            while lec_slots > 0:
                dur = min(lec_slots, 6)
                uid = f"{c}_S{s}_Lec_P{p}"
                if not any(tk['uid'] == uid for tk in fixed_tasks):
                    tasks.append({
                        'uid': uid, 
                        'id': c, 
                        'sec': s, 
                        'type': 'Lec', 
                        'dur': dur, 
                        'std': row['enrollment_count'], 
                        'tea': tea, 
                        'opt': opt, 
                        'online': row.get('lec_online') == 1
                    })
                lec_slots -= dur
                p += 1
            
            # Lab
            lab_dur = int(math.ceil(row['lab_hour'] * 2))
            if lab_dur > 0:
                uid = f"{c}_S{s}_Lab"
                if not any(tk['uid'] == uid for tk in fixed_tasks):
                    tasks.append({
                        'uid': uid, 
                        'id': c, 
                        'sec': s, 
                        'type': 'Lab', 
                        'dur': lab_dur, 
                        'std': row['enrollment_count'], 
                        'tea': tea, 
                        'opt': opt, 
                        'online': row.get('lab_online') == 1
                    })

        # 3. CP-SAT Model
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
            model.Add(model.NewIntVar(0, TOTAL_SLOTS+2, f"e_{uid}") == t_s + t['dur'])
            task_vars[uid] = {'d': t_d, 's': t_s}
            
            if t.get('fixed_room'):
                model.Add(t_d == t['f_d'])
                model.Add(t_s == t['f_s'])

            # สร้าง variables สำหรับทุก (room, day, slot) ที่เป็นไปได้
            for r in room_list:
                # ตรวจสอบเงื่อนไข room
                if t.get('online') and r['room'] != 'Online':
                    continue
                if not t.get('online') and (r['room'] == 'Online' or r['capacity'] < t.get('std', 0)):
                    continue
                if t.get('fixed_room') and r['room'] != t['target_room']:
                    continue
                if t.get('type') == 'Lab' and 'lab' not in str(r.get('type', '')).lower():
                    continue

                for di in range(5):
                    for si in range(TOTAL_SLOTS - t['dur']):
                        sv = SLOT_MAP[si]['val']
                        ev = sv + t['dur'] * 0.5
                        
                        # Mode 1: Compact (09:00-16:00 only)
                        if mode == 1 and (sv < 9.0 or ev > 16.0):
                            continue
                        
                        # ตรวจสอบเวลาพักกลางวัน
                        if any(SLOT_MAP[si+i]['is_lunch'] for i in range(t['dur'])):
                            continue
                        
                        # ตรวจสอบเวลาว่างของอาจารย์
                        if any(tid in un_map and si+i in un_map[tid][di] 
                               for tid in t['tea'] for i in range(t['dur'])):
                            continue

                        v = model.NewBoolVar(f"{uid}_{r['room']}_{di}_{si}")
                        vars[(uid, r['room'], di, si)] = v
                        model.Add(t_d == di).OnlyEnforceIf(v)
                        model.Add(t_s == si).OnlyEnforceIf(v)
                        
                        # Mode 2: Flexible แต่มี penalty นอกเวลา
                        if mode == 2 and (sv < 9.0 or ev > 16.0):
                            pen_terms.append(v * penalty_val)
                        
                        # เก็บ lookup สำหรับ conflict checking
                        for i in range(t['dur']):
                            room_lookup[r['room']][di][si+i].append(v)
                            for tid in t['tea']:
                                tea_lookup[tid][di][si+i].append(v)

            # แต่ละ task ต้องเลือก slot เดียว
            model.Add(sum(vars[k] for k in vars if k[0] == uid) == 1).OnlyEnforceIf(is_sched[uid])
            
            # Objective: ให้น้ำหนักสูงกับ fixed tasks และวิชาบังคับ
            weight = 1000000 if t.get('fixed_room') else (1000 if t.get('opt') == 0 else 100)
            obj_terms.append(is_sched[uid] * weight)

        # Conflict constraints
        for lookup in [room_lookup, tea_lookup]:
            for k in lookup:
                for d in lookup[k]:
                    for s in lookup[k][d]:
                        if len(lookup[k][d][s]) > 1:
                            model.Add(sum(lookup[k][d][s]) <= 1)

        # Maximize objective
        model.Maximize(sum(obj_terms) - sum(pen_terms))
        
        # Solve
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = solver_time
        status = solver.Solve(model)

        # Extract results
        res_final = []
        if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            for t in (fixed_tasks + tasks):
                uid = t['uid']
                if solver.Value(is_sched[uid]):
                    d = solver.Value(task_vars[uid]['d'])
                    s = solver.Value(task_vars[uid]['s'])
                    
                    # หาชื่อห้อง
                    room_name = "Unknown"
                    for k, v in vars.items():
                        if k[0] == uid and k[2] == d and k[3] == s and solver.Value(v):
                            room_name = k[1]
                            break
                    
                    # ตรวจสอบว่าเป็นเวลานอกช่วง
                    start_val = SLOT_MAP[s]['val']
                    end_val = SLOT_MAP[s + t['dur'] - 1]['val']
                    is_extended = start_val < 9.0 or end_val >= 16.0
                    
                    res_final.append({
                        'Day': DAYS[d],
                        'Start': SLOT_MAP[s]['time'],
                        'End': SLOT_MAP[s + t['dur']]['time'],
                        'Room': room_name,
                        'Course': t['id'],
                        'Sec': t['sec'],
                        'Type': t.get('type', '-'),
                        'Teacher': ",".join(t['tea']),
                        'Note': "Extended Time" if is_extended else ""
                    })
            
            return pd.DataFrame(res_final)
        
        return None
        
    except Exception as e:
        st.error(f"❌ เกิดข้อผิดพลาด: {e}")
        return None

# ==========================================
# MAIN APP
# ==========================================
def main():
    # Header
    st.markdown("""
    <div class='main-header'>
        <h1>📚 Automatic Scheduler Pro</h1>
        <p>ระบบจัดตารางเรียนอัตโนมัติ พร้อมมุมมองตารางสอนอาจารย์</p>
    </div>
    """, unsafe_allow_html=True)

    # Sidebar - Upload Section
    st.sidebar.header("📂 1. อัปโหลดข้อมูล")
    
    with st.sidebar.expander("📋 ไฟล์บังคับ (5 ไฟล์)", expanded=True):
        up_files = {
            'room': st.file_uploader("1️⃣ room.csv", type="csv", key="room"),
            'teacher_courses': st.file_uploader("2️⃣ teacher_courses.csv", type="csv", key="tc"),
            'ai_in': st.file_uploader("3️⃣ ai_in_courses.csv", type="csv", key="ai_in"),
            'cy_in': st.file_uploader("4️⃣ cy_in_courses.csv", type="csv", key="cy_in"),
            'all_teachers': st.file_uploader("5️⃣ all_teachers.csv", type="csv", key="teachers"),
        }
    
    with st.sidebar.expander("📌 ไฟล์ตารางคงที่ (Optional)"):
        up_files['ai_out'] = st.file_uploader("6️⃣ ai_out.csv (Fixed Schedule)", type="csv", key="ai_out")
        up_files['cy_out'] = st.file_uploader("7️⃣ cy_out.csv (Fixed Schedule)", type="csv", key="cy_out")

    st.sidebar.divider()
    
    # Sidebar - Solver Settings
    st.sidebar.header("⚙️ 2. ตั้งค่า Solver")
    
    mode_sel = st.sidebar.radio(
        "โหมดการจัดตาราง:",
        [1, 2],
        format_func=lambda x: "🔒 Compact (09:00-16:00)" if x == 1 else "🔓 Flexible (08:30-19:00)",
        help="Compact = จัดเฉพาะช่วง 09:00-16:00 เท่านั้น\nFlexible = ยืดหยุ่นนอกช่วงได้ แต่มี penalty"
    )
    
    solver_time = st.sidebar.slider(
        "⏱️ เวลาในการประมวลผล (วินาที):",
        min_value=10,
        max_value=600,
        value=120,
        step=10,
        help="เวลาที่ให้ Solver คำนวณหาคำตอบ (นานกว่า = คำตอบดีกว่า)"
    )
    
    penalty_val = st.sidebar.slider(
        "⚖️ Penalty Score (สำหรับเวลานอกช่วง):",
        min_value=0,
        max_value=200,
        value=10,
        step=5,
        help="คะแนนลบที่จะหักเมื่อจัดคาบนอกเวลา 09:00-16:00 (ใช้ใน Flexible mode)"
    )

    # Run Button
    st.sidebar.divider()
    run_button = st.sidebar.button("🚀 คำนวณตารางเรียน", use_container_width=True)

    # Main Content
    if run_button:
        mandatory = ['room', 'teacher_courses', 'ai_in', 'cy_in', 'all_teachers']
        if any(up_files[k] is None for k in mandatory):
            st.error("❌ กรุณาอัปโหลดไฟล์บังคับ 5 ไฟล์แรกให้ครบถ้วน")
        else:
            with st.status("🤖 กำลังประมวลผลตารางเรียน...", expanded=True) as status:
                st.write("📊 กำลังโหลดข้อมูล...")
                st.write("🧮 กำลังสร้างโมเดล CP-SAT...")
                st.write(f"⚙️ ใช้เวลาสูงสุด {solver_time} วินาที")
                
                df_res = calculate_schedule(up_files, mode_sel, solver_time, penalty_val)
                
                if df_res is not None and not df_res.empty:
                    st.session_state['res_df'] = df_res
                    st.session_state['run_done'] = True
                    status.update(label="✅ คำนวณสำเร็จ!", state="complete")
                    st.balloons()
                else:
                    st.error("❌ ไม่สามารถหาคำตอบได้ ลองเพิ่มเวลาประมวลผลหรือลด Penalty Score")
                    status.update(label="❌ ล้มเหลว", state="error")

    # Display Results
    if st.session_state.get('run_done'):
        df_res = st.session_state['res_df']
        
        st.markdown("""
        <div class='success-box'>
            <h3>✅ ตารางเรียนสำเร็จ!</h3>
            <p>พบรายการทั้งหมด {} รายการ</p>
        </div>
        """.format(len(df_res)), unsafe_allow_html=True)
        
        # View Mode Selection
        col1, col2 = st.columns([2, 1])
        with col1:
            view_mode = st.radio(
                "🔍 เลือกมุมมอง:",
                ["👨‍🏫 รายอาจารย์ (Teacher View)", "🏫 รายห้อง (Room View)"],
                horizontal=True
            )
        
        # Filter based on view mode
        if view_mode == "👨‍🏫 รายอาจารย์ (Teacher View)":
            # แยกรายชื่ออาจารย์ทั้งหมด
            all_teachers = sorted(list(set([
                i.strip() 
                for s in df_res['Teacher'] 
                for i in str(s).split(',') 
                if i.strip() != '-' and i.strip() != 'Unknown'
            ])))
            
            if not all_teachers:
                st.warning("⚠️ ไม่พบข้อมูลอาจารย์ในตาราง")
                return
            
            with col2:
                target = st.selectbox("🔎 เลือกอาจารย์:", all_teachers)
            
            filt_df = df_res[df_res['Teacher'].str.contains(target, na=False)]
            
            if filt_df.empty:
                st.info(f"ℹ️ ไม่พบตารางสอนสำหรับ {target}")
                return
            
            # สถิติ
            st.markdown("""
            <div class='info-box'>
                <strong>📊 สถิติการสอน:</strong><br>
                • จำนวนคาบสอนทั้งหมด: {} คาบ<br>
                • จำนวนวิชา: {} วิชา
            </div>
            """.format(len(filt_df), filt_df['Course'].nunique()), unsafe_allow_html=True)
            
        else:  # Room View
            with col2:
                target = st.selectbox("🔎 เลือกห้อง:", sorted(df_res['Room'].unique()))
            
            filt_df = df_res[df_res['Room'] == target]
            
            if filt_df.empty:
                st.info(f"ℹ️ ไม่พบตารางสำหรับห้อง {target}")
                return

        # Generate Timetable HTML
        days_map = {
            'Mon': 'จันทร์',
            'Tue': 'อังคาร',
            'Wed': 'พุธ',
            'Thu': 'พฤหัสบดี',
            'Fri': 'ศุกร์'
        }
        
        time_headers = [f"{h:02d}:{m:02d}" for h in range(8, 20) for m in [0, 30]][1:]
        
        html = "<div class='tt-container'><table class='tt-table'>"
        html += "<tr><th style='width: 100px;'>Day</th>"
        for t in time_headers:
            html += f"<th style='min-width: 70px;'>{t}</th>"
        html += "</tr>"
        
        for day in ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']:
            html += f"<tr><td class='tt-day'>{days_map[day]}</td>"
            d_data = filt_df[filt_df['Day'] == day]
            curr = 8.5
            
            while curr < 19.0:
                t_str = f"{int(curr):02d}:{round((curr % 1) * 60):02d}"
                match = d_data[d_data['Start'] == t_str]
                
                if not match.empty:
                    r = match.iloc[0]
                    sh, sm = map(int, r['Start'].split(':'))
                    eh, em = map(int, r['End'].split(':'))
                    span = int(((eh + em/60) - (sh + sm/60)) * 2)
                    
                    html += f"<td colspan='{span}'><div class='class-box'>"
                    html += f"<div class='c-code'>{r['Course']}</div>"
                    html += f"<div>Section
