import pandas as pd
from ortools.sat.python import cp_model
import math
import re
from collections import defaultdict

def get_slot_map():
    SLOT_MAP = {}
    t_start = 8.5
    idx = 0
    LUNCH_START, LUNCH_END = 12.5, 13.0
    while t_start < 19.0:
        hour, minute = int(t_start), int((t_start - int(t_start)) * 60)
        time_str = f"{hour:02d}:{minute:02d}"
        SLOT_MAP[idx] = {
            'time': time_str, 'val': t_start,
            'is_lunch': (t_start >= LUNCH_START and t_start < LUNCH_END)
        }
        idx += 1; t_start += 0.5
    return SLOT_MAP

def time_to_slot_index(time_str, slot_to_index):
    time_str = str(time_str).strip()
    match = re.search(r"(\d{1,2})[:.](\d{2})", time_str)
    if match:
        h, m = match.groups()
        time_str = f"{int(h):02d}:{int(m):02d}"
        return slot_to_index.get(time_str, -1)
    return -1

def parse_unavailable_time(unavailable_input, days_list, slot_to_index):
    unavailable_slots = {d_idx: set() for d_idx in range(len(days_list))}
    if not unavailable_input: return unavailable_slots
    target_list = unavailable_input if isinstance(unavailable_input, list) else [str(unavailable_input)]

    for item in target_list:
        ut_str = str(item).replace('[', '').replace(']', '').replace("'", "").replace('"', "")
        match = re.search(r"(\w{3})\s+(\d{1,2}[:.]\d{2})-(\d{1,2}[:.]\d{2})", ut_str)
        if not match: continue

        day_abbr, start_time_str, end_time_str = match.groups()
        try:
            day_idx = days_list.index(day_abbr)
            start_slot = time_to_slot_index(start_time_str.replace('.', ':'), slot_to_index)
            end_slot = time_to_slot_index(end_time_str.replace('.', ':'), slot_to_index)
            if start_slot != -1 and end_slot != -1:
                for slot in range(start_slot, end_slot):
                    unavailable_slots[day_idx].add(slot)
        except ValueError: continue
    return unavailable_slots

def run_solver_logic(data_dict, schedule_mode):
    # ดึง DataFrames ออกมา
    df_room = data_dict['room']
    df_courses = data_dict['courses']
    df_tc = data_dict['teacher_courses']
    all_teacher = data_dict['all_teacher']
    fixed_schedule_data = data_dict['fixed_schedule']

    SLOT_MAP = get_slot_map()
    DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
    SLOT_TO_INDEX = {v['time']: k for k, v in SLOT_MAP.items()}
    TOTAL_SLOTS = len(SLOT_MAP)

    # เตรียมรายชื่อห้อง
    room_list = df_room.to_dict('records')
    room_list.append({'room': 'Online', 'capacity': 9999, 'type': 'virtual'})

    # เตรียมแผนผังอาจารย์
    teacher_map = df_tc.groupby('course_code')['teacher_id'].apply(lambda x: [str(i).strip() for i in x]).to_dict()
    teacher_unavail = {str(row['teacher_id']): parse_unavailable_time(row.get('unavailable_times'), DAYS, SLOT_TO_INDEX) 
                       for _, row in all_teacher.iterrows()}

    # เตรียมงาน (Tasks)
    tasks = []
    MAX_LEC_SESSION_SLOTS = 6
    
    # เพิ่มวิชาที่ Fix ไว้ก่อน
    for lock in fixed_schedule_data:
        tasks.append({
            'uid': f"{lock['course']}_S{lock['sec']}_{lock['type']}",
            'id': lock['course'], 'sec': lock['sec'], 'type': lock['type'],
            'dur': lock['duration'], 'std': 50,
            'teachers': teacher_map.get(lock['course'], ['External']),
            'is_online': False, 'fixed_room': True, 'target_room': lock['room']
        })

    # เพิ่มวิชาปกติ
    for _, row in df_courses.iterrows():
        c_code, sec = str(row['course_code']), int(row['section'])
        teachers = teacher_map.get(c_code, ['Unknown'])
        
        # Lec split logic
        lec_h = int(math.ceil(row['lecture_hour'] * 2))
        part = 1
        while lec_h > 0:
            dur = min(lec_h, MAX_LEC_SESSION_SLOTS)
            uid = f"{c_code}_S{sec}_Lec_P{part}"
            if not any(t['uid'] == uid for t in tasks):
                tasks.append({
                    'uid': uid, 'id': c_code, 'sec': sec, 'type': 'Lec',
                    'dur': dur, 'std': row['enrollment_count'], 'teachers': teachers,
                    'is_online': (row['lec_online'] == 1)
                })
            lec_h -= dur; part += 1
            
        # Lab
        lab_h = int(math.ceil(row['lab_hour'] * 2))
        if lab_h > 0:
            uid = f"{c_code}_S{sec}_Lab"
            if not any(t['uid'] == uid for t in tasks):
                tasks.append({
                    'uid': uid, 'id': c_code, 'sec': sec, 'type': 'Lab',
                    'dur': lab_h, 'std': row['enrollment_count'], 'teachers': teachers,
                    'is_online': (row['lab_online'] == 1),
                    'req_ai': (row.get('require_lab_ai', 0) == 1)
                })

    # สร้าง Model OR-Tools
    model = cp_model.CpModel()
    schedule_vars = {}
    is_scheduled = {}
    task_vars = {}
    penalty_vars = []
    objective_terms = []

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
            if t['is_online'] and r['room'] != 'Online': continue
            if not t['is_online'] and r['room'] == 'Online': continue
            if r['capacity'] < t['std']: continue
            if t.get('fixed_room') and r['room'] != t['target_room']: continue
            if t.get('req_ai') and r['room'] != 'lab_ai': continue

            for d_idx in range(len(DAYS)):
                for s_idx in range(TOTAL_SLOTS - t['dur'] + 1):
                    s_val = SLOT_MAP[s_idx]['val']
                    e_val = s_val + (t['dur'] * 0.5)

                    if schedule_mode == 1 and (s_val < 9.0 or e_val > 16.0): continue
                    if any(SLOT_MAP[s_idx + i]['is_lunch'] for i in range(t['dur'])): continue
                    
                    # เช็คอาจารย์ว่าง
                    t_conflict = False
                    for tid in t['teachers']:
                        if tid in teacher_unavail and not set(range(s_idx, s_idx + t['dur'])).isdisjoint(teacher_unavail[tid][d_idx]):
                            t_conflict = True; break
                    if t_conflict: continue

                    var = model.NewBoolVar(f"{uid}_{r['room']}_{d_idx}_{s_idx}")
                    schedule_vars[(uid, r['room'], d_idx, s_idx)] = var
                    candidates.append(var)
                    model.Add(t_day == d_idx).OnlyEnforceIf(var)
                    model.Add(t_start == s_idx).OnlyEnforceIf(var)
                    if schedule_mode == 2 and (s_val < 9.0 or e_val > 16.0): penalty_vars.append(var)

        if not candidates:
            model.Add(is_scheduled[uid] == 0)
        else:
            model.Add(sum(candidates) == 1).OnlyEnforceIf(is_scheduled[uid])
            model.Add(sum(candidates) == 0).OnlyEnforceIf(is_scheduled[uid].Not())
        
        objective_terms.append(is_scheduled[uid] * (1000 if not t.get('is_optional') else 100))

    # Overlap Constraints (Simplified)
    for d in range(len(DAYS)):
        for s in range(TOTAL_SLOTS):
            for r in [rm['room'] for rm in room_list if rm['room'] != 'Online']:
                active = [v for k, v in schedule_vars.items() if k[1] == r and k[2] == d and k[3] <= s < k[3] + next(tk['dur'] for tk in tasks if tk['uid'] == k[0])]
                if active: model.Add(sum(active) <= 1)

    model.Maximize(sum(objective_terms) - sum(penalty_vars))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 60
    status = solver.Solve(model)

    # รวบรวมผลลัพธ์
    results = []
    unscheduled = []
    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        for t in tasks:
            uid = t['uid']
            if solver.Value(is_scheduled[uid]):
                d, s = solver.Value(task_vars[uid]['day']), solver.Value(task_vars[uid]['start'])
                rm = next(k[1] for k, v in schedule_vars.items() if k[0] == uid and k[2] == d and k[3] == s and solver.Value(v))
                results.append({
                    'Day': DAYS[d], 'Start': SLOT_MAP[s]['time'], 'End': SLOT_MAP.get(s + t['dur'], {'time': '19:00'})['time'],
                    'Room': rm, 'Course': t['id'], 'Sec': t['sec'], 'Type': t['type'], 'Teacher': ",".join(t['teachers'])
                })
            else:
                unsched_reason = "Constraint/Time Limit"
                unscheduled.append({'Course': t['id'], 'Sec': t['sec'], 'Reason': unsched_reason})
        return results, unscheduled
    return None, None
