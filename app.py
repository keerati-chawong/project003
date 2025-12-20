import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import math
import re
import io

# ตั้งค่าหน้าเว็บ
st.set_page_config(page_title="Automatic Scheduler", layout="wide")
st.title("🎓 Automatic Course Scheduler")

# ==========================================
# 1. User Config (UI Side)
# ==========================================
schedule_mode_desc = {
    1: "Compact Mode (09:00 - 16:00)",
    2: "Flexible Mode (08:30 - 19:00)"
}
SCHEDULE_MODE = st.radio(
    "Select Scheduling Mode:",
    options=[1, 2],
    format_func=lambda x: schedule_mode_desc[x]
)

st.write(f"**Current Mode:** {schedule_mode_desc[SCHEDULE_MODE]}")

# ปุ่มกดเพื่อเริ่มการจัดตาราง
run_button = st.button("🚀 Run Scheduler")

# ==========================================
# ฟังก์ชันคำนวณ (คืนค่า DataFrame แทนการแสดงผลทันที)
# ==========================================
def calculate_schedule():
    # --- Time Slot Setup ---
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
    
    TOTAL_SLOTS = len(SLOT_MAP)
    DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
    SLOT_TO_INDEX = {v['time']: k for k, v in SLOT_MAP.items()}

    def time_to_slot_index(time_str):
        time_str = str(time_str).strip()
        match = re.search(r"(\d{1,2})[:.](\d{2})", time_str)
        if match:
            h, m = match.groups()
            time_str = f"{int(h):02d}:{int(m):02d}"
            if time_str in SLOT_TO_INDEX:
                return SLOT_TO_INDEX[time_str]
        return -1

    def parse_unavailable_time(unavailable_input):
        unavailable_slots_by_day = {d_idx: set() for d_idx in range(len(DAYS))}
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

            try: day_idx = DAYS.index(day_abbr)
            except ValueError: continue

            start_slot = time_to_slot_index(start_time_str)
            end_slot = time_to_slot_index(end_time_str)

            if start_slot == -1 or end_slot == -1 or start_slot >= end_slot: continue

            for slot in range(start_slot, end_slot):
                unavailable_slots_by_day[day_idx].add(slot)
        return unavailable_slots_by_day

    # --- Data Loading ---
    try:
        df_room = pd.read_csv('Web_schedule-main/Web_schedule-main/room.csv')
        df_teacher_courses = pd.read_csv('Web_schedule-main/Web_schedule-main/teacher_courses.csv')
        df_ai_in = pd.read_csv('Web_schedule-main/Web_schedule-main/ai_in_courses.csv')
        df_cy_in = pd.read_csv('Web_schedule-main/Web_schedule-main/cy_in_courses.csv')
        all_teacher = pd.read_csv('Web_schedule-main/Web_schedule-main/all_teachers.csv')
        
        df_ai_out = pd.read_csv('Web_schedule-main/Web_schedule-main/ai_out_courses.csv')
        df_cy_out = pd.read_csv('Web_schedule-main/Web_schedule-main/cy_out_courses.csv')
        
        room_list = df_room.to_dict('records')
        room_list.append({'room': 'Online', 'capacity': 9999, 'type': 'virtual'})
    except FileNotFoundError as e:
        st.error(f"❌ Error: Missing CSV file. Details: {e}")
        return None, None

    # --- Data Cleaning & Prep ---
    df_teacher_courses.columns = df_teacher_courses.columns.str.strip()
    df_ai_in.columns = df_ai_in.columns.str.strip()
    df_cy_in.columns = df_cy_in.columns.str.strip()
    progress_bar = st.progress(0)
    progress_bar.progress(10)
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

    # Teacher Unavailability
    all_teacher['teacher_id'] = all_teacher['teacher_id'].astype(str).str.strip()
    all_teacher['unavailable_times'] = all_teacher['teacher_id'].apply(lambda x: None)
    
    TEACHER_UNAVAILABLE_SLOTS = {}
    for index, row in all_teacher.iterrows():
        parsed = parse_unavailable_time(row['unavailable_times'])
        TEACHER_UNAVAILABLE_SLOTS[row['teacher_id']] = parsed

    # Fixed Schedule Logic
    FIXED_FILE_NAMES = ['ai_out_courses.csv', 'cy_out_courses.csv']
    fixed_schedule = []
    for file_name, df_fixed in zip(FIXED_FILE_NAMES, [df_ai_out, df_cy_out]):
        for index, row in df_fixed.iterrows():
             try:
                day_str = str(row['day']).strip()[:3]
                course_code = str(row['course_code']).strip()
                sec_str = str(row['section']).strip()
                if not sec_str or not sec_str.isdigit(): continue
                sec = int(sec_str)
                room = str(row['room']).strip()
                start_time = str(row['start']).strip()
                lec_h = row['lecture_hour'] if not pd.isna(row['lecture_hour']) else 0
                lab_h = row['lab_hour'] if not pd.isna(row['lab_hour']) else 0
                
                if lec_h > 0:
                    duration = int(math.ceil(lec_h * 2))
                    fixed_schedule.append({'course': course_code, 'sec': sec, 'type': 'Lec', 'room': room, 'day': day_str, 'start': start_time, 'duration': duration})
                if lab_h > 0:
                    duration = int(math.ceil(lab_h * 2))
                    fixed_schedule.append({'course': course_code, 'sec': sec, 'type': 'Lab', 'room': room, 'day': day_str, 'start': start_time, 'duration': duration})
             except Exception: continue

    # Task Preparation
    tasks = []
    MAX_LEC_SESSION_SLOTS = 6
    course_optional_map = df_courses.set_index(['course_code', 'section'])['optional'].to_dict()

    for lock in fixed_schedule:
        uid = f"{lock['course']}_S{lock['sec']}_{lock['type']}"
        course_match = df_courses[(df_courses['course_code'] == lock['course']) & (df_courses['section'] == lock['sec'])]
        is_online_lec = course_match['lec_online'].iloc[0] == 1 if not course_match.empty else False
        is_online_lab = course_match['lab_online'].iloc[0] == 1 if not course_match.empty else False
        is_task_online = is_online_lec if lock['type'] == 'Lec' else is_online_lab
        optional_val = course_optional_map.get((lock['course'], lock['sec']), 1)
        tasks.append({
            'uid': uid, 'id': lock['course'], 'sec': lock['sec'], 'type': lock['type'],
            'dur': lock['duration'], 'std': course_match['enrollment_count'].iloc[0] if not course_match.empty else 50,
            'teachers': teacher_map.get(lock['course'], ['External_Faculty']),
            'is_online': is_task_online, 'is_optional': optional_val, 'fixed_room': True
        })

    for _, row in df_courses.iterrows():
        lec_slots = int(math.ceil(row['lecture_hour'] * 2))
        lab_slots = int(math.ceil(row['lab_hour'] * 2))
        teachers = teacher_map.get(row['course_code'], ['Unknown'])
        
        current_lec_slots = lec_slots
        part = 1
        while current_lec_slots > 0:
            session_dur = min(current_lec_slots, MAX_LEC_SESSION_SLOTS)
            uid = f"{row['course_code']}_S{row['section']}_Lec_P{part}"
            if not any(t['uid'] == uid for t in tasks):
                tasks.append({
                    'uid': uid, 'id': row['course_code'], 'sec': row['section'], 'type': 'Lec',
                    'dur': session_dur, 'std': row['enrollment_count'], 'teachers': teachers,
                    'is_online': (row['lec_online'] == 1), 'is_optional': row['optional']
                })
            current_lec_slots -= session_dur
            part += 1
        
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

    # --- Solver ---
    model = cp_model.CpModel()
    schedule = {}
    is_scheduled = {}
    task_vars = {}
    penalty_vars = []
    objective_terms = []
    
    SCORE_FIXED = 1000000
    SCORE_CORE_COURSE = 1000
    SCORE_ELECTIVE_COURSE = 100
    progress_bar.progress(25)
    st.info(f"Processing {len(tasks)} tasks...")

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
            if t['is_online']:
                if r['room'] != 'Online': continue
            else:
                if r['room'] == 'Online': continue
                if r['capacity'] < t['std']: continue
                if t['type'] == 'Lab' and 'lab' not in r['type']: continue
                if t.get('req_ai', False) and r['room'] != 'lab_ai': continue
                if t.get('req_network', False) and r['room'] != 'lab_network': continue

            for d_idx, day in enumerate(DAYS):
                for s_idx in SLOT_MAP:
                    s_val = SLOT_MAP[s_idx]['val']
                    e_val = s_val + (t['dur'] * 0.5)

                    if SCHEDULE_MODE == 1:
                        if s_val < 9.0 or e_val > 16.0: continue
                    else:
                        if s_idx + t['dur'] > TOTAL_SLOTS: continue
                        if s_val < 9.0 or e_val > 16.0: pass

                    overlaps_lunch = False
                    for i in range(t['dur']):
                        if SLOT_MAP.get(s_idx + i, {}).get('is_lunch', False):
                            overlaps_lunch = True; break
                    if overlaps_lunch: continue

                    teacher_conflict = False
                    for teacher_id in t['teachers']:
                        if teacher_id in ['External_Faculty', 'Unknown']: continue
                        if teacher_id in TEACHER_UNAVAILABLE_SLOTS:
                            unavailable_set = TEACHER_UNAVAILABLE_SLOTS[teacher_id].get(d_idx, set())
                            task_slots = set(range(s_idx, s_idx + t['dur']))
                            if not task_slots.isdisjoint(unavailable_set): teacher_conflict = True; break
                    if teacher_conflict: continue

                    var = model.NewBoolVar(f"{uid}_{r['room']}_{day}_{s_idx}")
                    schedule[(uid, r['room'], d_idx, s_idx)] = var
                    candidates.append(var)
                    model.Add(t_day == d_idx).OnlyEnforceIf(var)
                    model.Add(t_start == s_idx).OnlyEnforceIf(var)

                    if SCHEDULE_MODE == 2 and (s_val < 9.0 or e_val > 16.0):
                        penalty_vars.append(var)

        if not candidates:
            model.Add(is_scheduled[uid] == 0)
        else:
            model.Add(sum(candidates) == 1).OnlyEnforceIf(is_scheduled[uid])
            model.Add(sum(candidates) == 0).OnlyEnforceIf(is_scheduled[uid].Not())

        if 'fixed_room' in t: objective_terms.append(is_scheduled[uid] * SCORE_FIXED)
        elif t.get('is_optional') == 0: objective_terms.append(is_scheduled[uid] * SCORE_CORE_COURSE)
        else: objective_terms.append(is_scheduled[uid] * SCORE_ELECTIVE_COURSE)

    # Conflict Constraints
    for d in range(len(DAYS)):
        for s in SLOT_MAP:
            for r in room_list:
                if r['room'] == 'Online': continue
                active = []
                for t in tasks:
                    for offset in range(t['dur']):
                        if s - offset >= 0:
                            key = (t['uid'], r['room'], d, s - offset)
                            if key in schedule: active.append(schedule[key])
                if active: model.Add(sum(active) <= 1)
            
            all_teachers_set = set(tea for t in tasks for tea in t['teachers'] if tea != 'Unknown')
            for tea in all_teachers_set:
                active = []
                for t in tasks:
                    if tea in t['teachers']:
                        for r in room_list:
                             for offset in range(t['dur']):
                                if s - offset >= 0:
                                    key = (t['uid'], r['room'], d, s - offset)
                                    if key in schedule: active.append(schedule[key])
                if active: model.Add(sum(active) <= 1)

    model.Maximize(sum(objective_terms) - sum(penalty_vars))
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 4
    solver.parameters.max_time_in_seconds = 120
    progress_bar.progress(50)
    status = solver.Solve(model)
    progress_bar.progress(100)

    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        results = []
        unscheduled = []

        for t in tasks:
            uid = t['uid']
            if uid in is_scheduled and solver.Value(is_scheduled[uid]):
                d = solver.Value(task_vars[uid]['day'])
                s = solver.Value(task_vars[uid]['start'])
                dur = t['dur']
                r_name = "Unknown"
                
                for (tid, r, d_idx, s_idx), var in schedule.items():
                    if tid == uid and d_idx == d and s_idx == s and solver.Value(var):
                        r_name = r
                        break
                
                results.append({
                    'Day': DAYS[d], 
                    'Start': SLOT_MAP[s]['time'], 
                    'End': SLOT_MAP.get(s + dur, {'time': '19:00'})['time'],
                    'Room': r_name, 
                    'Course': t['id'], 
                    'Sec': t['sec'], 
                    'Type': t['type'],
                    'Teacher': ",".join(t['teachers'])
                })
            else:
                unscheduled.append({
                    'Course': t['id'], 
                    'Sec': t['sec'], 
                    'Reason': 'Constraint/Penalty'
                })
        
        return results, unscheduled
    else:
        return None, None

# ==========================================
# ส่วนควบคุมหลัก (Controller)
# ==========================================

# 1. เมื่อกดปุ่ม Run -> คำนวณและเก็บลง Session State
if run_button:
    with st.spinner("Calculating schedule... please wait"):
        res_list, un_list = calculate_schedule()
        
        if res_list is not None:
            # เก็บข้อมูลลง session_state
            st.session_state['schedule_results'] = pd.DataFrame(res_list)
            st.session_state['unscheduled_results'] = un_list if un_list else []
            st.session_state['has_run'] = True
            st.success("✅ Schedule calculation complete!")
        else:
            st.error("❌ Cannot schedule in current mode (Constraints too strict).")

# 2. ส่วนแสดงผล (ทำงานเมื่อมีข้อมูลใน Session State)
if st.session_state.get('has_run', False):
    df_res = st.session_state['schedule_results']
    unscheduled = st.session_state['unscheduled_results']
    
    if df_res.empty:
         st.warning("⚠️ Solver found a solution, but NO classes were scheduled.")
    else:
        # Sort Data
        day_order = {'Mon': 0, 'Tue': 1, 'Wed': 2, 'Thu': 3, 'Fri': 4}
        df_res['DayIdx'] = df_res['Day'].map(day_order)
        df_res = df_res.sort_values(by=['DayIdx', 'Start'])

        st.divider()
        st.header("🏫 Room Schedules (ตารางเรียนรายห้อง)")

        # --- Selectbox อยู่ข้างนอก if button แล้ว (ใช้ข้อมูลจาก session_state) ---
        all_rooms = sorted(df_res['Room'].unique())
        selected_room = st.selectbox("🔍 Select Room (เลือกห้องเรียน):", all_rooms)

# ฟังก์ชันสร้างตารางเรียนแบบ Grid (ฉบับแก้ไข: รองรับเศษนาที)
        def create_timetable_grid(df, room_name):
            # 1. กำหนดช่วงเวลา (Slots) ให้เป็นตัวเลขทศนิยมเพื่อการเปรียบเทียบ
            # เช่น 08:00-09:00 คือ start=8.0, end=9.0
            slots = []
            for h in range(8, 20): 
                if h < 19:
                    slots.append({
                        "label": f"{h:02d}:00-{h+1:02d}:00",
                        "start": float(h),
                        "end": float(h+1)
                    })
            
            # 2. สร้าง DataFrame ว่างๆ โดยใช้ Label เป็นชื่อคอลัมน์
            col_names = [s['label'] for s in slots]
            days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
            df_grid = pd.DataFrame('', index=days, columns=col_names)

            # ดึงข้อมูลเฉพาะห้องที่เลือก
            room_df = df[df['Room'] == room_name]

            for _, row in room_df.iterrows():
                # 3. แปลงเวลาเริ่ม-จบ เป็นตัวเลขทศนิยม (เช่น 09:30 -> 9.5)
                try:
                    s_parts = row['Start'].split(':')
                    e_parts = row['End'].split(':')
                    # ชั่วโมง + (นาที / 60)
                    start_val = int(s_parts[0]) + (int(s_parts[1]) / 60.0)
                    end_val = int(e_parts[0]) + (int(e_parts[1]) / 60.0)
                except:
                    continue # ข้ามถ้าเวลาผิดพลาด format
                
                # ข้อความที่จะแสดงในช่อง (เพิ่มเวลาเข้าไปด้วย เพื่อความชัดเจน)
                # เช่น "(09:30) LI101002"
                short_start = f"{int(s_parts[0]):02d}:{int(s_parts[1]):02d}"
                course_info = f"({short_start}) {row['Course']} ({row['Type']}) Sec {row['Sec']}"

                # 4. วนลูปเช็คทุกช่อง (Slot) ว่าวิชานี้คาบเกี่ยวหรือไม่
                for s in slots:
                    # Logic การเช็ค Overlap: max(start1, start2) < min(end1, end2)
                    # แปลว่า: ถ้าเวลาเริ่มวิชา น้อยกว่า เวลาจบช่อง AND เวลาจบวิชา มากกว่า เวลาเริ่มช่อง
                    if max(start_val, s['start']) < min(end_val, s['end']):
                        
                        col_name = s['label']
                        
                        # ถ้าช่องนั้นว่าง ให้ใส่ข้อมูลเลย
                        if df_grid.at[row['Day'], col_name] == '':
                            df_grid.at[row['Day'], col_name] = course_info
                        else:
                            # ถ้ามีข้อมูลอยู่แล้ว (และไม่ใช่ข้อความเดิม) ให้คั่นด้วย /
                            if course_info not in df_grid.at[row['Day'], col_name]:
                                df_grid.at[row['Day'], col_name] += ' / ' + course_info

            return df_grid

        if selected_room:
            st.subheader(f"📍 Timetable for: {selected_room}")
            grid_df = create_timetable_grid(df_res, selected_room)
            st.dataframe(grid_df, use_container_width=True, height=250)

            st.caption("📄 Detailed List")
            room_details = df_res[df_res['Room'] == selected_room][['Day', 'Start', 'End', 'Course', 'Sec', 'Type', 'Teacher']]
            st.dataframe(room_details, use_container_width=True, hide_index=True)

        st.divider()
        csv = df_res.to_csv(index=False).encode('utf-8')
        st.download_button("📥 Download Full Schedule CSV", data=csv, file_name=f"full_schedule.csv", mime="text/csv")
    
    if unscheduled:
        st.divider()
        st.warning(f"⚠️ Unscheduled Tasks ({len(unscheduled)})")
        st.dataframe(pd.DataFrame(unscheduled))
