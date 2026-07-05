import streamlit as st
import requests
import uuid

st.set_page_config(page_title="SmartDentist AI", page_icon="🦷", layout="centered")

st.title("🦷 SmartDentist: AI Booking Assistant")
st.write("Welcome to Bright Smiles Dental Clinic! How can I help you today?")

# Direct execution endpoints based on Google ADK structure
FASTAPI_RUN_URL = "http://localhost:8000/run"

# Persist stable IDs across renders
if "user_id" not in st.session_state:
    st.session_state.user_id = str(uuid.uuid4())

if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())

if "session_initialized" not in st.session_state:
    st.session_state.session_initialized = False

# Initialize chat history
if "messages" not in st.session_state:
    st.session_state.messages = []

# Display chat messages from history on app rerun
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# React to user input
if user_query := st.chat_input("Type your message here..."):
    st.session_state.messages.append({"role": "user", "content": user_query})
    with st.chat_message("user"):
        st.markdown(user_query)

    with st.chat_message("assistant"):
        response_placeholder = st.empty()
        
        try:
            # Step 1: Initialize the session dynamically if not already registered in backend store
            if not st.session_state.session_initialized:
                session_creation_url = f"http://localhost:8000/apps/app/users/{st.session_state.user_id}/sessions/{st.session_state.session_id}"
                # Registering the session ID into the core memory layer
                init_res = requests.post(session_creation_url, json={}, timeout=10)
                if init_res.status_code in [200, 201]:
                    st.session_state.session_initialized = True
            
            # Step 2: Formulate standard payload and hit execution endpoint
            payload = {
                "input": user_query,
                "userId": st.session_state.user_id,
                "sessionId": st.session_state.session_id,
                "app_name": "app"
            }
            
            res = requests.post(FASTAPI_RUN_URL, json=payload, timeout=30)
            
            if res.status_code == 200:
                data = res.json()
                if isinstance(data, dict):
                    agent_response = data.get("output", data.get("response", data.get("text", str(data))))
                else:
                    agent_response = str(data)
            else:
                agent_response = f"⚠️ Server Error {res.status_code}\n\n**Raw Response:** `{res.text}`"
                
        except requests.exceptions.ConnectionError:
            agent_response = "❌ Backend server off hai boss! Pehle uvicorn server start karein."
        except Exception as e:
            agent_response = f"💥 Error occurred during handshakes: {str(e)}"

        response_placeholder.markdown(agent_response)
        st.session_state.messages.append({"role": "assistant", "content": agent_response})