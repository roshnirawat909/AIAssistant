import os
import hashlib
import json
import sqlite3
import uuid
import streamlit as st
from dotenv import load_dotenv
from groq import Groq
from streamlit.errors import StreamlitSecretNotFoundError

load_dotenv()

try:
    api_key = st.secrets.get("GROQ_API_KEY")
except StreamlitSecretNotFoundError:
    api_key = None

api_key = api_key or os.getenv("GROQ_API_KEY")
if not api_key:
    st.error(
        "Groq API key is missing. Add GROQ_API_KEY to Streamlit Cloud Secrets "
        "or create a local .env file."
    )
    st.stop()

client = Groq(api_key=api_key)
model = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
database_path = os.path.join(os.path.dirname(__file__), "assistant.db")


def get_connection():
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database():
    with get_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chats (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                messages TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (id)
            );
            """
        )


def hash_password(password):
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def load_user_chats(user_id):
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT id, title, messages FROM chats WHERE user_id = ? ORDER BY rowid DESC",
            (user_id,),
        ).fetchall()
    return [
        {"id": row["id"], "title": row["title"], "messages": json.loads(row["messages"])}
        for row in rows
    ]


def save_chat(chat):
    if st.session_state.user_id is None:
        return
    with get_connection() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO chats (id, user_id, title, messages) VALUES (?, ?, ?, ?)",
            (
                chat["id"],
                st.session_state.user_id,
                chat["title"],
                json.dumps(chat["messages"]),
            ),
        )


def delete_chat_from_database(chat_id):
    if st.session_state.user_id is None:
        return
    with get_connection() as connection:
        connection.execute("DELETE FROM chats WHERE id = ? AND user_id = ?", (chat_id, st.session_state.user_id))


def new_chat():
    chat = {"id": uuid.uuid4().hex, "title": "New chat", "messages": []}
    st.session_state.chats.insert(0, chat)
    st.session_state.active_chat_id = chat["id"]
    save_chat(chat)


def delete_chat(chat_id):
    delete_chat_from_database(chat_id)
    st.session_state.chats = [chat for chat in st.session_state.chats if chat["id"] != chat_id]
    if not st.session_state.chats:
        new_chat()
    else:
        st.session_state.active_chat_id = st.session_state.chats[0]["id"]


def active_chat():
    return next(chat for chat in st.session_state.chats if chat["id"] == st.session_state.active_chat_id)


initialize_database()

if "user_id" not in st.session_state:
    st.session_state.user_id = None
    st.session_state.username = None
    st.session_state.chats = [{"id": uuid.uuid4().hex, "title": "New chat", "messages": []}]
    st.session_state.active_chat_id = st.session_state.chats[0]["id"]


def login(username, password):
    with get_connection() as connection:
        user = connection.execute(
            "SELECT id, username FROM users WHERE username = ? AND password_hash = ?",
            (username.strip(), hash_password(password)),
        ).fetchone()
    if user is None:
        return False
    st.session_state.user_id = user["id"]
    st.session_state.username = user["username"]
    st.session_state.chats = load_user_chats(user["id"])
    if not st.session_state.chats:
        new_chat()
    st.session_state.active_chat_id = st.session_state.chats[0]["id"]
    return True


def register(username, password):
    username = username.strip()
    if not username or not password:
        return False, "Username and password are required."
    try:
        with get_connection() as connection:
            connection.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (username, hash_password(password)),
            )
    except sqlite3.IntegrityError:
        return False, "That username is already registered."
    return True, "Registration successful. Please log in to continue."


def update_profile(username, password):
    username = username.strip()
    if not username:
        return False, "Username is required."
    try:
        with get_connection() as connection:
            if password:
                connection.execute(
                    "UPDATE users SET username = ?, password_hash = ? WHERE id = ?",
                    (username, hash_password(password), st.session_state.user_id),
                )
            else:
                connection.execute(
                    "UPDATE users SET username = ? WHERE id = ?",
                    (username, st.session_state.user_id),
                )
    except sqlite3.IntegrityError:
        return False, "That username is already registered."
    st.session_state.username = username
    return True, "Profile updated successfully."


def delete_account():
    user_id = st.session_state.user_id
    with get_connection() as connection:
        connection.execute("DELETE FROM chats WHERE user_id = ?", (user_id,))
        connection.execute("DELETE FROM users WHERE id = ?", (user_id,))
    logout()


def logout():
    st.session_state.user_id = None
    st.session_state.username = None
    guest_chat = {"id": uuid.uuid4().hex, "title": "New chat", "messages": []}
    st.session_state.chats = [guest_chat]
    st.session_state.active_chat_id = guest_chat["id"]


if "next_page" in st.session_state:
    st.session_state.page = st.session_state.pop("next_page")

page = st.radio(
    "Navigation",
    ["Assistant", "Profile", "Logout"]
    if st.session_state.user_id is not None
    else ["Assistant", "Login", "Register"],
    horizontal=True,
    label_visibility="collapsed",
    key="page",
)

if page == "Logout":
    logout()
    st.session_state.next_page = "Assistant"
    st.rerun()

if page == "Profile":
    st.title("Profile")
    st.write(f"Signed in as **{st.session_state.username}**")
    with st.form("profile_form"):
        new_username = st.text_input("Username", value=st.session_state.username)
        new_password = st.text_input(
            "New password (leave blank to keep current password)", type="password"
        )
        submitted_profile = st.form_submit_button("Update profile")
    if submitted_profile:
        updated, message = update_profile(new_username, new_password)
        if updated:
            st.success(message)
        else:
            st.error(message)

    st.divider()
    st.subheader("Delete account")
    st.warning("Deleting your account permanently removes your saved chats.")
    with st.form("delete_account_form"):
        confirm_delete = st.checkbox("I understand that this cannot be undone")
        submitted_delete = st.form_submit_button("Delete account")
    if submitted_delete:
        if confirm_delete:
            delete_account()
            st.session_state.next_page = "Assistant"
            st.rerun()
        else:
            st.error("Confirm the deletion before continuing.")
    st.stop()

if page == "Login":
    st.title("Login")
    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted_login = st.form_submit_button("Login")
    if submitted_login:
        if login(username, password):
            st.session_state.next_page = "Assistant"
            st.success("Login successful.")
            st.rerun()
        else:
            st.error("Invalid username or password.")
    st.stop()

if page == "Register":
    st.title("Register")
    with st.form("register_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        confirm_password = st.text_input("Confirm password", type="password")
        submitted_register = st.form_submit_button("Register")
    if submitted_register:
        if password != confirm_password:
            st.error("Passwords do not match.")
        else:
            registered, message = register(username, password)
            if registered:
                st.session_state.next_page = "Login"
                st.success(message)
                st.rerun()
            else:
                st.error(message)
    st.stop()


with st.container(horizontal=True):
    st.subheader("🤖 AI Assistant")
    st.caption(f"Signed in as {st.session_state.username}" if st.session_state.username else "Guest mode")
    if st.session_state.user_id is not None and st.button("Logout"):
        logout()
        st.rerun()


with st.sidebar:
    st.title("Chats")
    if st.button("+ New chat", use_container_width=True):
        new_chat()
        st.rerun()

    st.divider()
    for chat in st.session_state.chats:
        with st.container(horizontal=True):
            if st.button(
                chat["title"],
                key=f"chat_{chat['id']}",
                use_container_width=True,
                type="primary" if chat["id"] == st.session_state.active_chat_id else "secondary",
            ):
                st.session_state.active_chat_id = chat["id"]
                st.rerun()
            if st.button("×", key=f"delete_{chat['id']}", help="Delete this chat"):
                delete_chat(chat["id"])
                st.rerun()

active = active_chat()

if st.session_state.user_id is None:
    st.info("You can use the assistant as a guest. Register or log in to save chats after refresh.")

with st.form("question_form", clear_on_submit=True):
    question = st.text_input("Ask your question")
    submitted = st.form_submit_button("Ask")

if submitted and question:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "You are a helpful AI assistant."
            },
            {
                "role": "user",
                "content": question
            }
        ]
    )

    answer = response.choices[0].message.content
    active["messages"].extend(
        [
            {"role": "user", "content": question},
            {"role": "assistant", "content": answer},
        ]
    )
    if active["title"] == "New chat":
        active["title"] = question[:32]
    save_chat(active)

for message in active["messages"]:
    with st.chat_message(message["role"]):
        st.write(message["content"])