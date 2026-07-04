# ruff: noqa
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

"""
Biz Booking Agent — Phase 2: Multi-Agent Architecture
======================================================
Workflow graph:
  START
    → input_classifier      (FunctionNode: classifies query type)
    → security_checkpoint   (FunctionNode: PII scrub + injection detect)
    → faq_agent             (LlmAgent: handles FAQs / info queries)
    → booking_agent         (LlmAgent: handles appointment bookings)
    → escalation_agent      (LlmAgent: handles complex / human-review cases)
    → human_approval        (FunctionNode: RequestInput pause for bookings)
    → final_output          (FunctionNode: formats and returns response)

Edge routing:
  input_classifier  → security_checkpoint  (always)
  security_checkpoint → faq_agent          (route="FAQ")
  security_checkpoint → booking_agent      (route="BOOKING")
  security_checkpoint → escalation_agent   (route="ESCALATE" | "SECURITY_EVENT")
  faq_agent         → final_output         (always — unconditional)
  booking_agent     → human_approval       (always — HITL for every booking)
  human_approval    → final_output         (always — unconditional)
  escalation_agent  → final_output         (always — unconditional)
"""

import json
import logging
import re
from typing import Any

from google.adk import Context
from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.events import RequestInput
from google.adk.tools import AgentTool
from google.adk.workflow import Edge, START, Workflow, node

from .config import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Business Knowledge Base (in-memory RAG substitute for demo)
# ---------------------------------------------------------------------------
BUSINESS_KNOWLEDGE: dict[str, Any] = {
    "business_name": "Bright Smiles Dental Clinic",
    "services": {
        "dental_checkup": {"name": "General Dental Checkup", "price": "Rs. 1,500", "duration": "45 min"},
        "teeth_cleaning": {"name": "Professional Teeth Cleaning", "price": "Rs. 2,500", "duration": "60 min"},
        "tooth_extraction": {"name": "Tooth Extraction", "price": "Rs. 3,000–6,000", "duration": "30–60 min"},
        "root_canal": {"name": "Root Canal Treatment", "price": "Rs. 12,000–18,000", "duration": "90 min"},
        "teeth_whitening": {"name": "Teeth Whitening", "price": "Rs. 8,000", "duration": "75 min"},
        "braces_consultation": {"name": "Braces Consultation", "price": "Free first visit", "duration": "30 min"},
    },
    "timings": "Monday–Saturday: 9:00 AM – 7:00 PM, Sundays: 10:00 AM – 2:00 PM (emergency only)",
    "location": "Office 3B, Al-Rehman Plaza, Main Boulevard, Lahore",
    "contact": "+92-42-3456-7890",
    "doctors": ["Dr. Ayesha Khan (Senior Dentist)", "Dr. Umar Farooq (Orthodontist)"],
    "faqs": {
        "payment": "We accept cash, Easypaisa, JazzCash, and all major debit/credit cards.",
        "insurance": "We are on panel with Jubilee Life, EFU Health, and State Life insurance plans.",
        "parking": "Free parking available in the building basement.",
        "emergency": "For dental emergencies, call our emergency line: +92-333-456-7890 (24/7).",
        "cancellation": "Please cancel at least 2 hours before your appointment to avoid a no-show fee.",
    },
}


def knowledge_lookup(topic: str) -> str:
    """Look up business information from the knowledge base.

    Args:
        topic: The topic to look up (services, timings, location, faqs, etc.)

    Returns:
        A formatted string with the requested information.
    """
    topic_lower = topic.lower()
    kb = BUSINESS_KNOWLEDGE

    if "service" in topic_lower or "price" in topic_lower or "cost" in topic_lower:
        lines = [f"**{kb['business_name']} — Services & Prices**\n"]
        for svc in kb["services"].values():
            lines.append(f"• {svc['name']}: {svc['price']} ({svc['duration']})")
        return "\n".join(lines)

    if "time" in topic_lower or "hour" in topic_lower or "open" in topic_lower:
        return f"**Opening Hours:** {kb['timings']}"

    if "location" in topic_lower or "address" in topic_lower or "where" in topic_lower:
        return f"**Location:** {kb['location']}"

    if "contact" in topic_lower or "phone" in topic_lower or "number" in topic_lower:
        return f"**Contact:** {kb['contact']}"

    if "doctor" in topic_lower or "staff" in topic_lower:
        return "**Our Doctors:** " + ", ".join(kb["doctors"])

    if "payment" in topic_lower or "pay" in topic_lower:
        return "**Payment:** " + kb["faqs"]["payment"]

    if "insurance" in topic_lower:
        return "**Insurance:** " + kb["faqs"]["insurance"]

    if "park" in topic_lower:
        return "**Parking:** " + kb["faqs"]["parking"]

    if "emergency" in topic_lower:
        return "**Emergency:** " + kb["faqs"]["emergency"]

    if "cancel" in topic_lower:
        return "**Cancellation Policy:** " + kb["faqs"]["cancellation"]

    # Default: return full summary
    return json.dumps({
        "business": kb["business_name"],
        "timings": kb["timings"],
        "location": kb["location"],
        "contact": kb["contact"],
        "services_count": len(kb["services"]),
    }, indent=2)


# ---------------------------------------------------------------------------
# Sub-agents
# ---------------------------------------------------------------------------

faq_agent = LlmAgent(
    name="faq_agent",
    model=config.model,
    instruction=f"""You are the FAQ specialist for {BUSINESS_KNOWLEDGE['business_name']}.
Your role is to answer customer questions about services, prices, timings, location, payments, insurance, and general clinic information.

BUSINESS DATA:
{json.dumps(BUSINESS_KNOWLEDGE, indent=2)}

Guidelines:
- Be warm, concise, and professional.
- Always quote exact prices and timings from the data above.
- If you cannot find the answer, say so and suggest calling {BUSINESS_KNOWLEDGE['contact']}.
- Do NOT book appointments — only provide information.
- Respond in the same language the customer used (English or Urdu).
""",
    tools=[knowledge_lookup],
    output_key="faq_response",
)

booking_agent = LlmAgent(
    name="booking_agent",
    model=config.model,
    instruction=f"""You are the booking specialist for {BUSINESS_KNOWLEDGE['business_name']}.
Your role is to collect all information needed to book an appointment.

AVAILABLE SERVICES:
{json.dumps({k: v['name'] + ' — ' + v['price'] for k, v in BUSINESS_KNOWLEDGE['services'].items()}, indent=2)}

OPENING HOURS: {BUSINESS_KNOWLEDGE['timings']}

You MUST collect ALL of these fields before confirming:
1. Patient full name
2. Contact number (Pakistani mobile format preferred: 03XX-XXXXXXX)
3. Service requested (match to one of the services above)
4. Preferred date (e.g., "2026-07-10" or "this Thursday")
5. Preferred time slot (morning 9-12 / afternoon 12-4 / evening 4-7)
6. Doctor preference (optional)

Output a JSON booking summary when all fields are collected, in this format:
{{
  "patient_name": "...",
  "contact": "...",
  "service": "...",
  "price": "...",
  "date": "...",
  "time_slot": "...",
  "doctor_preference": "...",
  "booking_id": "BK-XXXX"
}}

Be friendly and collect missing fields conversationally. Generate a booking ID like BK-{{}}.
""",
    output_key="booking_summary",
)

escalation_agent = LlmAgent(
    name="escalation_agent",
    model=config.model,
    instruction=f"""You are the escalation handler for {BUSINESS_KNOWLEDGE['business_name']}.
Your role is to handle:
1. Complex medical/dental queries requiring doctor's advice
2. Complaints or urgent issues
3. Queries flagged for human review
4. Cases where standard FAQ and booking flows couldn't resolve the issue

Guidelines:
- Empathize with the customer's situation
- Collect their name and contact number if not already provided
- Explain that a clinic representative will call them back within 2 hours during business hours
- For emergencies, immediately provide: {BUSINESS_KNOWLEDGE['faqs']['emergency']}
- Log a reference ticket number (ESC-XXXX format)
- Do NOT make medical diagnoses
""",
    output_key="escalation_response",
)

# Orchestrator uses sub-agents as tools
orchestrator = LlmAgent(
    name="orchestrator",
    model=config.model,
    instruction="""You are the smart orchestrator for a business support & booking system.
Analyze the customer message and classify it into ONE of these intents:
- FAQ: questions about services, prices, timings, location, payments, insurance, cancellation, parking
- BOOKING: requests to book, schedule, or make an appointment
- ESCALATE: complaints, medical advice requests, complex issues, anything ambiguous

Return ONLY one of these three words: FAQ, BOOKING, or ESCALATE
""",
    output_key="intent",
)

# ---------------------------------------------------------------------------
# Workflow function nodes
# ---------------------------------------------------------------------------


@node
def input_classifier(ctx: Context, node_input: Any) -> dict:
    """Classify the incoming user query and store it in state."""
    # Normalise input: it may arrive as a string or dict
    if isinstance(node_input, dict):
        user_message = node_input.get("message", str(node_input))
    else:
        user_message = str(node_input) if node_input else ""

    ctx.state["user_message"] = user_message
    ctx.state["intent"] = "UNKNOWN"
    ctx.state["booking_confirmed"] = False
    logger.info("[input_classifier] user_message: %r", user_message[:120])
    return {"user_message": user_message}


@node
def security_checkpoint(ctx: Context) -> dict:
    """PII scrubbing, injection detection, and audit logging.

    Routes:
      - SECURITY_EVENT → escalation_agent  (if injection detected)
      - FAQ / BOOKING / ESCALATE           (based on orchestrator intent)
    """
    user_message = ctx.state.get("user_message", "")
    audit: dict[str, Any] = {
        "node": "security_checkpoint",
        "original_length": len(user_message),
        "pii_scrubbed": False,
        "injection_detected": False,
        "severity": "INFO",
        "intent": "UNKNOWN",
    }

    # --- PII Scrubbing (domain: phone numbers, CNIC, email) ---
    if config.pii_redaction_enabled:
        # Pakistani phone numbers
        scrubbed = re.sub(r"\b(0?3\d{2}[-\s]?\d{7}|\+92[-\s]?\d{10,11})\b", "[PHONE_REDACTED]", user_message)
        # CNIC format
        scrubbed = re.sub(r"\b\d{5}-\d{7}-\d\b", "[CNIC_REDACTED]", scrubbed)
        # Email addresses
        scrubbed = re.sub(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b", "[EMAIL_REDACTED]", scrubbed)
        if scrubbed != user_message:
            audit["pii_scrubbed"] = True
            audit["severity"] = "WARNING"
            ctx.state["user_message"] = scrubbed
            user_message = scrubbed

    # --- Prompt Injection Detection ---
    injection_keywords = [
        "ignore previous", "ignore all", "disregard instructions", "system prompt",
        "jailbreak", "act as", "you are now", "forget your", "new persona",
        "sudo", "override", "bypass", "##instructions", "[[instructions]]",
    ]
    if config.injection_detection_enabled:
        msg_lower = user_message.lower()
        for kw in injection_keywords:
            if kw in msg_lower:
                audit["injection_detected"] = True
                audit["severity"] = "CRITICAL"
                audit["injection_keyword"] = kw
                logger.warning("[security_checkpoint] INJECTION DETECTED: %r", kw)
                ctx.state["security_event"] = True
                ctx.state["audit_log"] = audit
                logger.info("[AUDIT] %s", json.dumps(audit))
                ctx.route = "ESCALATE"
                return {"security_event": True, "audit": audit}

    # --- Domain-specific rule: dental emergency fast-track ---
    emergency_keywords = ["toothache", "severe pain", "swollen", "bleeding", "emergency", "urgent", "درد", "ایمرجنسی"]
    msg_lower = user_message.lower()
    is_emergency = any(kw in msg_lower for kw in emergency_keywords)
    if is_emergency:
        audit["severity"] = "WARNING"
        ctx.state["is_emergency"] = True
        logger.warning("[security_checkpoint] Emergency keywords detected")

    # --- Intent classification via LLM ---
    # Simple keyword-based pre-classifier to save tokens
    booking_keywords = ["book", "appointment", "schedule", "reserve", "appoint", "slot", "بک", "اپوائنٹمنٹ"]
    faq_keywords = ["price", "cost", "fee", "time", "hour", "open", "location", "address", "doctor", "service",
                    "payment", "insurance", "cancel", "park", "قیمت", "وقت", "کتنا"]

    if any(kw in msg_lower for kw in booking_keywords):
        intent = "BOOKING"
    elif any(kw in msg_lower for kw in faq_keywords):
        intent = "FAQ"
    elif is_emergency:
        intent = "ESCALATE"
    else:
        intent = "FAQ"  # Default to FAQ for general queries

    ctx.state["intent"] = intent
    audit["intent"] = intent
    ctx.state["audit_log"] = audit
    logger.info("[AUDIT] %s", json.dumps(audit))

    ctx.route = intent
    return {"intent": intent, "audit": audit}


@node
def human_approval(ctx: Context) -> dict:
    """HITL: Pause and ask the human to confirm the booking details."""
    booking_summary = ctx.state.get("booking_summary", "Booking details not yet collected.")
    interrupt_id = f"booking_confirm_{ctx.run_id}"

    # Check if we already have a resume input (human has responded)
    resume = ctx.resume_inputs.get(interrupt_id)
    if resume is not None:
        confirmed = str(resume).strip().lower() in ("yes", "confirm", "ok", "proceed", "y", "ہاں")
        ctx.state["booking_confirmed"] = confirmed
        ctx.state["human_decision"] = "CONFIRMED" if confirmed else "CANCELLED"
        logger.info("[human_approval] Human decision: %s", ctx.state["human_decision"])
        return {"confirmed": confirmed, "decision": ctx.state["human_decision"]}

    # First time: yield RequestInput to pause execution
    yield RequestInput(
        interrupt_id=interrupt_id,
        message=(
            f"📋 **Booking Summary Ready — Please Confirm**\n\n"
            f"{booking_summary}\n\n"
            f"Type **yes** to confirm this booking, or **no** to cancel."
        ),
    )


@node
def final_output(ctx: Context) -> str:
    """Assemble the final response from whichever agent handled the request."""
    intent = ctx.state.get("intent", "FAQ")
    booking_confirmed = ctx.state.get("booking_confirmed", False)
    human_decision = ctx.state.get("human_decision", "")
    business_name = BUSINESS_KNOWLEDGE["business_name"]

    if intent == "BOOKING":
        booking_summary = ctx.state.get("booking_summary", "")
        if booking_confirmed:
            response = (
                f"✅ **Booking Confirmed!**\n\n"
                f"{booking_summary}\n\n"
                f"We'll send a reminder 1 hour before your appointment. "
                f"For any changes, call {BUSINESS_KNOWLEDGE['contact']}. "
                f"Thank you for choosing {business_name}!"
            )
        elif human_decision == "CANCELLED":
            response = (
                f"❌ **Booking Cancelled**\n\n"
                f"Your booking request has been cancelled. "
                f"Feel free to book again anytime or call us at {BUSINESS_KNOWLEDGE['contact']}."
            )
        else:
            response = (
                f"⏳ **Booking Pending**\n\n"
                f"{booking_summary}\n\n"
                f"Please confirm above to finalize your appointment."
            )
    elif intent == "ESCALATE":
        security_event = ctx.state.get("security_event", False)
        if security_event:
            response = (
                "⚠️ **Security Alert**\n\n"
                "Your message contained content that could not be processed. "
                "Please rephrase your question or contact us directly at "
                f"{BUSINESS_KNOWLEDGE['contact']}."
            )
        else:
            response = ctx.state.get("escalation_response", "Your case has been escalated. A representative will contact you shortly.")
    else:
        response = ctx.state.get("faq_response", "I'm sorry, I couldn't find the information. Please call " + BUSINESS_KNOWLEDGE["contact"])

    logger.info("[final_output] intent=%s, response_length=%d", intent, len(response))
    ctx.state["final_response"] = response
    return response


# ---------------------------------------------------------------------------
# Build the Workflow graph
# ---------------------------------------------------------------------------
# IMPORTANT: The ADK Workflow validator rejects duplicate (source, target)
# edges. We therefore use a SINGLE unconditional edge from each terminal
# agent into final_output. Conditional routing is done via ctx.route inside
# security_checkpoint and the intent stored in ctx.state.

faq_node   = node(faq_agent,        name="faq_node")
book_node  = node(booking_agent,    name="booking_node")
esc_node   = node(escalation_agent, name="escalation_node")

booking_workflow = Workflow(
    name="biz_booking_workflow",
    edges=[
        # START → input classifier
        Edge(from_node=START, to_node=input_classifier),
        # input classifier → security checkpoint
        Edge(from_node=input_classifier, to_node=security_checkpoint),
        # security checkpoint routes by ctx.route
        Edge(from_node=security_checkpoint, to_node=faq_node,   route="FAQ"),
        Edge(from_node=security_checkpoint, to_node=book_node,  route="BOOKING"),
        Edge(from_node=security_checkpoint, to_node=esc_node,   route="ESCALATE"),
        # FAQ → single unconditional edge to final_output
        Edge(from_node=faq_node,  to_node=final_output),
        # Booking → HITL approval → final_output
        Edge(from_node=book_node,   to_node=human_approval),
        Edge(from_node=human_approval, to_node=final_output),
        # Escalation → single unconditional edge to final_output
        Edge(from_node=esc_node, to_node=final_output),
    ],
)

# ADK App exposes the workflow as the root agent
app = App(
    root_agent=booking_workflow,
    name="app",
)
