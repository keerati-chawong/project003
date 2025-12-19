import streamlit as st
import pandas as pd
from scheduler_engine import run_solver_logic, get_slot_map # import logic

# ตั้งค่าหน้าเว็บ
st.set_page_config(page_title="Automatic Scheduler Pro", layout="wide")
st.title("🎓 Automatic Course Scheduler")

# ==========================================
# 1. UI Sidebar & Config
# ==========================================
st.sidebar.header("⚙️ Configuration")
SCHEDULE_MODE = st.sidebar.radio(
    "Select Scheduling Mode:",
    options=[1, 2],
    format_func=lambda x: "Compact (09:00-16:00)" if x==1 else "Flexible (08:30-19:00)"
)

# ส่วนการโหลดข้อมูล (ในที่นี้ใช้ไฟล์ local ตามโค้ดต้นฉบับของคุณ)
def load_data():
    try:
        path = "Web_schedule-main/Web_schedule-main/"
        return {
            'room': pd.read_csv(f"{path}room.csv"),
            'teacher_courses': pd.read_csv(f"{path}teacher_courses.csv"),
            'ai_in': pd.read_csv(f"{path}ai_in_courses.csv"),
            'cy_in': pd.read_csv(f"{path}cy_in_courses.csv"),
            'all_teacher': pd.read_csv(f"{path}all_teachers.csv"),
            'ai_out': pd.read_csv(f"{path}ai_out_courses.csv"),
            'cy_out': pd.read_csv(f"{path}cy_out_courses.csv")
        }
    except Exception as e:
        st.error(f"Error loading files: {e}")
        return None

# ==========================================
# 2. Main Controller
# ==========================================
if st.button("🚀 Run Scheduler", use_container_width=True):
    data = load_data()
    if data:
        # เตรียมข้อมูลสำหรับ Solver
        input_data = {
            'room': data['room'],
            'courses': pd.concat([data['ai_in'], data['cy_in']], ignore_index=True),
            'teacher_courses': data['teacher_courses'],
            'all_teacher': data['all_teacher'],
            'fixed_schedule': [] # จัดฟอร์แมต ai_out/cy_out ตามที่ solver ต้องการ
        }
        
        with st.spinner("🤖 AI is calculating the best schedule..."):
            res, un = run_solver_logic(input_data, SCHEDULE_MODE)
            
            if res:
                st.session_state['results'] = pd.DataFrame(res)
                st.session_state['unscheduled'] = un
                st.session_state['has_run'] = True
                st.success("✅ Schedule optimized!")
            else:
                st.error("❌ Could not find a feasible schedule.")

# ==========================================
# 3. Visualization
# ==========================================
if st.session_state.get('has_run'):
    df_res = st.session_state['results']
    
    # ส่วนแสดงตาราง Grid
    st.divider()
    all_rooms = sorted(df_res['Room'].unique())
    selected_room = st.selectbox("🔍 Select Room:", all_rooms)
    
    # ... (โค้ดสร้าง Grid ตารางเรียนที่คุณทำไว้ในต้นฉบับ) ...
    # เรียกใช้ create_timetable_grid() ที่นี่
    
    st.dataframe(df_res[df_res['Room'] == selected_room], use_container_width=True)
    
    # ปุ่มดาวน์โหลด
    csv = df_res.to_csv(index=False).encode('utf-8')
    st.download_button("📥 Download CSV", data=csv, file_name="schedule.csv")
