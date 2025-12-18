import streamlit as st
from data_loader import load_all_data
from solver import solve_timetable

st.set_page_config(layout="wide")
st.title("📚 ระบบจัดตารางเรียนอัตโนมัติ 2")

mode = st.radio(
    "เลือกโหมดเวลา",
    [1, 2],
    format_func=lambda x: "Compact (09–16)" if x == 1 else "Flexible (08:30–19:00)"
)

if st.button("▶️ สร้างตารางเรียน"):
    with st.spinner("กำลังประมวลผล..."):
        data = load_all_data()
        df = solve_timetable(data, mode)

    if df is None:
        st.error("ไม่สามารถจัดตารางได้")
    else:
        st.success("จัดตารางสำเร็จ")
        st.dataframe(df)
        st.download_button(
            "ดาวน์โหลด CSV",
            df.to_csv(index=False),
            "final_schedule.csv",
            "text/csv"
        )
