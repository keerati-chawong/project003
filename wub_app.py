import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import math
import re

# ตั้งค่าหน้าเว็บ
st.set_page_config(page_title="Automatic Scheduler Pro", layout="wide")
st.title("🎓 Automatic Course Scheduler")

# ==========================================
# 1. ระบบอัปโหลดไฟล์ และ ตัวแปรปรับจูน (Sidebar)
# ==========================================
st.sidebar.header("📂 1. อัปโหลดข้อมูล (CSV)")
up_room = st.sidebar.file_uploader("ห้องเรียน (room.csv)", type="csv")
up_tc = st.sidebar.file_uploader("วิชาและอาจารย์ (teacher_courses.csv)", type="csv")
up_ai = st.sidebar.file_uploader("วิชา AI (ai_in_courses.csv)", type="csv")
up_cy = st.sidebar.file_uploader("วิชา CY (cy_in_courses.csv)", type="csv")
up_teach = st.sidebar.file_uploader("ข้อมูลอาจารย์ (all_teachers.csv)", type="csv")

st.sidebar.divider()
st.sidebar.header("⚙️ 2. ตั้งค่า Solver")
# ข้อ 3: เพิ่มตัวแปรให้ผู้ใช้ปรับได้เอง (เวลา และ Penalty)
SOLVER_TIME = st.sidebar.slider("เวลาประมวลผลสูงสุด (วินาที)", 10, 300, 120)
PENALTY_SCORE = st.sidebar.slider("คะแนนบทลงโทษ (Penalty Score)", 1, 100, 10)

mode_desc = {1: "Compact (09:00 - 16:00)", 2: "Flexible (08:30 - 19:00)"}
SCHEDULE_MODE = st.radio("เลือกโหมดการจัดตาราง:", options=[1, 2], format_func=lambda x: mode_desc[x])

# ==========================================
# ฟังก์ชันคำนวณ (ปรับปรุงให้รับค่าจาก UI)
# ==========================================
def calculate_schedule(files, max_time, penalty_val):
    # [Logic การเตรียม SLOT_MAP และการคำนวณเหมือนเดิม แต่เปลี่ยนการโหลดไฟล์]
    # ตัวอย่างการโหลดไฟล์ที่อัปโหลด
    try:
        df_room = pd.read_csv(files['room'])
        df_tc = pd.read_csv(files['tc'])
        df_courses = pd.concat([pd.read_csv(files['ai']), pd.read_csv(files['cy'])], ignore_index=True)
        # ... (กระบวนการ Solver เหมือนเดิม) ...
        
        # ข้อ 3: นำค่าจาก UI ไปใช้ใน Solver
        # solver.parameters.max_time_in_seconds = max_time
        # objective_terms.append(var * penalty_val)
        
        # สมมติผลลัพธ์เป็น DataFrame (เพื่อประหยัดพื้นที่แสดงตัวอย่าง)
        return pd.DataFrame() # คืนค่าผลลัพธ์จริงที่นี่
    except Exception as e:
        st.error(f"เกิดข้อผิดพลาด: {e}")
        return None

#ปุ่มรัน
if st.button("🚀 เริ่มจัดตารางสอน"):
    if not (up_room and up_tc and up_ai and up_cy and up_teach):
        st.warning("กรุณาอัปโหลดไฟล์ให้ครบถ้วนในแถบด้านข้าง")
    else:
        files = {'room': up_room, 'tc': up_tc, 'ai': up_ai, 'cy': up_cy, 'teach': up_teach}
        res = calculate_schedule(files, SOLVER_TIME, PENALTY_SCORE)
        if res is not None:
            st.session_state['res'] = res
            st.session_state['run'] = True

# ==========================================
# 2. ส่วนการแสดงผล (มุมมองรายอาจารย์)
# ==========================================
if st.session_state.get('run'):
    df_res = st.session_state['res']
    
    # ข้อ 2: เพิ่มมุมมองให้เลือกดูรายห้อง หรือ รายอาจารย์
    view_option = st.radio("เลือกมุมมองตาราง:", ["ดูตามห้องเรียน (Room)", "ดูตามรายชื่ออาจารย์ (Teacher)"])
    
    if view_option == "ดูตามห้องเรียน (Room)":
        target_list = sorted(df_res['Room'].unique())
        label = "เลือกห้องเรียน:"
    else:
        # แยกชื่ออาจารย์ออกมาจากคอลัมน์ Teacher
        target_list = sorted(list(set([t.strip() for ts in df_res['Teacher'] for t in ts.split(',')])))
        label = "เลือกชื่ออาจารย์:"
        
    selected_target = st.selectbox(label, target_list)
    
    # กรองข้อมูลตามที่เลือก
    if view_option == "ดูตามห้องเรียน (Room)":
        filt_df = df_res[df_res['Room'] == selected_target]
    else:
        filt_df = df_res[df_res['Teacher'].str.contains(selected_target)]

    st.subheader(f"📍 ตารางของ: {selected_target}")
    st.table(filt_df) # หรือใช้ฟังก์ชัน create_timetable_grid ของคุณในการแสดงผล
