from ortools.sat.python import cp_model
import math
import pandas as pd
from collections import defaultdict

def solve_timetable(data, mode):
    SLOT_MAP = {}
    t_start = 8.5 # Start at 8:30 for consistent indexing, filtered later.
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
    
    # Define SLOT_TO_INDEX using the created SLOT_MAP
    SLOT_TO_INDEX = {v['time']: k for k, v in SLOT_MAP.items()}
    
    # Define time conversion functions here, after SLOT_MAP and SLOT_TO_INDEX are ready.
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
            # Corrected regex to ensure 3 capturing groups for day, start time string, and end time string
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
    
    # ========================================== # Data Loading and Cleaning
    # 1. Data Loading and Cleaning
    # ==========================================
    # Using try-except for robust file loading
    try:
        # Using pre-loaded dataframes from kernel state
        # df_room = pd.read_csv('room.csv') # No need to read again
        # Initialize room_list here, immediately after df_room is loaded
        room_list = df_room.to_dict('records')
        room_list.append({'room': 'Online', 'capacity': 9999, 'type': 'virtual'}) # Add Online room here
    
        # df_teacher_courses = pd.read_csv('teacher_courses.csv') # No need to read again
        # df_ai = pd.read_csv('ai_courses.csv') # No need to read again
        # df_cy = pd.read_csv('cy_in_courses.csv') # No need to read again
        # all_teacher = pd.read_csv('all_teachers.csv') # No need to read again
    
        # FIXED_FILE_NAMES also need these files
        # df_ai_out = pd.read_csv('Ai_out.csv') # No need to read again
        # df_cy_out = pd.read_csv('cy_0ut_courses.csv') # No need to read again
    except NameError as e:
        print(f"❌ Error: Required dataframes not found: {e}. \n   Please ensure previous cells have been executed to load data.")
        exit() # Exit if critical files are missing
    
    # Clean Data
    df_teacher_courses.columns = df_teacher_courses.columns.str.strip()
    df_ai_in.columns = df_ai_in.columns.str.strip()
    df_cy_in.columns = df_cy_in.columns.str.strip()
    
    df_courses = pd.concat([df_ai_in, df_cy_in], ignore_index=True)
    if 'lec_online' not in df_courses.columns: df_courses['lec_online'] = 0
    if 'lab_online' not in df_courses.columns: df_courses['lab_online'] = 0
    # Ensure 'optional' column is present, defaulting to 1 if missing
    if 'optional' not in df_courses.columns: df_courses['optional'] = 1
    df_courses = df_courses.fillna(0)
    
    df_teacher_courses['course_code'] = df_teacher_courses['course_code'].astype(str).str.strip()
    df_courses['course_code'] = df_courses['course_code'].astype(str).str.strip()
    
    teacher_map = {}
    for _, row in df_teacher_courses.iterrows(): # Use df_teacher_courses here
        c_code = row['course_code']
        t_id = str(row['teacher_id']).strip()
        if c_code not in teacher_map: teacher_map[c_code] = []
        teacher_map[c_code].append(t_id)
    
    # --- Teacher Unavailability Setup (copied from V3/V4) ---
    all_teacher['teacher_id'] = all_teacher['teacher_id'].astype(str).str.strip()
    all_teacher['unavailable_times'] = all_teacher['teacher_id'].apply(lambda x: None)
    
    def set_unavailable(tid, time_list):
        idx = all_teacher.index[all_teacher['teacher_id'] == tid].tolist()
        if idx:
            all_teacher.at[idx[0], 'unavailable_times'] = time_list
    
    # set_unavailable('BB1', ["Mon 10:00-12:00"])
    # set_unavailable('HH1', ["Mon 10:00-12:00"])
    # set_unavailable('K1', ['Fri 15:30-18:30'])
    # set_unavailable('V1', ['Wed 08:30-10:30'])
    
    print("--- Debug: Parsed Unavailable Time Slots ---")
    TEACHER_UNAVAILABLE_SLOTS = {}
    for index, row in all_teacher.iterrows():
        parsed = parse_unavailable_time(row['unavailable_times'])
        TEACHER_UNAVAILABLE_SLOTS[row['teacher_id']] = parsed
        has_unavailable = any(len(s) > 0 for s in parsed.values())
        if has_unavailable:
            print(f"Teacher {row['teacher_id']}: {row['unavailable_times']} -> Slots: {parsed}")
    print("-" * 50)
    
    # --- Fixed Schedule Loading (copied from V3/V4) ---
    FIXED_FILE_NAMES = ['ai_out_courses.csv', 'cy_out_courses.csv']
    fixed_schedule = []
    required_cols = ['course_code', 'section', 'day', 'start', 'room', 'lecture_hour', 'lab_hour']
    
    for file_name in FIXED_FILE_NAMES:
        try:
            # Load df_fixed from the dataframes already loaded from gdown
            if file_name == 'ai_out_courses.csv':
                df_fixed = df_ai_out
            elif file_name == 'cy_out_courses.csv':
                df_fixed = df_cy_out
            else:
                # This case should ideally not be reached if FIXED_FILE_NAMES is exhaustive for pre-loaded DFs
                df_fixed = pd.read_csv(file_name) # Fallback for other files if any
    
            if not all(col in df_fixed.columns for col in required_cols):
                 print(f"--- Warning: File {file_name} is missing required columns ({required_cols}).")
                 continue
            print(f"Found required fixed schedule data in file {file_name}: {len(df_fixed)} items")
    
            for index, row in df_fixed.iterrows():
                try:
                    day_str = str(row['day']).strip()[:3]
                    course_code = str(row['course_code']).strip()
    
                    sec_str = str(row['section']).strip()
                    if not sec_str or not sec_str.isdigit():
                        if not pd.isna(row['section']): pass
                        continue
                    sec = int(sec_str)
    
                    room = str(row['room']).strip()
                    start_time = str(row['start']).strip()
                    lec_h = row['lecture_hour'] if not pd.isna(row['lecture_hour']) else 0
                    lab_h = row['lab_hour'] if not pd.isna(row['lab_hour']) else 0
    
                    if lec_h > 0 or lab_h > 0:
    
                        if lec_h > 0:
                            duration = int(math.ceil(lec_h * 2))
                            fixed_schedule.append({
                                'course': course_code, 'sec': sec, 'type': 'Lec',
                                'room': room, 'day': day_str, 'start': start_time, 'duration': duration
                            })
    
                        if lab_h > 0:
                            duration = int(math.ceil(lab_h * 2))
                            fixed_schedule.append({
                                'course': course_code, 'sec': sec, 'type': 'Lab',
                                'room': room, 'day': day_str, 'start': start_time, 'duration': duration
                            })
    
                except Exception as e:
                    print(f"--- Warning: Error in fixed schedule data (row {index+1}, {file_name}): {row.to_dict()} (Error: {e})")
                    continue
    
        except NameError:
            print(f"--- Warning: Fixed Schedule dataframe ({file_name}) not found. Skipping fixed schedule processing for this file.")
        except Exception as e:
            print(f"--- Warning: Error processing fixed schedule in file {file_name}: {e}")
    
    # ========================================== # Task Preparation
    # 3. Prepare Tasks (Supports Split Lec / Online)
    # ==========================================
    tasks = []
    MAX_LEC_SESSION_SLOTS = 6
    
    # Create course_optional_map
    course_optional_map = df_courses.set_index(['course_code', 'section'])['optional'].to_dict()
    
    for lock in fixed_schedule:
        uid = f"{lock['course']}_S{lock['sec']}_{lock['type']}"
        # Retrieve is_online status from df_courses based on course_code and section
        course_match = df_courses[(df_courses['course_code'] == lock['course']) & (df_courses['section'] == lock['sec'])]
        is_online_lec = course_match['lec_online'].iloc[0] == 1 if not course_match.empty else False
        is_online_lab = course_match['lab_online'].iloc[0] == 1 if not course_match.empty else False
    
        # Determine online status based on task type
        is_task_online = is_online_lec if lock['type'] == 'Lec' else is_online_lab
    
        # Retrieve is_optional status for fixed tasks
        optional_val = course_optional_map.get((lock['course'], lock['sec']), 1)
    
        tasks.append({
            'uid': uid,
            'id': lock['course'],
            'sec': lock['sec'],
            'type': lock['type'],
            'dur': lock['duration'],
            'std': course_match['enrollment_count'].iloc[0] if not course_match.empty else 50, # Use enrollment from df_courses
            'teachers': teacher_map.get(lock['course'], ['External_Faculty']),
            'is_online': is_task_online,
            'is_optional': optional_val, # Add is_optional here
            'fixed_room': True
        })
    
    
    for _, row in df_courses.iterrows():
        lec_slots = int(math.ceil(row['lecture_hour'] * 2))
        lab_slots = int(math.ceil(row['lab_hour'] * 2))
        is_lec_online = (row['lec_online'] == 1)
        is_lab_online = (row['lab_online'] == 1)
        teachers = teacher_map.get(row['course_code'], ['Unknown'])
        is_optional = row['optional'] # Get is_optional directly from row
    
        # Split Lecture
        current_lec_slots = lec_slots
        part = 1
        while current_lec_slots > 0:
            session_dur = min(current_lec_slots, MAX_LEC_SESSION_SLOTS)
            uid = f"{row['course_code']}_S{row['section']}_Lec_P{part}"
            # Check if this task is already in fixed_schedule
            if not any(t['uid'] == uid for t in tasks): # Avoid duplicating fixed tasks
                tasks.append({
                    'uid': uid,
                    'id': row['course_code'], 'sec': row['section'], 'type': 'Lec',
                    'dur': session_dur, 'std': row['enrollment_count'], 'teachers': teachers,
                    'is_online': is_lec_online,
                    'is_optional': is_optional # Add is_optional here
                })
            current_lec_slots -= session_dur
            part += 1
        req_ai = (row.get('require_lab_ai', 0) == 1)
        req_network = (row.get('require_lab_network', 0) == 1)
        if lab_slots > 0:
            uid = f"{row['course_code']}_S{row['section']}_Lab"
            # Check if this task is already in fixed_schedule
            if not any(t['uid'] == uid for t in tasks):
                tasks.append({
                    'uid': uid,
                    'id': row['course_code'], 'sec': row['section'], 'type': 'Lab',
                    'dur': lab_slots, 'std': row['enrollment_count'], 'teachers': teachers,
                    'is_online': is_lab_online,
                    'is_optional': is_optional, # Add is_optional here
                    'req_ai': req_ai,
                    'req_network': req_network
                })
    
    # ========================================== # Model Creation
    # 4. Create the Model
    # ==========================================
    model = cp_model.CpModel()
    schedule = {}
    is_scheduled = {}
    task_vars = {}
    penalty_vars = [] # Initialize as list
    lec_lab_penalties = [] # Initialize as list
    objective_terms = [] # Initialize objective_terms here
    
    # Define constants for objective scores
    SCORE_FIXED = 1000000  # High score for fixed tasks to ensure they are prioritized
    SCORE_CORE_COURSE = 1000 # Score for non-optional courses
    SCORE_ELECTIVE_COURSE = 100 # Score for optional courses
    
    print(f"Processing schedule for: {len(tasks)} items...")
    
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
                # ถ้าวิชาต้องการ Lab AI แต่ห้องนี้ไม่ใช่ lab_ai -> ข้าม
                if t.get('req_ai', False) and r['room'] != 'lab_ai': continue
                # ถ้าวิชาต้องการ Lab Network แต่ห้องนี้ไม่ใช่ lab_network -> ข้าม
                if t.get('req_network', False) and r['room'] != 'lab_network': continue
    
            for d_idx, day in enumerate(DAYS):
                for s_idx in SLOT_MAP:
    
                    # --- Conditions based on selected MODE ---
                    s_val = SLOT_MAP[s_idx]['val']
                    e_val = s_val + (t['dur'] * 0.5)
    
                    if SCHEDULE_MODE == 1:
                        # Mode 1: Enforce 09:00 - 16:00 (Hard Constraint)
                        if s_val < 9.0 or e_val > 16.0: continue
                    else:
                        # Mode 2: 08:30 - 19:00 (soft constraint/penalty if outside 9-16)
                        if s_idx + t['dur'] > TOTAL_SLOTS: continue # Cannot exceed 19:00
                        if s_val < 9.0 or e_val > 16.0:
                             # In Mode 2, allow but add penalty to prioritize normal hours.
                             pass
    
                    # Hard Constraint: No overlapping with lunch (12:30-13:00)
                    overlaps_lunch = False
                    for i in range(t['dur']):
                        if SLOT_MAP.get(s_idx + i, {}).get('is_lunch', False):
                            overlaps_lunch = True
                            break
                    if overlaps_lunch: continue
                    # --- [แทรกตรงนี้] เช็คเวลาว่างอาจารย์ (Teacher Unavailability) ---
                    teacher_conflict = False
                    for teacher_id in t['teachers']:
                        # ข้าม External Faculty หรือ Unknown
                        if teacher_id in ['External_Faculty', 'Unknown']: continue
    
                        # ถ้าอาจารย์มี list เวลาที่ไม่ว่าง
                        if teacher_id in TEACHER_UNAVAILABLE_SLOTS:
                            unavailable_set = TEACHER_UNAVAILABLE_SLOTS[teacher_id].get(d_idx, set())
                            # ตรวจสอบช่วงเวลาที่วิชาจะลง (ตั้งแต่ s_idx ยาวไปตาม duration)
                            task_slots = set(range(s_idx, s_idx + t['dur']))
    
                            # ถ้ามีเวลาซ้อนทับกัน (Intersection ไม่ว่างเปล่า)
                            if not task_slots.isdisjoint(unavailable_set):
                                teacher_conflict = True
                                break
                    if teacher_conflict: continue
                    # Create Variable
                    var = model.NewBoolVar(f"{uid}_{r['room']}_{day}_{s_idx}")
                    schedule[(uid, r['room'], d_idx, s_idx)] = var
                    candidates.append(var)
    
                    model.Add(t_day == d_idx).OnlyEnforceIf(var)
                    model.Add(t_start == s_idx).OnlyEnforceIf(var)
    
                    # Mode 2 Penalty (if outside 9-16)
                    if SCHEDULE_MODE == 2:
                        if s_val < 9.0 or e_val > 16.0:
                            penalty_vars.append(var)
    
        if not candidates:
            print(f"--- Warning: Task {uid} has no valid placement (e.g., due to strict time constraints in Mode {SCHEDULE_MODE}).")
            model.Add(is_scheduled[uid] == 0)
        else:
            model.Add(sum(candidates) == 1).OnlyEnforceIf(is_scheduled[uid])
            model.Add(sum(candidates) == 0).OnlyEnforceIf(is_scheduled[uid].Not())
    
        # Objective scoring based on task type and optional status
        is_fixed_task = 'fixed_room' in t # Check if it's a fixed task based on the key
        if is_fixed_task:
            objective_terms.append(is_scheduled[uid] * SCORE_FIXED)
        else:
            if t.get('is_optional') == 0: # Check is_optional from task dictionary
                objective_terms.append(is_scheduled[uid] * SCORE_CORE_COURSE)
            else:
                objective_terms.append(is_scheduled[uid] * SCORE_ELECTIVE_COURSE)
    
    
    
    # Conflicts
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
    
            all_teachers = set(tea for t in tasks for tea in t['teachers'] if tea != 'Unknown')
            for tea in all_teachers:
                active = []
                for t in tasks:
                    if tea in t['teachers']:
                        for r in room_list:
                            for offset in range(t['dur']):
                                if s - offset >= 0:
                                    key = (t['uid'], r['room'], d, s - offset)
                                    if key in schedule: active.append(schedule[key])
                if active: model.Add(sum(active) <= 1)
    
    # Lec/Lab Order
    lec_tasks = {t['uid']: t for t in tasks if t['type'] == 'Lec'}
    lab_tasks = {t['uid']: t for t in tasks if t['type'] == 'Lab'}
    
    course_section_map = defaultdict(lambda: {'Lec': [], 'Lab': []})
    for uid in lec_tasks:
        base_id = '_'.join(uid.split('_')[:2])
        course_section_map[base_id]['Lec'].append(uid)
    for uid in lab_tasks:
        base_id = uid.split('_Lab')[0]
        course_section_map[base_id]['Lab'].append(uid)
    
    for base_id, task_lists in course_section_map.items():
        if task_lists['Lec'] and task_lists['Lab']:
            for lec_uid in task_lists['Lec']:
                for lab_uid in task_lists['Lab']:
                    if lec_uid not in is_scheduled or lab_uid not in is_scheduled: continue
    
                    both = model.NewBoolVar(f"both_{lec_uid}_{lab_uid}")
                    model.AddBoolAnd([is_scheduled[lec_uid], is_scheduled[lab_uid]]).OnlyEnforceIf(both)
    
                    wrong_day = model.NewBoolVar(f"wd_{lec_uid}_{lab_uid}")
                    model.Add(task_vars[lec_uid]['day'] > task_vars[lab_uid]['day']).OnlyEnforceIf([both, wrong_day])
                    lec_lab_penalties.append(wrong_day)
    
                    same_day = model.NewBoolVar(f"sd_{lec_uid}_{lab_uid}")
                    model.Add(task_vars[lec_uid]['day'] == task_vars[lab_uid]['day']).OnlyEnforceIf([both, same_day])
                    wrong_time = model.NewBoolVar(f"wt_{lec_uid}_{lab_uid}")
                    model.Add(task_vars[lec_uid]['end'] > task_vars[lab_uid]['start']).OnlyEnforceIf([both, same_day, wrong_time])
                    lec_lab_penalties.append(wrong_time)
    
    # Objective
    model.Maximize(sum(objective_terms) - sum(penalty_vars) - (sum(lec_lab_penalties) * 10))
    
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 120
    status = solver.Solve(model)
    
    # Display Results
    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        print(f"✅ Schedule successful! (Status: {solver.StatusName(status)})")
        results = []
        unscheduled_tasks = []
    
        # Iterate through all tasks (including fixed) to identify scheduled and unscheduled ones
        for t in tasks:
            uid = t['uid']
            if uid in is_scheduled and solver.Value(is_scheduled[uid]):
                d = solver.Value(task_vars[uid]['day'])
                s = solver.Value(task_vars[uid]['start'])
                dur = t['dur']
                r_name = "Unknown"
                for (tid, r, d_idx, s_idx), var in schedule.items():
                    if tid == uid and d_idx == d and s_idx == s and solver.Value(var):
                        r_name = r; break
    
                start_time = SLOT_MAP[s]['time']
                end_time = SLOT_MAP.get(s + dur, {'time': '19:00'})['time']
    
                notes = []
                if t['is_online']: notes.append("Online")
                s_val = SLOT_MAP[s]['val']
                e_val = s_val + (dur * 0.5)
                if s_val < 9.0 or e_val > 16.0: notes.append("Ext.Time") # Only add if Mode 2
    
                results.append({
                    'Day': DAYS[d], 'Start': start_time, 'End': end_time, 'Room': r_name,
                    'Course': t['id'], 'Sec': t['sec'], 'Type': t['type'],
                    'Teacher': ",".join(t['teachers']), 'Note': ", ".join(notes)
                })
            else:
                # For unscheduled tasks, including the ones filtered by candidates loop
                unscheduled_tasks.append({
                    'Course': t['id'],
                    'Sec': t['sec'],
                    'Type': t['type'],
                    'Duration (hours)': t['dur'] * 0.5,
                    'Teachers': ",".join(t['teachers']),
                    'Status': f"Unscheduled (Mode {SCHEDULE_MODE} constraints)"
                })
return pd.DataFrame(results)
