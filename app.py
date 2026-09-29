"""
Step 5 - Streamlit front end for the AML analytics agent.

Run:   streamlit run app.py
Needs: agent.py, aml.db, and your .env in the same folder.
Setup: pip install streamlit
"""

import streamlit as st
import agent

st.set_page_config(page_title="AML Analytics Agent", page_icon="search", layout="centered")

st.title("💸 AML Analytics Agent")
st.caption("Ask a bank transaction database in plain English — it writes the SQL, runs it, and answers.")

with st.expander("About this data"):
    st.markdown(
        "About 255k bank transactions (a sampled slice of the IBM AML dataset), "
        "modeled as two tables:\n\n"
        "- **transactions** -- each transfer: sender, receiver, amount, currency, format, flag\n"
        "- **accounts** -- each account and its bank\n\n"
        "`is_laundering` is a label already present in the data, not a prediction."

    )


def handle(prompt):
    st.chat_message("user").write(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt})
    before = len(st.session_state.messages)
    st.session_state.messages, reply = agent.continue_chat(st.session_state.messages)
    sql = []
    for m in st.session_state.messages[before:]:
        if isinstance(m["content"], list):
            for b in m["content"]:
                if getattr(b, "type", None) == "tool_use":
                    sql.append(b.input["query"])
    with st.chat_message("assistant"):
        st.write(reply)
        for q in sql:
            with st.expander("🔍 SQL the agent wrote", expanded=True):
                st.code(q, language="sql")


if "messages" not in st.session_state:
    st.session_state.messages = []

for m in st.session_state.messages:
    if isinstance(m["content"], str):
        st.chat_message(m["role"]).write(m["content"])

examples = [
    "Which 5 banks received the most flagged money?",
    "What is the flagged rate for each payment format?",
    "Which 10 accounts receive money from the most distinct senders?",
]
cols = st.columns(len(examples))
for i, ex in enumerate(examples):
    if cols[i].button(ex):
        handle(ex)

if prompt := st.chat_input("Ask a question..."):
    handle(prompt)

st.divider()
st.caption(
    "Prototype for portfolio use. Known limits: amount columns mix 15 currencies, "
    "so cross-currency sums are indicative only; rate rankings can surface low-volume "
    "accounts. See the README for details."
)
