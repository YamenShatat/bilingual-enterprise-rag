"""The Streamlit UI (D-025). Run it, with the API already running:

    streamlit run src/bilingual_rag/ui/app.py

It is a client of the API and nothing more: login, questions, the document list and uploads all
go through the API, which enforces every permission. The login token is kept in this browser
session's server-side state only, never in the URL or a cookie.
"""

import streamlit as st

from bilingual_rag.ui import client as api
from bilingual_rag.ui.text import answer_html, citation_rows, refusal_message

LANGUAGES = {"en": "English", "ar": "العربية (Arabic)"}
ACCESS_LEVELS = ("public", "employee", "engineering", "hr", "management")

st.set_page_config(page_title="Bilingual Enterprise RAG", page_icon="📚", layout="wide")


def get_client() -> api.ApiClient:
    if "client" not in st.session_state:
        st.session_state.client = api.make_client()
    return st.session_state.client


def show_login(client: api.ApiClient) -> None:
    st.title("Bilingual Enterprise RAG")
    st.caption("Answers from your company documents, in Arabic and English, with sources.")
    with st.form("login"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in")
    if submitted:
        try:
            st.session_state.me = client.login(username.strip(), password)
        except api.ApiError as exc:
            st.error("Wrong username or password." if exc.status == 401 else str(exc))
            return
        st.rerun()


def show_answer(result: dict) -> None:
    if result["refusal"] is not None:
        st.warning(refusal_message(result["refusal"]))
        return
    st.markdown(answer_html(result["answer"]), unsafe_allow_html=True)
    st.subheader("Sources")
    st.dataframe(citation_rows(result["citations"]), hide_index=True, use_container_width=True)


def ask_tab(client: api.ApiClient) -> None:
    with st.form("ask"):
        question = st.text_area(
            "Question (Arabic or English)",
            placeholder="How many days of annual leave do I get? / كم يوم إجازة سنوية أستحق؟",
        )
        submitted = st.form_submit_button("Ask")
    if submitted and question.strip():
        with st.spinner("Searching your documents..."):
            st.session_state.last_result = client.ask(question.strip())
    if "last_result" in st.session_state:
        show_answer(st.session_state.last_result)


def documents_tab(client: api.ApiClient) -> None:
    documents = client.documents()
    st.caption(f"{len(documents)} documents you may read.")
    st.dataframe(documents, hide_index=True, use_container_width=True)


def upload_tab(client: api.ApiClient) -> None:
    with st.form("upload", clear_on_submit=True):
        file = st.file_uploader("File", type=["md", "txt", "docx", "pdf"])
        doc_id = st.text_input("Document id (lowercase letters, digits and hyphens)")
        title = st.text_input("Title")
        department = st.text_input("Department")
        topic = st.text_input("Topic")
        language = st.selectbox("Language", list(LANGUAGES), format_func=LANGUAGES.get)
        access_level = st.selectbox("Who may read it", ACCESS_LEVELS)
        digits = st.selectbox("Digits (Arabic documents)", ["", "arabic-indic", "western"])
        submitted = st.form_submit_button("Upload")
    if submitted:
        if file is None:
            st.error("Choose a file first.")
            return
        fields = {
            "id": doc_id.strip(),
            "title": title.strip(),
            "department": department.strip(),
            "topic": topic.strip(),
            "language": language,
            "access_level": access_level,
        }
        if digits:
            fields["digits"] = digits
        result = client.upload(file.name, file.getvalue(), fields)
        st.success(
            f"Stored {result['id']}: {result['chunks']} chunks, {result['embedded']} embedded."
        )


def main() -> None:
    client = get_client()
    if not client.logged_in:
        show_login(client)
        return
    me: api.Me = st.session_state.me
    with st.sidebar:
        st.write(f"**{me.username}** ({me.role})")
        st.caption("May read: " + ", ".join(me.access_levels))
        if st.button("Log out"):
            client.logout()
            st.session_state.clear()
            st.rerun()
    tabs = ["Ask", "Documents"] + (["Upload"] if me.is_admin else [])
    views = dict(zip(tabs, st.tabs(tabs), strict=True))
    try:
        with views["Ask"]:
            ask_tab(client)
        with views["Documents"]:
            documents_tab(client)
        if "Upload" in views:
            with views["Upload"]:
                upload_tab(client)
    except api.ApiError as exc:
        if exc.status == 401:  # the token expired or the user was deactivated
            st.session_state.clear()
            st.warning("Your session has ended. Please log in again.")
            st.rerun()
        st.error(str(exc))


main()
