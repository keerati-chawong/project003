import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import math
import re
import os

# ==========================================
# 1. Page Config & Initialization
# ==========================================
st.set_page_config(page_title="Automatic Scheduler Pro", layout="wide")
st.title("🎓 Automatic Course Scheduler")

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
    if uploaded: return uploaded
    if os.path.exists(default_file): return default_file
    return None

up_room = upload_section("room.csv", "room.csv")
up_teacher_courses = upload_section("teacher_courses.csv", "teacher_courses.csv")
up_ai_in = upload_section("ai_in_courses.csv", "ai_in_courses.csv")
up_cy_in = upload_section("cy_in_courses.csv", "cy_in_courses.csv")
up_teachers = upload_section("all_teachers.csv", "all_teachers.csv")
up_ai_out = upload_section("ai_out_courses.csv", "ai_out_courses.csv")
up_cy_out = upload_section("cy_out_courses.csv", "cy_out_courses.csv")

# ==========================================
# 3. User Configuration
# ==========================================
st.subheader("⚙️ Scheduler Configuration")
schedule_mode_desc = {
    1: "Compact Mode (09:00 - 16:00) - เน้นเกาะกลุ่มช่วงกลางวัน",
    2: "Flexible Mode (08:30 - 19:00) - ยืดหยุ่นพิเศษสำหรับวิชาเยอะ"
}
SCHEDULE_MODE = st.radio("Select Scheduling Mode:", options=[1, 2], format_func=lambda x: schedule_mode_desc[x])
run_button = st.button("🚀 Run Automatic Scheduler", use_container_width=True)

# ==========================================
# 4. Core Logic Function
# ==========================================
def calculate_schedule():
    # --- Time Slot Setup (08:30 - 19:00) ---
    SLOT_MAP = {}
    t_curr = 8.5
    idx = 0
    while t_curr < 19.0:
        h, m = int(t_curr), int((t_curr % 1) * 60)
        SLOT_MAP[idx] = {'time': f"{h:02d}:{m:02d}", 'val': t_curr, 'is_lunch': (12.5 <= t_curr < 13.0)}
        idx += 1
        t_curr += 0.5
    
    TOTAL_SLOTS = len(SLOT_MAP)
    DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
    SLOT_TO_INDEX = {v['time']: k for k, v in SLOT_MAP.items()}

    def time_to_idx(t_str):
        t_str = str(t_str).replace('.', ':').strip()
        match = re.search(r"(\d{1,2}):(\d{2})", t_str)
        if match:
            h, m = match.groups()
            formatted = f"{int(h):02d}:{int(m):02d}"
            return SLOT_TO_INDEX.get(formatted, -1)
        return -1

    # --- Load & Clean Data ---
    try:
        df_room = pd.read_csv(up_room)
        df_teacher_courses = pd.read_csv(up_teacher_courses)
        df_ai_in = pd.read_csv(up_ai_in)
        df_cy_in = pd.read_csv(up_cy_in)
        all_teachers = pd.read_csv(up_teachers)
        df_courses = pd.concat([df_ai_in, df_cy_in], ignore_index=True).fillna(0)
        
        room_list = df_room.to_dict('records')
        room_list.append({'room': 'Online', 'capacity': 9999, 'type': 'virtual'})
    except Exception as e:
        st.error(f"Data Error: {e}")
        return None, None

    teacher_map = df_teacher_courses.groupby('course_code')['teacher_id'].apply(lambda x: [str(i) for i in x]).to_dict()

    # --- Fixed Schedule Logic (สำคัญ: ป้องกันที่นั่งซ้อนกับวิชาที่มีอยู่แล้ว) ---
    occupied_slots = [] # List of (room, day, slot_idx)
    for up_f in [up_ai_out, up_cy_out]:
        if up_f:
            df_f = pd.read_csv(up_f)
            for _, row in df_f.iterrows():
                d_idx = DAYS.index(row['day'][:3]) if str(row.get('day'))[:3] in DAYS else -1
                s_idx = time_to_idx(row.get('start'))
                dur = int(math.ceil((row.get('lecture_hour', 0) + row.get('lab_hour', 0)) * 2))
                if d_idx != -1 and s_idx != -1:
                    for i in range(dur):
                        occupied_slots.append((str(row['room']), d_idx, s_idx + i))

    # --- Task Prep ---
    tasks = []
    for _, row in df_courses.iterrows():
        c_code, sec = str(row['course_code']), int(row['section'])
        t_list = teacher_map.get(c_code, ['Staff'])
        
        for t_type in ['Lec', 'Lab']:
            hours = row.get(f'{t_type.lower()}_hour', 0)
            if hours > 0:
                tasks.append({
                    'uid': f"{c_code}_S{sec}_{t_type}", 'id': c_code, 'sec': sec, 'type': t_type,
                    'dur': int(math.ceil(hours * 2)), 'std': row['enrollment_count'], 
                    'teachers': t_list, 'is_online': row.get(f'{t_type.lower()}_online', 0) == 1
                })

    # --- Solver ---
    model = cp_model.CpModel()
    vars = {} # (uid, room, day, slot) -> BoolVar
    is_scheduled = {}

    progress_bar = st.progress(20, "Building Model...")
    
    for t in tasks:
        uid = t['uid']
        is_scheduled[uid] = model.NewBoolVar(f"s_{uid}")
        candidates = []

        for r in room_list:
            if (t['is_online'] and r['room'] != 'Online') or (not t['is_online'] and r['room'] == 'Online'): continue
            if r['capacity'] < t['std']: continue

            for d_idx in range(len(DAYS)):
                for s_idx in range(TOTAL_SLOTS - t['dur'] + 1):
                    # 1. Lunch & Mode Constraint
                    s_val = SLOT_MAP[s_idx]['val']
                    e_val = s_val + (t['dur'] * 0.5)
                    if any(SLOT_MAP[s_idx+i]['is_lunch'] for i in range(t['dur'])): continue
                    if SCHEDULE_MODE == 1 and (s_val < 9.0 or e_val > 16.0): continue
                    
                    # 2. Fixed Occupied Check
                    if any((r['room'], d_idx, s_idx + i) in occupied_slots for i in range(t['dur'])): continue

                    v = model.NewBoolVar(f"{uid}_{r['room']}_{d_idx}_{s_idx}")
                    vars[(uid, r['room'], d_idx, s_idx)] = v
                    candidates.append(v)

        if candidates:
            model.Add(sum(candidates) == 1).OnlyEnforceIf(is_scheduled[uid])
            model.Add(sum(candidates) == 0).OnlyEnforceIf(is_scheduled[uid].Not())
        else:
            model.Add(is_scheduled[uid] == 0)

    # Constraints: No overlaps (Room & Teacher)
    for d in range(len(DAYS)):
        for s in range(TOTAL_SLOTS):
            for r in [rm['room'] for rm in room_list if rm['room'] != 'Online']:
                room_usage = [vars[k] for k in vars if k[1] == r and k[2] == d and k[3] <= s < k[3] + next(x['dur'] for x in tasks if x['uid'] == k[0])]
                if room_usage: model.Add(sum(room_usage) <= 1)
            
            # Teacher constraint (simplified)
            all_t = set([tea for tk in tasks for tea in tk['teachers']])
            for tea in all_t:
                tea_usage = [vars[k] for k in vars if tea in next(x['teachers'] for x in tasks if x['uid'] == k[0]) and k[2] == d and k[3] <= s < k[3] + next(x['dur'] for x in tasks if x['uid'] == k[0])]
                if tea_usage: model.Add(sum(tea_usage) <= 1)

    model.Maximize(sum(is_scheduled.values()))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 40 
    status = solver.Solve(model)

    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        res = []
        for k, v in vars.items():
            if solver.Value(v):
                t_info = next(x for x in tasks if x['uid'] == k[0])
                res.append({
                    'Day': DAYS[k[2]], 'Start': SLOT_MAP[k[3]]['time'],
                    'End': SLOT_MAP.get(k[3] + t_info['dur'], {'time': '19:00'})['time'],
                    'Room': k[1], 'Course': t_info['id'], 'Sec': t_info['sec'], 'Type': t_info['type']
                })
        return res, []
    return None, None


# ==========================================
# 5. Controller & UI Display (Updated with Teacher View)
# ==========================================
if run_button:
    with st.spinner("กำลังคำนวณตารางสอนที่ดีที่สุด..."):
        res, _ = calculate_schedule()
        if res:
            st.session_state['schedule_results'] = pd.DataFrame(res)
            st.session_state['has_run'] = True
            st.success("✅ จัดตารางสอนสำเร็จ!")
        else:
            st.error("❌ ไม่สามารถจัดตารางได้: เงื่อนไขแน่นเกินไป หรือวิชาที่ Fix ไว้ชนกันเอง")

if st.session_state['has_run']:
    df_res = st.session_state['schedule_results']
    
    st.divider()
    st.subheader("📊 ข้อมูลตารางสอน (Visual Timetable)")

    # 1. เลือกโหมดการดู
    view_mode = st.radio("เลือกมุมมองที่ต้องการ:", ["ดูตามห้องเรียน (Room)", "ดูตามรายชื่ออาจารย์ (Teacher)"], horizontal=True)

    if view_mode == "ดูตามห้องเรียน (Room)":
        target_list = sorted(df_res['Room'].unique())
        selection = st.selectbox("🔍 เลือกห้องเรียน:", target_list)
        filtered_df = df_res[df_res['Room'] == selection]
        display_title = f"📍 ตารางการใช้ห้อง: {selection}"
    else:
        # แยกรายชื่ออาจารย์ที่อาจสอนร่วมกัน (เช่น P1, J1 -> [P1, J1])
        all_teachers = set()
        for t_str in df_res['Teacher'].fillna('Staff'):
            for t in str(t_str).split(','):
                all_teachers.add(t.strip())
        
        selection = st.selectbox("🔍 เลือกชื่ออาจารย์:", sorted(list(all_teachers)))
        # กรองข้อมูลวิชาที่ชื่ออาจารย์ปรากฏอยู่
        filtered_df = df_res[df_res['Teacher'].str.contains(selection, na=False)]
        display_title = f"👨‍🏫 ตารางสอนอาจารย์: {selection}"

    # 2. ฟังก์ชันสร้าง Grid ตารางสอน (ปรับปรุงให้รองรับรายละเอียดมากขึ้น)
    def render_timetable(df):
        days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
        # สร้างช่วงเวลาตั้งแต่ 08:00 - 19:00
        time_cols = [f"{h:02d}:00" for h in range(8, 20)]
        grid = pd.DataFrame('', index=days, columns=time_cols)

        for _, row in df.iterrows():
            # ใช้เวลา Start ในการปักตำแหน่งในตาราง
            start_hour = row['Start'].split(':')[0] + ":00"
            if start_hour in grid.columns:
                # แสดงรหัสวิชา, เซกชัน, และประเภท (และห้องถ้าดูในมุมมองอาจารย์)
                info = f"{row['Course']} (S{row['Sec']}) - {row['Type']}"
                if view_mode == "ดูตามรายชื่ออาจารย์ (Teacher)":
                    info += f"\nRm: {row['Room']}"
                
                # ป้องกันข้อความทับกันหากมีวิชาในชั่วโมงเดียวกัน (ถ้ามี)
                existing = grid.at[row['Day'], start_hour]
                grid.at[row['Day'], start_hour] = (existing + "\n" + info).strip()
        
        return grid

    st.info(f"**{display_title}**")
    st.table(render_timetable(filtered_df))

    # 3. ปุ่มดาวน์โหลดข้อมูลทั้งหมด
    st.divider()
    csv_data = df_res.to_csv(index=False).encode('utf-8')
    st.download_button("📥 ดาวน์โหลดตารางสอนทั้งหมด (CSV)", csv_data, "full_schedule.csv", "text/csv")
