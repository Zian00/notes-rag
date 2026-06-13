import streamlit as st
from core.embeddings import load_vector_db
from core.ingestion import ingest_files
from core.intent import is_summary_intent
from core.retrieval import retrieve_for_summary, retrieve_for_qa
from core.chains import load_llm, run_summary_chain, run_qa
from utils.parsing import extract_lecture_id

# =========================
# STREAMLIT SETUP
# =========================
st.set_page_config(page_title="Lecture Notes RAG", layout="wide")
st.title("📚 Lecture Notes RAG")

# Initialize Chat History
if "messages" not in st.session_state:
    st.session_state.messages = []


@st.cache_resource
def load_components():
    return load_vector_db(), load_llm()


db, llm = load_components()

# =========================
# SIDEBAR
# =========================
with st.sidebar:
    st.header("📤 Upload Notes")

    try:
        st.metric("Documents in Database", db._collection.count())
    except:
        st.metric("Documents in Database", 0)

    if st.button("🗑️ Clear Database"):
        ids = db._collection.get()["ids"]
        if ids:
            db._collection.delete(ids=ids)
            st.success("Database cleared")
            st.rerun()

    uploaded_files = st.file_uploader(
        "Upload lecture notes (PDF, TXT, MD)",
        type=["pdf", "txt", "md"],
        accept_multiple_files=True
    )

    if st.button("Process Files", disabled=not uploaded_files):
        added = ingest_files(uploaded_files, db)
        st.success(f"✅ Added {added} chunks")
        st.rerun()

# =========================
# DISPLAY CHAT HISTORY
# =========================
for msg in st.session_state.messages:
    st.chat_message(msg["role"]).write(msg["content"])

# =========================
# CHAT INPUT
# =========================
if prompt := st.chat_input("Ask about your notes"):
    # Save user message
    st.session_state.messages.append({"role": "user", "content": prompt})
    st.chat_message("user").write(prompt)

    lecture_id = extract_lecture_id(prompt)
    summary_intent = lecture_id and is_summary_intent(prompt)

    if summary_intent:
        with st.spinner("🔍 Retrieving lecture content..."):
            docs = retrieve_for_summary(db, lecture_id)

        if not docs:
            st.error("❌ Lecture not found in uploaded documents.")
            st.stop()

        answer = run_summary_chain(llm, docs)

    else:
        with st.spinner("🔍 Searching relevant content..."):
            docs = retrieve_for_qa(db, prompt)

        with st.spinner("💭 Generating answer..."):
            answer = run_qa(llm, docs, prompt)

    # Save assistant response and show in chat
    st.session_state.messages.append(
        {"role": "assistant", "content": answer}
    )
    st.chat_message("assistant").write(answer)
