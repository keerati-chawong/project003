import pandas as pd
from ortools.sat.python import cp_model
import math
import re
from collections import defaultdict

def get_slot_map():
    slots = {}
    t_start = 8.5
    idx = 0
    while t_start < 19.0:
        h, m = int(t_start), int((t_start % 1) * 60)
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

def calculate_schedule(files, mode, solver_time, penalty_val):
    logs = []
    SLOT_MAP = get_slot_map()
    DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
    SLOT_INV = {v['time']: k for k, v in SLOT_MAP.items()}
    TOTAL_SLOTS = len(SLOT_MAP)

    try:
        # Load & Clean Data
        room_list = pd.read_csv(files['room']).to_dict('records')
        room_list.append({'room': 'Online', 'capacity': 9999, 'type': 'virtual'})
        df_tc = pd.read_csv(files['teacher_courses'])
        df_courses = pd.concat([pd.read_csv(files['ai_in']), pd.read_csv(files['cy_in'])], ignore_index=True).fillna(0)
        df_teacher = pd.read_csv(files['teachers'])

        t_map = defaultdict(list)
        for _, r in df_tc.iterrows(): t_map[str(r['course_code']).strip()].append(str(r['teacher_id']).strip())
        un_map = {str(r['teacher_id']).strip(): parse_unavailable_time(r.get('unavailable_times'), DAYS, SLOT_INV) for _, r in df_teacher.iterrows()}

        # 1. Fixed Schedule
        fixed_tasks = []
        occupied_fixed = defaultdict(lambda: defaultdict(set))
        for key in ['ai_out', 'cy_out']:
            if files[key]:
                df_f = pd.read_csv(files[key])
                for _, r in df_f.iterrows():
                    d_idx = DAYS.index(r['day'][:3]) if r['day'][:3] in DAYS else -1
                    s_idx = SLOT_INV.get(str(r['start']).replace('.', ':'), -1)
                    dur = int(math.ceil((r.get('lecture_hour', 0) + r.get('lab_hour', 0)) * 2))
                    if d_idx != -1 and s_idx != -1:
                        fixed_tasks.append({'id': str(r['course_code']), 'sec': int(r['section']), 'room': str(r['room']), 'd': d_idx, 's': s_idx, 'dur': dur, 'tea': t_map.get(str(r['course_code']), ['-'])})
                        for i in range(dur): occupied_fixed[str(r['room'])][d_idx].add(s_idx + i)

        # 2. Dynamic Tasks Preparation
        tasks = []
        for _, r in df_courses.iterrows():
            c, s = str(r['course_code']).strip(), int(r['section'])
            tea = t_map.get(c, ['Unknown'])
            opt = r.get('optional', 1)
            curr_lec = int(math.ceil(r['lecture_hour'] * 2))
            p = 1
            while curr_lec > 0:
                dur = min(curr_lec, 6)
                tasks.append({'uid': f"{c}_S{s}_Lec_P{part if 'part' in locals() else p}", 'id': c, 'sec': s, 'type': 'Lec', 'dur': dur, 'std': r['enrollment_count'], 'tea': tea, 'opt': opt, 'online': r.get('lec_online')==1})
                curr_lec -= dur; p += 1
            lab_dur = int(math.ceil(r['lab_hour'] * 2))
            if lab_dur > 0:
                tasks.append({'uid': f"{c}_S{s}_Lab", 'id': c, 'sec': s, 'type': 'Lab', 'dur': lab_dur, 'std': r['enrollment_count'], 'tea': tea, 'opt': opt, 'online': r.get('lab_online')==1, 'req_ai': r.get('require_lab_ai')==1})

        # 3. Solver Setup
        model = cp_model.CpModel()
        vars, is_sched, task_vars = {}, {}, {}
        room_lookup = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        tea_lookup = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        obj_terms, pen_terms, lec_lab_penalties = [], [], []

        for t in tasks:
            uid = t['uid']
            is_sched[uid] = model.NewBoolVar(f"sc_{uid}")
            t_d, t_s = model.NewIntVar(0, 4, f"d_{uid}"), model.NewIntVar(0, TOTAL_SLOTS-1, f"s_{uid}")
            t_e = model.NewIntVar(0, TOTAL_SLOTS, f"e_{uid}")
            model.Add(t_e == t_s + t['dur'])
            task_vars[uid] = {'d': t_d, 's': t_s, 'e': t_e}

            cands = []
            for r in room_list:
                if t['online'] and r['room'] != 'Online': continue
                if not t['online'] and (r['room'] == 'Online' or r['capacity'] < t['std']): continue
                if t.get('req_ai') and r['room'] != 'lab_ai': continue

                for d in range(5):
                    forbidden = occupied_fixed[r['room']][d]
                    for s in range(TOTAL_SLOTS - t['dur'] + 1):
                        s_v = SLOT_MAP[s]['val']
                        if mode == 1 and (s_v < 9.0 or (s_v + t['dur']*0.5) > 16.0): continue
                        if any(SLOT_MAP[s+i]['is_lunch'] for i in range(t['dur'])): continue
                        if any((s+i) in forbidden for i in range(t['dur'])): continue
                        if any(tid in un_map and not set(range(s, s+t['dur'])).isdisjoint(un_map[tid][d]) for tid in t['tea']): continue

                        v = model.NewBoolVar(f"{uid}_{r['room']}_{d}_{s}")
                        cands.append(v); vars[(uid, r['room'], d, s)] = v
                        model.Add(t_d == d).OnlyEnforceIf(v); model.Add(t_s == s).OnlyEnforceIf(v)
                        if mode == 2 and (s_v < 9.0 or (s_v + t['dur']*0.5) > 16.0): pen_terms.append(v * penalty_val)
                        for i in range(t['dur']):
                            room_lookup[r['room']][d][s+i].append(v)
                            for tid in t['tea']: tea_lookup[tid][d][s+i].append(v)

            if cands: model.Add(sum(cands) == 1).OnlyEnforceIf(is_sched[uid])
            else: model.Add(is_sched[uid] == 0)
            obj_terms.append(is_sched[uid] * (1000 if t['opt']==0 else 100))

        for lookup in [room_lookup, tea_lookup]:
            for k in lookup:
                for d in lookup[k]:
                    for s in lookup[k][d]:
                        if len(lookup[k][d][s]) > 1: model.Add(sum(lookup[k][d][s]) <= 1)

        model.Maximize(sum(obj_terms) - sum(pen_terms))
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = solver_time
        solver.parameters.num_search_workers = 8
        status = solver.Solve(model)

        res_final = []
        unsched_final = []
        if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            for f in fixed_tasks:
                res_final.append({'Day': DAYS[f['d']], 'Start': SLOT_MAP[f['s']]['time'], 'End': SLOT_MAP.get(f['s']+f['dur'],{'time':'19:00'})['time'], 'Room': f['room'], 'Course': f['id'], 'Sec': f['sec'], 'Type': 'Fixed', 'Teacher': ",".join(f['tea']), 'Note': 'Fixed'})
            for t in tasks:
                uid = t['uid']
                if solver.Value(is_sched[uid]):
                    d, s = solver.Value(task_vars[uid]['d']), solver.Value(task_vars[uid]['s'])
                    rm = next(k[1] for k, v in vars.items() if k[0] == uid and k[2] == d and k[3] == s and solver.Value(v))
                    res_final.append({'Day': DAYS[d], 'Start': SLOT_MAP[s]['time'], 'End': SLOT_MAP.get(s+t['dur'],{'time':'19:00'})['time'], 'Room': rm, 'Course': t['id'], 'Sec': t['sec'], 'Type': t['type'], 'Teacher': ",".join(t['tea']), 'Note': "Scheduled"})
                else: unsched_final.append({'Course': t['id'], 'Sec': t['sec'], 'Type': t['type'], 'Reason': 'Constraints'})
            
            df_res = pd.DataFrame(res_final)
            df_res['DayIdx'] = df_res['Day'].apply(lambda x: DAYS.index(x))
            return df_res.sort_values(['DayIdx', 'Start']).drop(columns='DayIdx'), pd.DataFrame(unsched_final), logs
        return pd.DataFrame(), pd.DataFrame(unsched_final), logs + ["No Solution Found"]
    except Exception as e: return None, None, [f"Error: {str(e)}"]
