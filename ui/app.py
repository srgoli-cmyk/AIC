"""Minimal page: pick a course, ask a question, read the answer and open its sources."""

import os

import logic
import streamlit as st

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000").rstrip("/")

st.set_page_config(page_title="AI Lecture Search")
st.title("AI Lecture Search")

try:
    courses = logic.fetch_courses(BACKEND_URL)
except logic.BackendError as exc:
    st.error(str(exc))
    st.stop()

if not courses:
    st.info("No courses have been synced yet.")
    st.stop()

# Pick by ID: labels change as courses sync, and a changed label must not change the selection.
by_id = {c["course_id"]: c for c in courses}
if st.session_state.get("course_id") not in (None, *by_id):
    del st.session_state["course_id"]
    st.warning("The course you had selected is no longer available.")

course_id = st.selectbox(
    "Course",
    list(by_id),
    format_func=lambda cid: logic.course_label(by_id[cid]),
    key="course_id",
)
if not by_id[course_id]["indexed"]:
    st.warning("This course is still being indexed, so answers may be incomplete.")

with st.form("ask"):
    question = st.text_input("Question", max_chars=2000)
    submitted = st.form_submit_button("Ask")

if submitted:
    if not question.strip():
        st.warning("Type a question first.")
    else:
        st.session_state.pop("result", None)
        try:
            with st.spinner("Searching the lectures..."):
                answer = logic.ask(BACKEND_URL, course_id, question.strip())
        except logic.BackendError as exc:
            st.error(str(exc))
        else:
            st.session_state["result"] = {
                "course_id": course_id,
                "question": question.strip(),
                "answer": answer,
            }

result = st.session_state.get("result")
if result and result["course_id"] == course_id:
    st.subheader("Answer")
    # Answers and snippets come from captions, so they are shown as plain text, never as markup.
    st.text(f"Q: {result['question']}")
    st.text(result["answer"]["answer"])
    sources = result["answer"]["sources"]
    if sources:
        st.subheader("Sources")
    for source in sources:
        st.markdown(logic.source_link(source))
        st.text(source["snippet"])
