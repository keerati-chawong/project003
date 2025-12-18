import pandas as pd

def load_all_data():
    df_room = pd.read_csv("room.csv")
    df_teacher = pd.read_csv("all_teachers.csv")
    df_teacher_courses = pd.read_csv("teacher_courses.csv")
    df_ai_in = pd.read_csv("ai_in_courses.csv")
    df_ai_out = pd.read_csv("ai_out_courses.csv")
    df_cy_in = pd.read_csv("cy_in_courses.csv")
    df_cy_out = pd.read_csv("cy_out_courses.csv")
    students = pd.read_csv("students.csv")

    return {
        "room": df_room,
        "teacher": df_teacher,
        "teacher_courses": df_teacher_courses,
        "ai_in": df_ai_in,
        "ai_out": df_ai_out,
        "cy_in": df_cy_in,
        "cy_out": df_cy_out,
        "students": students,
    }
