import streamlit as st
import pandas as pd
from scheduler_engine import calculate_schedule # นำเข้า Engine

# ==========================================
# 1. Page Config & CSS (Blue Box Style)
# ==========================================
st.set_page_config(page_title="Automatic Scheduler Pro", layout="wide")

st.markdown("""
<style>
    .tt-container { overflow-x: auto; font-family: 'Helvetica', sans-serif; margin-top: 20px; }
    .tt-table { width: 100%; border-collapse: collapse; min-width: 1200px; }
    .tt-table th { background-color: #343a40 !important; color: white !important; border: 1px solid #444; text-align: center; padding: 8px; font-size: 14px; }
    .tt-table td { border: 1px solid #dee2e6; text-align: center; padding: 4px; height: 75px; }
    .tt-day { background-color: #f8f9fa !important; color: #333 !important; font-weight: bold !important; width: 80px; position: sticky; left: 0; z-index: 10; border-right: 2px solid #ccc !important; font-size: 14px;}
    .class-box { background-color: #e7f1ff; border: 1px solid #b6d4fe; border-radius: 6px; padding: 4px; height: 95%; display: flex; flex-direction: column; justify-content: center; color: #084298 !important; box-shadow: 1px 1px 3px rgba(0,0,0,0.1); font-size: 10px; line-height: 1.2; }
    .c-code { font-weight: bold; text-decoration: underline; font-size: 12px; color: #004085 !important; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. Sidebar: อัปโหลด 7 ไฟล์ & ตั้งค่า Solver
# ==========================================
st.sidebar.header("📂 1. ระบบอัปโหลดไฟล์ (7 ไฟล์)")
up_files = {
    'room': st.sidebar.file_uploader("1. room.csv", type="csv"),
    'teacher_courses': st.sidebar.file_uploader("2. teacher_courses.csv", type="csv"),
    'ai_in': st.sidebar.file_uploader("3. ai_in_courses.csv", type="csv"),
    'cy_in': st.sidebar.file_uploader("4. cy_in_courses.csv", type="csv"),
    'all_teachers': st.sidebar.file_uploader("5. all_teachers.csv", type="csv"),
    'ai_out': st.sidebar.file_uploader("6. ai_out (Fixed)", type="csv"),
    'cy_out': st.sidebar.file_uploader("7. cy_out (Fixed)", type="csv"),
}

st.sidebar.divider()
st.sidebar.header("⚙️ 2. ตั้งค่า Solver")
mode_sel = st.sidebar.radio("โหมด:", [1, 2], format_func=lambda x: "Compact (09-16)" if x==1 else "Flexible (08:30-19)")
solver_t = st.sidebar.slider("เวลาคำนวณ (วินาที):", 10, 600, 120)
penalty_v = st.sidebar.slider("Penalty Score:", 0, 100, 10)

if st.button("🚀 Run Automatic Scheduler", use_container_width=True):
    mandatory = ['room', 'teacher_courses', 'ai_in', 'cy_in', 'all_teachers']
    if any(up_files[k] is None for k in mandatory):
        st.error("❌ กรุณาอัปโหลดไฟล์บังคับ 5 ไฟล์แรกให้ครบถ้วน")
    else:
        with st.status("🤖 กำลังคำนวณ...", expanded=True) as status:
            df_res = calculate_schedule(up_files, mode_sel, solver_t, penalty_v)
            if df_res is not None and not df_res.empty:
                st.session_state['res_df'], st.session_state['run_done'] = df_res, True
                status.update(label="✅ สำเร็จ!", state="complete")
            else: st.error("❌ หาคำตอบไม่ได้ (ลองเพิ่มเวลาหรือลด Penalty)")

# ==========================================
# 3. มุมมองตารางสอนอาจารย์ดูตารางสอนของตัวเอง
# ==========================================
if st.session_state.get('run_done'):
    df_res = st.session_state['res_df']
    view_mode = st.radio("เลือกมุมมอง:", ["รายห้อง (Room View)", "รายอาจารย์ (Teacher View)"], horizontal=True)
    
    if view_mode == "รายห้อง (Room View)":
        target = st.selectbox("เลือกห้อง:", sorted(df_res['Room'].unique()))
        filt_df = df_res[df_res['Room'] == target]
    else:
        all_t = sorted(list(set([i.strip() for s in df_res['Teacher'] for i in str(s).split(',') if i.strip() != '-'])))
        target = st.selectbox("เลือกชื่ออาจารย์ (Teacher View):", all_t)
        filt_df = df_res[df_res['Teacher'].str.contains(target, na=False)]

    # ตาราง Blue Box (เหมือนเดิม)
    days_map = {'Mon': 'จันทร์', 'Tue': 'อังคาร', 'Wed': 'พุธ', 'Thu': 'พฤหัสบดี', 'Fri': 'ศุกร์'}
    time_headers = [f"{h:02d}:{m:02d}" for h in range(8, 19) for m in [0, 30]][1:]
    
    html = f"<div class='tt-container'><table class='tt-table'><tr class='tt-header'><th>Day</th>"
    for t in time_headers: html += f"<th>{t}</th>"
    html += "</tr>"
    for day in ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']:
        html += f"<tr><td class='tt-day'>{days_map[day]}</td>"
        d_data = filt_df[filt_df['Day'] == day]
        curr = 8.5
        while curr < 19.0:
            t_str = f"{int(curr):02d}:{round((curr%1)*60):02d}"
            match = d_data[d_data['Start'] == t_str]
            if not match.empty:
                r = match.iloc[0]
                sh, sm = map(int, r['Start'].split(':')); eh, em = map(int, r['End'].split(':'))
                span = int(((eh + em/60) - (sh + sm/60)) * 2)
                html += f"<td colspan='{span}'><div class='class-box'><span class='c-code'>{r['Course']}</span><span>(S{r['Sec']}) {r['Type']}</span><span>{r['Teacher']}</span>"
                if r['Note']: html += f"<span style='color:red; font-size:9px'>{r['Note']}</span>"
                html += "</div></td>"; curr += (span * 0.5)
            else: html += "<td></td>"; curr += 0.5
    st.markdown(html + "</table></div>", unsafe_allow_html=True)
    st.download_button("📥 Download CSV", df_res.to_csv(index=False).encode('utf-8'), "schedule.csv", "text/csv")
