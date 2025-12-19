import streamlit as st
import pandas as pd
from scheduler_engine import run_solver_logic, get_slot_map # นำเข้า Engine

# ตั้งค่าหน้าเว็บ
st.set_page_config(page_title="Automatic Scheduler Pro", layout="wide")
st.title("🎓 Automatic Course Scheduler")

# ==========================================
# 1. UI Sidebar & File Uploads
# ==========================================
st.sidebar.header("📂 Data Management")

# ฟังก์ชันช่วยสร้างปุ่มอัปโหลดและตรวจสอบไฟล์
def upload_section(label, default_path):
    uploaded = st.sidebar.file_uploader(f"Upload {label}", type="csv")
    if uploaded:
        return uploaded
    return default_path # ถ้าไม่มีการอัปโหลด ให้ใช้ path เดิมในระบบ

# ปุ่มอัปโหลดไฟล์ต่างๆ
up_room = upload_section("room.csv", 'Web_schedule-main/Web_schedule-main/room.csv')
up_teacher_courses = upload_section("teacher_courses.csv", 'Web_schedule-main/Web_schedule-main/teacher_courses.csv')
up_ai_in = upload_section("ai_in_courses.csv", 'Web_schedule-main/Web_schedule-main/ai_in_courses.csv')
up_cy_in = upload_section("cy_in_courses.csv", 'Web_schedule-main/Web_schedule-main/cy_in_courses.csv')
up_teachers = upload_section("all_teachers.csv", 'Web_schedule-main/Web_schedule-main/all_teachers.csv')
up_ai_out = upload_section("ai_out_courses.csv", 'Web_schedule-main/Web_schedule-main/ai_out_courses.csv')
up_cy_out = upload_section("cy_out_courses.csv", 'Web_schedule-main/Web_schedule-main/cy_out_courses.csv')

st.sidebar.divider()
st.sidebar.header("⚙️ Configuration")
SCHEDULE_MODE = st.sidebar.radio(
    "Select Scheduling Mode:",
    options=[1, 2],
    format_func=lambda x: "Compact (09:00-16:00)" if x==1 else "Flexible (08:30-19:00)"
)

# ==========================================
# 2. Main Controller
# ==========================================
if st.button("🚀 Run Scheduler", use_container_width=True):
    try:
        # โหลดข้อมูลจากไฟล์ที่อัปโหลดหรือไฟล์ Default
        input_data = {
            'room': pd.read_csv(up_room),
            'teacher_courses': pd.read_csv(up_teacher_courses),
            'courses': pd.concat([pd.read_csv(up_ai_in), pd.read_csv(up_cy_in)], ignore_index=True),
            'all_teacher': pd.read_csv(up_teachers),
            # สำหรับ fixed schedule จัดการแยกตามชื่อไฟล์
            'fixed_schedule_files': [
                {'name': 'ai_out_courses.csv', 'data': pd.read_csv(up_ai_out)},
                {'name': 'cy_out_courses.csv', 'data': pd.read_csv(up_cy_out)}
            ]
        }
        
        with st.spinner("🤖 AI กำลังคำนวณตารางเรียนที่ดีที่สุด..."):
            # ส่งข้อมูลเข้าสู่ Solver Engine
            res, un = run_solver_logic(input_data, SCHEDULE_MODE)
            
            if res:
                st.session_state['results'] = pd.DataFrame(res)
                st.session_state['unscheduled'] = un
                st.session_state['has_run'] = True
                st.success("✅ จัดตารางเรียนสำเร็จ!")
            else:
                st.error("❌ ไม่สามารถจัดตารางที่เหมาะสมได้ตามเงื่อนไขที่กำหนด")
                
    except Exception as e:
        st.error(f"❌ เกิดข้อผิดพลาดในการโหลดข้อมูล: {e}")

# ==========================================
# 3. Visualization & Download
# ==========================================
if st.session_state.get('has_run'):
    df_res = st.session_state['results']
    unscheduled = st.session_state['unscheduled']
    
    st.divider()
    
    # แสดงตารางเรียนรายห้อง
    all_rooms = sorted(df_res['Room'].unique())
    selected_room = st.selectbox("🔍 เลือกห้องเรียนเพื่อดูตาราง:", all_rooms)
    
    # ... (แสดงผลตาราง Grid หรือ DataFrame ตามที่คุณออกแบบไว้) ...
    st.dataframe(df_res[df_res['Room'] == selected_room], use_container_width=True)
    
    # แสดงวิชาที่จัดไม่ได้
    if unscheduled:
        with st.expander("⚠️ รายการวิชาที่จัดลงตารางไม่ได้"):
            st.table(pd.DataFrame(unscheduled))
    
    # ปุ่มดาวน์โหลด
    csv = df_res.to_csv(index=False).encode('utf-8')
    st.download_button("📥 ดาวน์โหลดตารางสอนทั้งหมด (CSV)", csv, file_name="full_schedule.csv", mime="text/csv")
