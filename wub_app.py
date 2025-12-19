import streamlit as st
import pandas as pd
from scheduler_engine import calculate_schedule, get_slot_map # นำเข้า Engine

# ==========================================
# 1. Page Configuration & Style
# ==========================================
st.set_page_config(page_title="Automatic Scheduler Pro", layout="wide")

st.markdown("""
<style>
    .tt-container { overflow-x: auto; font-family: 'Helvetica', sans-serif; margin-top: 20px; }
    .tt-table { width: 100%; border-collapse: collapse; min-width: 1200px; }
    .tt-table th, .tt-table td { border: 1px solid #dee2e6; text-align: center; padding: 4px; font-size: 11px; height: 75px; }
    .tt-header { background-color: #343a40; color: white; position: sticky; left: 0; }
    .tt-day { background-color: #f8f9fa; font-weight: bold; width: 70px; position: sticky; left: 0; z-index: 10; border-right: 2px solid #ccc; font-size: 13px;}
    .class-box { 
        background-color: #e7f1ff; border: 1px solid #b6d4fe; border-radius: 6px;
        padding: 4px; height: 95%; display: flex; flex-direction: column; justify-content: center;
        color: #084298; box-shadow: 1px 1px 3px rgba(0,0,0,0.1); font-size: 10px; line-height: 1.2;
    }
    .c-code { font-weight: bold; text-decoration: underline; font-size: 12px; color: #004085; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. Sidebar & Controller
# ==========================================
st.sidebar.header("📂 1. Upload Data")
up_files = {k: st.sidebar.file_uploader(f"Upload {k}.csv", type="csv") for k in ['room', 'teacher_courses', 'ai_in', 'cy_in', 'teachers']}
up_files['ai_out'] = st.sidebar.file_uploader("Upload ai_out_courses.csv (Optional)", type="csv")
up_files['cy_out'] = st.sidebar.file_uploader("Upload cy_out_courses.csv (Optional)", type="csv")

st.sidebar.divider()
st.sidebar.header("⚙️ 2. Configuration")
mode_sel = st.sidebar.radio("Mode:", [1, 2], format_func=lambda x: "Compact (09-16)" if x==1 else "Flexible (08:30-19)")
solver_t = st.sidebar.slider("Solver Time (Sec):", 10, 600, 120)
penalty_v = st.sidebar.slider("Penalty (Ext. Time):", 0, 100, 10)

if st.button("🚀 Run Automatic Scheduler", use_container_width=True):
    if any(up_files[k] is None for k in ['room', 'teacher_courses', 'ai_in', 'cy_in', 'teachers']):
        st.error("Please upload all 5 required files.")
    else:
        with st.status("Solving...", expanded=True) as status:
            df_r, df_u, l = calculate_schedule(up_files, mode_sel, solver_t, penalty_v)
            if df_r is not None and not df_r.empty:
                st.session_state['res'], st.session_state['un'], st.session_state['has_run'] = df_r, df_u, True
                status.update(label="✅ Completed!", state="complete")
            else: st.error("Failed to find schedule.")

# ==========================================
# 3. Visualization
# ==========================================
if st.session_state.get('has_run'):
    res, un = st.session_state['res'], st.session_state['un']
    view = st.radio("View Mode:", ["Room View", "Teacher View"], horizontal=True)
    
    if view == "Room View":
        sel = st.selectbox("Select Room:", sorted(res['Room'].unique()))
        filt = res[res['Room'] == sel]
    else:
        all_t = sorted(list(set([i.strip() for s in res['Teacher'] for i in str(s).split(',') if i != '-'])))
        sel = st.selectbox("Select Teacher:", all_t)
        filt = res[res['Teacher'].str.contains(sel, na=False)]

    # HTML Table Generator
    days_map = {'Mon': 'จันทร์', 'Tue': 'อังคาร', 'Wed': 'พุธ', 'Thu': 'พฤหัสบดี', 'Fri': 'ศุกร์'}
    time_cols = [f"{h:02d}:{m:02d}" for h in range(8, 19) for m in [0, 30]][1:]
    html = f"<div class='tt-container'><table class='tt-table'><tr class='tt-header'><th>Day</th>"
    for t in time_cols: html += f"<th>{t}</th>"
    html += "</tr>"
    for day in ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']:
        html += f"<tr><td class='tt-day'>{days_map[day]}</td>"
        d_data = filt[filt['Day'] == day]
        curr = 8.5
        while curr < 19.0:
            t_str = f"{int(curr):02d}:{int((curr%1)*60):02d}"
            match = d_data[d_data['Start'] == t_str]
            if not match.empty:
                row = match.iloc[0]
                sh, sm = map(int, row['Start'].split(':'))
                eh, em = map(int, row['End'].split(':'))
                span = int(((eh + em/60) - (sh + sm/60)) * 2)
                html += f"<td colspan='{span}'><div class='class-box'><span class='c-code'>{row['Course']}</span><span>(S{row['Sec']}) {row['Type']}</span><span>{row.get('Teacher','-')}</span>"
                html += "</div></td>"; curr += (span * 0.5)
            else: html += "<td></td>"; curr += 0.5
    st.markdown(html + "</table></div>", unsafe_allow_html=True)
