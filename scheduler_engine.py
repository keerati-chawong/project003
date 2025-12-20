import pandas as pd
from ortools.sat.python import cp_model
import math
import re
from collections import defaultdict

def get_slot_map():
    slots = {}
    t_start = 8.5
    idx = 0
    while t_start <= 19.0: # ครอบคลุมถึงเวลาเลิก 19:00
        h, m = int(t_start), round((t_start % 1) * 60)
        slots[idx] = {'time': f"{h:02d}:{m:02d}", 'val': t_start, 'is_lunch': (12.5 <= t_start < 13.0)}
        idx += 1; t_start += 0.5
    return slots

def parse_unavailable_time(val, days, inv):
    res = {i: set() for i in range(len(days))}
    if pd.isna(val) or not val: return res
    items = val if isinstance(val, list) else [str(val)]
    for it in items:
        m = re.search(r"(\w{3})\s+(\d{1,2}[:.]\d{2})-(\d{1,2}[:.]\d{2})", str(it))
        if m:
            d, s, e = m.groups()
            s_f = s.replace('.', ':'); e_f = e.replace('.', ':')
            if d in days and s_f in inv and e_f in inv:
                for i in range(inv[s_f], inv[e_f]): res[days.index(d)].add(i)
    return res

def calculate_schedule(files, mode, solver_time, penalty_score):
    SLOT_MAP = get_slot_map()
    DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
    SLOT_INV = {v['time']: k for k, v in SLOT_MAP.items()}
    TOTAL_SLOTS = len(SLOT_MAP)

    try:
        # Load Data
        df_room = pd.read_csv(files['room'])
        room_list = df_room.to_dict('records')
        room_list.append({'room': 'Online', 'capacity': 9999, 'type': 'virtual'})
        df_tc = pd.read_csv(files['teacher_courses'])
        df_courses = pd.concat([pd.read_csv(files['ai_in']), pd.read_csv(files['cy_in'])], ignore_index=True).fillna(0)
        df_teacher = pd.read_csv(files['all_teachers'])

        t_map = defaultdict(list)
        for _, r in df_tc.iterrows(): t_map[str(r['course_code']).strip()].append(str(r['teacher_id']).strip())
        un_map = {str(r['teacher_id']).strip(): parse_unavailable_time(r.get('unavailable_times'), DAYS, SLOT_INV) for _, r in df_teacher.iterrows()}

        # 1. Fixed Schedule (ai_out, cy_out)
        fixed_tasks = []
        for key in ['ai_out', 'cy_out']:
            if files[key] is not None:
                df_f = pd.read_csv(files[key])
                for _, r in df_f.iterrows():
                    d_i = DAYS.index(str(r['day'])[:3]) if str(r['day'])[:3] in DAYS else -1
                    s_i = SLOT_INV.get(str(r['start']).replace('.', ':'), -1)
                    dur = int(math.ceil((r.get('lecture_hour', 0) + r.get('lab_hour', 0)) * 2))
                    if d_i != -1 and s_i != -1:
                        fixed_tasks.append({
                            'uid': f"FIX_{r['course_code']}_{r['section']}", 'id': str(r['course_code']), 
                            'sec': int(r['section']), 'dur': dur, 'type': 'Fixed',
                            'tea': t_map.get(str(r['course_code']).strip(), ['-']),
                            'fixed_room': True, 'target_room': str(r['room']), 'f_d': d_i, 'f_s': s_i
                        })

        # 2. Dynamic Tasks
        tasks = []
        for _, r in df_courses.iterrows():
            c, s = str(r['course_code']).strip(), int(r['section'])
            tea, opt = t_map.get(c, ['Unknown']), r.get('optional', 1)
            lec_slots = int(math.ceil(r['lecture_hour'] * 2))
            p = 1
            while lec_slots > 0:
                dur = min(lec_slots, 6)
                uid = f"{c}_S{s}_Lec_P{p}"
                if not any(tk['uid'] == uid for tk in fixed_tasks):
                    tasks.append({'uid': uid, 'id': c, 'sec': s, 'type': 'Lec', 'dur': dur, 'std': r['enrollment_count'], 'tea': tea, 'opt': opt, 'online': r.get('lec_online')==1})
                lec_slots -= dur; p += 1
            lab_dur = int(math.ceil(r['lab_hour'] * 2))
            if lab_dur > 0:
                uid = f"{c}_S{s}_Lab"
                if not any(tk['uid'] == uid for tk in fixed_tasks):
                    tasks.append({'uid': uid, 'id': c, 'sec': s, 'type': 'Lab', 'dur': lab_dur, 'std': r['enrollment_count'], 'tea': tea, 'opt': opt, 'online': r.get('lab_online')==1})

        # 3. Solver Setup
        model = cp_model.CpModel()
        vars, is_sched, task_vars = {}, {}, {}
        room_lookup = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        tea_lookup = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        obj_terms, pen_terms = [], []

        for t in (fixed_tasks + tasks):
            uid = t['uid']
            is_sched[uid] = model.NewBoolVar(f"sc_{uid}")
            t_d, t_s = model.NewIntVar(0, 4, f"d_{uid}"), model.NewIntVar(0, TOTAL_SLOTS-1, f"s_{uid}")
            model.Add(model.NewIntVar(0, TOTAL_SLOTS+1, f"e_{uid}") == t_s + t['dur'])
            task_vars[uid] = {'d': t_d, 's': t_s}
            if t.get('fixed_room'):
                model.Add(t_d == t['f_d']); model.Add(t_s == t['f_s'])

            cands = []
            for r in room_list:
                if t.get('online') and r['room'] != 'Online': continue
                if not t.get('online') and (r['room'] == 'Online' or r['capacity'] < t.get('std', 0)): continue
                if t.get('fixed_room') and r['room'] != t['target_room']: continue
                if t.get('type') == 'Lab' and 'lab' not in str(r.get('type','')).lower(): continue

                for d in range(5):
                    for s in range(TOTAL_SLOTS - t['dur']):
                        sv, ev = SLOT_MAP[s]['val'], SLOT_MAP[s]['val'] + (t['dur']*0.5)
                        if mode == 1 and (sv < 9.0 or ev > 16.0): continue
                        if any(SLOT_MAP[s+i]['is_lunch'] for i in range(t['dur'])): continue
                        if any(tid in un_map and s+i in un_map[tid][d] for tid in t['tea'] for i in range(t['dur'])): continue

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
        solver.parameters.max_time_in_seconds = solver_time # ตัวแปรเวลาประมวลผล
        status = solver.Solve(model)

        res_final = []
        if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            for t in (fixed_tasks + tasks):
                if solver.Value(is_sched[t['uid']]):
                    d, s = solver.Value(task_vars[t['uid']]['d']), solver.Value(task_vars[t['uid']]['s'])
                    rm = next((k[1] for k, v in vars.items() if k[0] == t['uid'] and k[2] == d and k[3] == s and solver.Value(v)), "Unknown")
                    res_final.append({'Day': DAYS[d], 'Start': SLOT_MAP[s]['time'], 'End': SLOT_MAP[s+t['dur']]['time'], 'Room': rm, 'Course': t['id'], 'Sec': t['sec'], 'Type': t.get('type','-'), 'Teacher': ",".join(t['tea']), 'Note': "Ext.Time" if (SLOT_MAP[s]['val'] < 9.0 or SLOT_MAP[s+t['dur']-1]['val'] >= 16.0) else ""})
            return pd.DataFrame(res_final)
        return None
    except Exception as e:
        return None
