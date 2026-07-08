# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""FastAPI application for biz-booking-agent.

Local dev: no GCP credentials required — uses Gemini API key from .env.
Production: set GOOGLE_CLOUD_PROJECT + GOOGLE_GENAI_USE_VERTEXAI=True.
"""

import contextlib
import logging
import os
from collections.abc import AsyncIterator

from a2a.server.tasks import InMemoryTaskStore
from dotenv import load_dotenv
import json
import urllib.request
from fastapi import FastAPI, Request, Response, Query, HTTPException, File, UploadFile
from google.adk.cli.fast_api import get_fast_api_app
from google.adk.runners import Runner
from app.config import config

from app.app_utils import services

load_dotenv()

# Standard Python logger (no GCP dependency for local dev)
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)

allow_origins = (
    os.getenv("ALLOW_ORIGINS", "").split(",") if os.getenv("ALLOW_ORIGINS") else None
)

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Lazy import so agent is built AFTER env/telemetry setup
    from app.agent import app as adk_app
    from app.agent import root_agent

    runner = Runner(
        app=adk_app,
        session_service=services.get_session_service(),
        artifact_service=services.get_artifact_service(),
        auto_create_session=True,
    )
    app.state.runner = runner
    app.state.agent_app_name = adk_app.name

    # A2A routes (optional — only if a2a integration is needed)
    try:
        from app.app_utils.a2a import attach_a2a_routes
        await attach_a2a_routes(
            app,
            agent=root_agent,
            runner=runner,
            task_store=InMemoryTaskStore(),
            rpc_path=f"/a2a/{adk_app.name}",
        )
    except Exception as exc:
        logger.warning("A2A routes not attached: %s", exc)

    yield


app: FastAPI = get_fast_api_app(
    agents_dir=AGENT_DIR,
    web=True,
    artifact_service_uri=services.ARTIFACT_SERVICE_URI,
    allow_origins=allow_origins,
    session_service_uri=services.SESSION_SERVICE_URI,
    otel_to_cloud=False,
    lifespan=lifespan,
)
app.title = "biz-booking-agent"
app.description = "AI Support & Booking Agent for Local Businesses"


# Optional: Reasoning Engine proxy routes for Vertex AI Console
try:
    from app.app_utils.reasoning_engine_adapter import attach_reasoning_engine_routes
    attach_reasoning_engine_routes(app)
except Exception as exc:
    logger.debug("Reasoning engine routes skipped: %s", exc)


def send_whatsapp_message(to_phone: str, text_body: str) -> None:
    """Send text message to WhatsApp via Graph API using urllib."""
    url = f"https://graph.facebook.com/v20.0/{config.whatsapp_phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {config.whatsapp_api_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_phone,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": text_body
        }
    }
    
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            res_body = response.read().decode("utf-8")
            logger.info("Sent WhatsApp message to %s successfully. Response: %s", to_phone, res_body)
    except Exception as e:
        logger.error("Failed to send WhatsApp message to %s: %s", to_phone, e)


@app.get("/webhook")
def whatsapp_webhook_verification(
    mode: str = Query(None, alias="hub.mode"),
    token: str = Query(None, alias="hub.verify_token"),
    challenge: str = Query(None, alias="hub.challenge"),
):
    """WhatsApp Webhook verification endpoint (handshake)."""
    if mode == "subscribe" and token == config.whatsapp_verify_token:
        logger.info("WhatsApp webhook verified successfully!")
        return Response(content=challenge, media_type="text/plain")
    logger.warning("WhatsApp webhook verification failed. Token mismatch.")
    raise HTTPException(status_code=403, detail="Verification token mismatch")


@app.post("/webhook")
async def whatsapp_webhook_handler(request: Request):
    """Receive messages from Meta (WhatsApp Cloud API) and reply using ADK."""
    try:
        payload = await request.json()
    except Exception as e:
        logger.error("Failed to parse JSON body: %s", e)
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    logger.info("Received WhatsApp payload: %s", json.dumps(payload))
    
    # Check if this is a message object
    entry = payload.get("entry", [])
    if not entry:
        return {"status": "ignored", "reason": "No entry field"}
    
    changes = entry[0].get("changes", [])
    if not changes:
        return {"status": "ignored", "reason": "No changes field"}
        
    value = changes[0].get("value", {})
    messages = value.get("messages", [])
    if not messages:
        statuses = value.get("statuses", [])
        if statuses:
            logger.info("Received status update from WhatsApp: %s", statuses)
            return {"status": "ignored", "reason": "Status update"}
        return {"status": "ignored", "reason": "No messages in changes"}
        
    msg = messages[0]
    msg_type = msg.get("type")
    
    if msg_type != "text":
        logger.info("Ignored non-text message of type: %s", msg_type)
        return {"status": "ignored", "reason": "Non-text message"}
        
    from_phone = msg.get("from")
    message_text = msg.get("text", {}).get("body", "").strip()
    
    if not from_phone or not message_text:
        return {"status": "ignored", "reason": "Missing sender phone or message text"}
        
    logger.info("Processing WhatsApp message from %s: %s", from_phone, message_text)
    
    # Isolate conversation session per user phone number
    user_id = f"wa-{from_phone}"
    session_id = f"wa-session-{from_phone}"
    
    from google.genai import types as genai_types
    new_message = genai_types.Content(
        role="user",
        parts=[genai_types.Part.from_text(text=message_text)],
    )
    
    try:
        runner = request.app.state.runner
    except AttributeError:
        from google.adk.runners import Runner
        from app.agent import app as adk_app
        runner = Runner(
            app=adk_app,
            session_service=services.get_session_service(),
            artifact_service=services.get_artifact_service(),
            auto_create_session=True,
        )
    
    session_service = services.get_session_service()
    from app.agent import app as adk_app
    try:
        await session_service.create_session(
            app_name=adk_app.name,
            user_id=user_id,
            session_id=session_id
        )
    except Exception:
        pass
        
    texts: list[str] = []
    try:
        async for event in runner.run_async(
            new_message=new_message,
            user_id=user_id,
            session_id=session_id,
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if getattr(part, "text", None):
                        texts.append(part.text)
    except Exception as e:
        logger.error("Error executing agent: %s", e)
        texts = ["I apologize, but I encountered an issue processing your request. Please try again or call the clinic."]
        
    agent_response = "".join(texts)
    logger.info("Agent response generated for %s: %r", from_phone, agent_response)
    
    if config.whatsapp_api_token and config.whatsapp_phone_number_id:
        send_whatsapp_message(from_phone, agent_response)
    else:
        logger.warning(
            "WhatsApp credentials not set in environment (WHATSAPP_API_TOKEN or WHATSAPP_PHONE_NUMBER_ID is empty). "
            "Skipping outbound message delivery. Logged agent output: %r",
            agent_response
        )
        
    return {"status": "success", "response": agent_response}


@app.post("/webhook/whatsapp/audio")
async def handle_whatsapp_voice(file: UploadFile = File(...)):
    """Trial Endpoint to stream local voice messages directly into Gemini Multimodal Engine."""
    try:
        from google.genai import Client
        from google.genai import types as genai_types
        
        # Initializes natively via your env token
        client = Client()
        audio_bytes = await file.read()
        
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[
                genai_types.Part.from_bytes(
                    data=audio_bytes,
                    mime_type=file.content_type,
                ),
                "Listen carefully to this patient query. Transcribe and respond to it efficiently."
            ]
        )
        return {"status": "success", "reply": response.text}
    except Exception as e:
        logger.error("Voice processing failed: %s", e)
        return {"status": "error", "message": str(e)}


@app.post("/feedback")
def collect_feedback(feedback: dict) -> dict:
    """Collect and log feedback.

    Args:
        feedback: The feedback data to log

    Returns:
        Success message
    """
    logger.info("Feedback received: %s", feedback)
    return {"status": "success"}


# Main execution
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)