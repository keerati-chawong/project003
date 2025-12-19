from ortools.sat.python import cp_model
import math
import re
import os

# ==========================================
# 1. Page Config & CSS Styling (สำหรับตารางสวยงาม)
# 1. UI & CSS Styling (ตารางรูปแบบ Blue Box)
# ==========================================
st.set_page_config(page_title="Automatic Scheduler Pro", layout="wide")
st.set_page_config(page_title="Course Scheduler Pro", layout="wide")

st.markdown("""
<style>
    .tt-container { overflow-x: auto; font-family: 'Helvetica', sans-serif; }
    .tt-table { width: 100%; border-collapse: collapse; table-layout: fixed; min-width: 1100px; }
    .tt-table th, .tt-table td { border: 1px solid #dee2e6; text-align: center; padding: 4px; font-size: 11px; height: 75px; }
    .tt-header { background-color: #343a40; color: white; font-weight: bold; }
    .tt-day { background-color: #f8f9fa; font-weight: bold; width: 70px; font-size: 14px; }
    .tt-container { overflow-x: auto; margin-top: 20px; }
    .tt-table { width: 100%; border-collapse: collapse; table-layout: fixed; min-width: 1200px; background-color: #1e1e1e; color: white; }
    .tt-table th, .tt-table td { border: 1px solid #444; text-align: center; padding: 2px; height: 80px; }
    .tt-header { background-color: #333; font-weight: bold; font-size: 12px; }
    .tt-day { background-color: #252525; font-weight: bold; width: 80px; font-size: 14px; }
    .class-box { 
        background-color: #e7f1ff; border: 1px solid #b6d4fe; border-radius: 5px;
        padding: 4px; height: 90%; display: flex; flex-direction: column; justify-content: center;
        color: #084298; box-shadow: 1px 1px 3px rgba(0,0,0,0.1);
        background-color: #e7f1ff; border-radius: 6px; padding: 6px; height: 90%; 
        display: flex; flex-direction: column; justify-content: center; align-items: center;
        color: #084298; border: 2px solid #b6d4fe; box-shadow: 0 2px 4px rgba(0,0,0,0.2);
    }
    .c-code { font-weight: bold; font-size: 13px; color: #004085; text-decoration: underline; }
    .c-info { font-size: 10px; margin: 2px 0; }
    .c-teacher { font-weight: bold; font-size: 10px; color: #333; }
    .c-code { font-weight: bold; font-size: 14px; text-decoration: underline; margin-bottom: 2px; }
    .c-info { font-size: 11px; font-weight: 500; }
    .c-teacher { font-size: 11px; margin-top: 2px; color: #052c65; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. Sidebar: Upload & Config
# 2. ฟังก์ชันช่วยประมวลผลเวลา
# ==========================================
st.sidebar.header("📂 1. Data Management")
def upload_file(label, default):
    up = st.sidebar.file_uploader(f"Upload {label}", type="csv")
    if up: return up
    return default if os.path.exists(default) else None

up_r = upload_file("room.csv", "room.csv")
up_tc = upload_file("teacher_courses.csv", "teacher_courses.csv")
up_ai_in = upload_file("ai_in_courses.csv", "ai_in_courses.csv")
up_cy_in = upload_file("cy_in_courses.csv", "cy_in_courses.csv")
up_ai_out = upload_file("ai_out_courses.csv", "ai_out_courses.csv")
up_cy_out = upload_file("cy_out_courses.csv", "cy_out_courses.csv")

st.sidebar.divider()
st.sidebar.header("⚙️ 2. Solver Settings")
SOLVER_TIME = st.sidebar.slider("Solver Time Limit (Sec)", 10, 300, 120)
PENALTY_EXT = st.sidebar.slider("Penalty: Avoid Ext. Time", 0, 100, 50)

# ==========================================
# 3. Core Logic
# ==========================================
def calculate_schedule():
    # Slot Map: 08:30 - 19:00 (30 min steps)
def get_slot_map():
    slots = {}
    t, idx = 8.5, 0
    while t < 19.0:
        h, m = int(t), int((t % 1) * 60)
        slots[idx] = {'time': f"{h:02d}:{m:02d}", 'val': t, 'is_lunch': (12.5 <= t < 13.0)}
        t += 0.5; idx += 1
    
    days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
    time_to_idx = {v['time']: k for k, v in slots.items()}
    return slots

def time_to_val(t_str):
    t_str = str(t_str).replace('.', ':').strip()
    if ':' in t_str:
        h, m = map(int, t_str.split(':'))
        return h + (0.5 if m >= 30 else 0)
    return float(t_str)

# ==========================================
# 3. Sidebar: Configuration
# ==========================================
st.sidebar.header("📁 Data Management")
up_r = st.sidebar.file_uploader("Upload room.csv", type="csv")
up_tc = st.sidebar.file_uploader("Upload teacher_courses.csv", type="csv")
up_ai_in = st.sidebar.file_uploader("Upload ai_in_courses.csv", type="csv")
up_cy_in = st.sidebar.file_uploader("Upload cy_in_courses.csv", type="csv")
up_ai_out = st.sidebar.file_uploader("Upload ai_out_courses.csv", type="csv")
up_cy_out = st.sidebar.file_uploader("Upload cy_out_courses.csv", type="csv")

st.sidebar.divider()
SOLVER_TIME = st.sidebar.slider("Solver Time (Sec)", 10, 300, 120)
PENALTY_EXT = st.sidebar.slider("Penalty: Avoid Ext. Time", 0, 100, 50)
SCH_MODE = st.radio("Scheduling Mode:", [1, 2], index=1, horizontal=True, 
                    format_func=lambda x: "Compact (09-16)" if x==1 else "Flexible (08:30-19)")

# ==========================================
# 4. Solver Core Logic
# ==========================================
def run_solver():
    slots = get_slot_map()
    days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
    
    # Load Data
    try:
        df_courses = pd.concat([pd.read_csv(up_ai_in), pd.read_csv(up_cy_in)], ignore_index=True).fillna(0)
        room_list = pd.read_csv(up_r).to_dict('records')
        room_list.append({'room': 'Online', 'capacity': 9999})
        t_map = pd.read_csv(up_tc).groupby('course_code')['teacher_id'].apply(lambda x: ",".join(x.astype(str))).to_dict()

        # ตรวจสอบวิชาที่ล็อกไว้ (Fixed)
        occupied = []
        for f in [up_ai_out, up_cy_out]:
            if f:
                df_f = pd.read_csv(f)
                for _, row in df_f.iterrows():
                    d_i = days.index(row['day'][:3]) if str(row['day'])[:3] in days else -1
                    s_str = str(row['start']).replace('.', ':').zfill(5)
                    s_i = time_to_idx.get(s_str, -1)
                    d_idx = days.index(row['day'][:3]) if str(row['day'])[:3] in days else -1
                    s_val = time_to_val(row['start'])
                    dur = int(math.ceil((row['lecture_hour'] + row['lab_hour']) * 2))
                    if d_i != -1 and s_i != -1:
                        for i in range(dur): occupied.append((str(row['room']), d_i, s_i + i))
    except Exception as e: return None, None, [f"Error: {e}"]
                    s_idx = next((k for k, v in slots.items() if v['val'] == s_val), -1)
                    if d_idx != -1 and s_idx != -1:
                        for i in range(dur): occupied.append((str(row['room']), d_idx, s_idx + i))
    except Exception as e: return None, None, f"Error: {e}"

    model = cp_model.CpModel()
    vars, is_sched, penalties = {}, {}, []
@@ -92,8 +100,14 @@ def calculate_schedule():
        for ty in ['Lec', 'Lab']:
            hr = row.get(f'{ty.lower()}_hour', 0)
            if hr > 0:
                tasks.append({'uid': f"{c}_S{sec}_{ty}", 'id': c, 'sec': sec, 'type': ty, 'dur': int(math.ceil(hr*2)), 'std': row['enrollment_count'], 'teachers': t_map.get(c, 'Staff'), 'is_on': row.get(f'{ty.lower()}_online', 0) == 1})

                tasks.append({
                    'uid': f"{c}_S{sec}_{ty}", 'id': c, 'sec': sec, 'type': ty, 
                    'dur': int(math.ceil(hr*2)), 'std': row['enrollment_count'], 
                    'teachers': t_map.get(c, 'Unknown'), 
                    'is_on': row.get(f'{ty.lower()}_online', 0) == 1
                })

    # Constraints & Variables
    for t in tasks:
        uid = t['uid']
        is_sched[uid] = model.NewBoolVar(f"s_{uid}")
@@ -103,19 +117,20 @@ def calculate_schedule():
            if r['capacity'] < t['std']: continue
            for d in range(5):
                for s in range(len(slots) - t['dur'] + 1):
                    s_val, e_val = slots[s]['val'], slots[s]['val'] + (t['dur']*0.5)
                    s_v, e_v = slots[s]['val'], slots[s]['val'] + (t['dur']*0.5)
                    if any(slots[s+i]['is_lunch'] for i in range(t['dur'])): continue
                    if SCH_MODE == 1 and (s_val < 9.0 or e_val > 16.0): continue
                    if SCH_MODE == 1 and (s_v < 9.0 or e_v > 16.0): continue
                    if any((r['room'], d, s+i) in occupied for i in range(t['dur'])): continue

                    v = model.NewBoolVar(f"{uid}_{r['room']}_{d}_{s}")
                    vars[(uid, r['room'], d, s)] = v
                    cands.append(v)
                    if s_val < 9.0 or e_val > 16.0: penalties.append(v * PENALTY_EXT)
                    if s_v < 9.0 or e_v > 16.0: penalties.append(v * PENALTY_EXT)

        if cands: model.Add(sum(cands) == 1).OnlyEnforceIf(is_sched[uid])
        else: model.Add(is_sched[uid] == 0)

    # Overlap Checks
    for d in range(5):
        for s in range(len(slots)):
            for r in [rm['room'] for rm in room_list if rm['room'] != 'Online']:
@@ -134,68 +149,57 @@ def calculate_schedule():
            for k, v in vars.items():
                if k[0] == t['uid'] and solver.Value(v):
                    found = True
                    notes = []
                    if t['is_on']: notes.append("Online")
                    if slots[k[3]]['val'] < 9.0 or (slots[k[3]]['val'] + t['dur']*0.5) > 16.0: notes.append("Ext.Time")
                    res.append({'Day': days[k[2]], 'Start': slots[k[3]]['time'], 'End': slots.get(k[3]+t['dur'], {'time':'19:00'})['time'], 'Room': k[1], 'Course': t['id'], 'Sec': t['sec'], 'Type': t['type'], 'Teacher': t['teachers'], 'Note': ", ".join(notes)})
        if not found: unres.append({'Course': t['id'], 'Sec': t['sec'], 'Type': t['type'], 'Teachers': t['teachers']})
                    res.append({
                        'Day': days[k[2]], 'Start': slots[k[3]]['time'], 
                        'End': slots.get(k[3]+t['dur'], {'time':'19:00'})['time'], 
                        'Room': k[1], 'Course': t['id'], 'Sec': t['sec'], 
                        'Type': t['type'], 'Teacher': t['teachers']
                    })
        if not found: unres.append(t)

    return pd.DataFrame(res), pd.DataFrame(unres)
    return pd.DataFrame(res), pd.DataFrame(unres), "Success"

# ==========================================
# 4. HTML Table Renderer (Format ตามรูปภาพ)
# 5. Display & Table Rendering
# ==========================================
def render_html_table(df, title):
    days_th = {'Mon': 'จันทร์', 'Tue': 'อังคาร', 'Wed': 'พุธ', 'Thu': 'พฤหัสบดี', 'Fri': 'ศุกร์'}
    times = [f"{h:02d}:{m:02d}" for h in range(8, 19) for m in [0, 30]][1:-1] # 08:30 - 18:30
if st.button("🚀 Run Scheduler", use_container_width=True):
    with st.spinner("กำลังคำนวณตารางเรียน..."):
        df_res, df_un, msg = run_solver()
        if df_res is not None:
            st.session_state['res'], st.session_state['un'] = df_res, df_un
            st.success("จัดตารางสำเร็จ!")

if 'res' in st.session_state:
    res_df = st.session_state['res']

    html = f"<div class='tt-container'><h4>{title}</h4><table class='tt-table'><tr class='tt-header'><th>Day/Time</th>"
    for t in times: html += f"<th>{t}</th>"
    # ตัวเลือกฟิลเตอร์
    target_room = st.selectbox("เลือกห้องที่ต้องการดู:", sorted(res_df['Room'].unique()))
    filtered = res_df[res_df['Room'] == target_room]

    # Render HTML Table
    days_map = {'Mon': 'จันทร์', 'Tue': 'อังคาร', 'Wed': 'พุธ', 'Thu': 'พฤหัสบดี', 'Fri': 'ศุกร์'}
    time_headers = [f"{h:02d}:{m:02d}" for h in range(8, 19) for m in [0, 30]][1:-1]
    
    html = f"<div class='tt-container'><table class='tt-table'><tr class='tt-header'><th>Day/Time</th>"
    for th in time_headers: html += f"<th>{th}</th>"
    html += "</tr>"

    for d_en in ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']:
        html += f"<tr><td class='tt-day'>{days_th[d_en]}</td>"
        d_df = df[df['Day'] == d_en]
        html += f"<tr><td class='tt-day'>{days_map[d_en]}</td>"
        d_df = filtered[filtered['Day'] == d_en]
        curr = 8.5
        while curr < 19.0:
        while curr < 18.5:
            c_str = f"{int(curr):02d}:{'30' if curr%1!=0 else '00'}"
            match = d_df[d_df['Start'] == c_str]
            if not match.empty:
                r = match.iloc[0]
                s_f = float(r['Start'].replace(':','.')) if ":30" not in r['Start'] else int(r['Start'][:2])+0.5
                e_f = float(r['End'].replace(':','.')) if ":30" not in r['End'] else int(r['End'][:2])+0.5
                span = int((e_f - s_f) * 2)
                html += f"<td colspan='{span}'><div class='class-box'><div class='c-code'>{r['Course']}</div><div class='c-info'>(S{r['Sec']}) {r['Type']}</div><div class='c-teacher'>{r['Teacher']}</div><div style='font-size:9px'>{r['Room']} {r['Note']}</div></div></td>"
                s_val = time_to_val(r['Start'])
                e_val = time_to_val(r['End'])
                span = int((e_val - s_val) * 2)
                html += f"<td colspan='{span}'><div class='class-box'><div class='c-code'>{r['Course']}</div><div class='c-info'>(S{r['Sec']}) {r['Type']}</div><div class='c-teacher'>{r['Teacher']}</div></div></td>"
                curr += (span * 0.5)
            else:
                if curr < 18.5: html += "<td></td>"
                curr += 0.5
                html += "<td></td>"; curr += 0.5
        html += "</tr>"
    return html + "</table></div>"

# ==========================================
# 5. UI Controller
# ==========================================
SCH_MODE = st.radio("Select Mode:", [1, 2], index=1, horizontal=True, format_func=lambda x: "Compact (09-16)" if x==1 else "Flexible (08:30-19)")
if st.button("🚀 Run Automatic Scheduler", use_container_width=True):
    with st.spinner("Processing..."):
        df_res, df_un = calculate_schedule()
        if df_res is not None:
            st.session_state['res'], st.session_state['un'], st.session_state['run'] = df_res, df_un, True

if st.session_state.get('run'):
    view = st.radio("View Mode:", ["Room View", "Teacher View"], horizontal=True)
    if view == "Room View":
        sel = st.selectbox("Select Room:", sorted(st.session_state['res']['Room'].unique()))
        filt = st.session_state['res'][st.session_state['res']['Room'] == sel]
    else:
        all_t = sorted(list(set([i.strip() for s in st.session_state['res']['Teacher'] for i in str(s).split(',')])))
        sel = st.selectbox("Select Teacher:", all_t)
        filt = st.session_state['res'][st.session_state['res']['Teacher'].str.contains(sel)]
    
    st.markdown(render_html_table(filt, f"Results for: {sel}"), unsafe_allow_html=True)

    if not st.session_state['un'].empty:
        st.error("⚠️ Unscheduled Tasks")
        st.table(st.session_state['un'])
    st.download_button("📥 Download CSV", st.session_state['res'].to_csv(index=False), "schedule.csv")
    st.markdown(html + "</table></div>", unsafe_allow_html=True)
