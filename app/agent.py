# ruff: noqa
import datetime
import json
import logging
import re
import sys
from typing import Any, AsyncGenerator

from google.adk.agents import Agent, LlmAgent
from google.adk.agents.context import Context
from google.adk.apps import App, ResumabilityConfig
from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput
from google.adk.models import Gemini
from google.adk.tools import AgentTool, ToolContext
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from google.adk.workflow import Workflow, START, FunctionNode, Edge
from google.genai import types
from mcp import StdioServerParameters

from app.config import config

# Setup security logger
logger = logging.getLogger("pet_pal_security")
logging.basicConfig(level=logging.INFO)

# MCP server connection
mcp_connection = StdioConnectionParams(
    server_params=StdioServerParameters(
        command=sys.executable,
        args=["app/mcp_server.py"],
    )
)
mcp_toolset = McpToolset(connection_params=mcp_connection)

# Local proposed appointment tool
def propose_appointment(details: str, tool_context: ToolContext) -> dict:
    """Propose a new vet or grooming appointment details. This will initiate the human approval flow.
    
    Args:
        details: The details of the proposed appointment (e.g. date, time, provider).
        
    Returns:
        A dictionary confirming the proposal has been recorded.
    """
    tool_context.state["appointment_pending"] = True
    tool_context.state["appointment_details"] = details
    return {
        "status": "success",
        "message": f"Appointment proposal recorded for: {details}. Awaiting user approval."
    }

# 1. Specialized sub-agents
appointment_agent = LlmAgent(
    name="appointment_agent",
    model=config.model,
    instruction="""You are a specialized Pet Appointment Scheduler. 
Your role is to check veterinarian/grooming availability and manage booking requests.
You have access to MCP tools for checking availability and booking.
If the user wants to book an appointment, look up available times, propose a time, and then call the custom tool propose_appointment to start the approval flow.
Do not book directly unless you have proposed the appointment first via propose_appointment.
If the user has already approved a previously proposed appointment, you may proceed with the booking MCP tool.""",
    description="Specialist for scheduling vet/grooming appointments and checking availability.",
    tools=[mcp_toolset, propose_appointment],
)

medication_agent = LlmAgent(
    name="medication_agent",
    model=config.model,
    instruction="""You are a specialized Pet Medication Tracker.
Your role is to track medications (names, dosages, schedules, reminders) and help pet owners verify compliance.
You have access to MCP tools to retrieve medication schedules and log new medication tracking events.""",
    description="Specialist for tracking pet medications, dosages, and schedules.",
    tools=[mcp_toolset],
)

routine_agent = LlmAgent(
    name="routine_agent",
    model=config.model,
    instruction="""You are a specialized Pet Care Routine Generator.
Your role is to suggest daily routines, diets, and exercise plans tailored to a pet's stage.
You have access to MCP tools to retrieve routines.""",
    description="Specialist for pet care routines, diet plans, and exercise scheduling.",
    tools=[mcp_toolset],
)

# 2. Orchestrator sub-agent tools
appointment_tool = AgentTool(appointment_agent)
medication_tool = AgentTool(medication_agent)
routine_tool = AgentTool(routine_agent)

pet_care_orchestrator_agent = LlmAgent(
    name="pet_care_orchestrator_agent",
    model=config.model,
    instruction="""You are the Pet Pal Orchestrator, the central hub for the Pet Care Assistant.
Your job is to understand the user's pet-related request and delegate it to the appropriate specialized agent using your tools:
- Route scheduling/vet/grooming requests to appointment_agent.
- Route medication tracking/logs to medication_agent.
- Route diet/exercise/routine queries to routine_agent.

Always consult the specialized agents. If a sub-agent suggests an appointment and requests approval, let the user know you have recorded it and are waiting for confirmation.""",
    tools=[appointment_tool, medication_tool, routine_tool],
)

# Helper to safely extract user text from types.Content
def get_user_text(node_input: Any) -> str:
    if isinstance(node_input, types.Content):
        return "".join([part.text for part in node_input.parts if part.text])
    elif isinstance(node_input, str):
        return node_input
    elif isinstance(node_input, dict) and "text" in node_input:
        return str(node_input["text"])
    return str(node_input)

# 3. Security node
def security_checkpoint(ctx: Context, node_input: Any) -> Event:
    user_input = get_user_text(node_input)
    
    # PII Scrubbing: Scrub phone numbers and emails
    phone_pattern = r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b'
    email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    
    clean_input = re.sub(phone_pattern, "[REDACTED_PHONE]", user_input)
    clean_input = re.sub(email_pattern, "[REDACTED_EMAIL]", clean_input)
    
    # Audit Logging
    audit_data = {
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "event": "pii_scrubbing",
        "severity": "INFO",
        "details": {
            "scrubbed_phone": len(re.findall(phone_pattern, user_input)) > 0,
            "scrubbed_email": len(re.findall(email_pattern, user_input)) > 0
        }
    }
    logger.info(json.dumps(audit_data))
    
    # Prompt Injection Check
    injection_keywords = ["ignore instructions", "system prompt", "override rules", "jailbreak", "ignore previous"]
    detected_injection = any(kw in user_input.lower() for kw in injection_keywords)
    
    if detected_injection:
        audit_data = {
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "event": "prompt_injection_detection",
            "severity": "CRITICAL",
            "message": "Potential prompt injection attack blocked."
        }
        logger.warning(json.dumps(audit_data))
        return Event(output="Security Event: Prompt injection detected.", route="SECURITY_EVENT")
        
    # Domain-specific rule: Surgical booking consent check
    surgical_keywords = ["surgery", "surgical", "operation", "neuter", "spay", "castrate"]
    needs_consent = any(kw in user_input.lower() for kw in surgical_keywords)
    has_consent = any(ok in user_input.lower() for ok in ["consent", "approve", "agree", "authorized", "yes"])
    
    if needs_consent and not has_consent:
        audit_data = {
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "event": "domain_rule_violation",
            "severity": "WARNING",
            "message": "Surgical procedure requires explicit owner consent."
        }
        logger.warning(json.dumps(audit_data))
        return Event(
            output="Security Checkpoint: Surgical procedures require explicit owner consent. Please state 'I consent' or 'I authorize' to proceed.",
            route="SECURITY_EVENT"
        )
        
    # Security check passed, proceed with clean input
    clean_content = types.Content(role='user', parts=[types.Part.from_text(text=clean_input)])
    
    audit_data = {
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "event": "security_check_passed",
        "severity": "INFO"
    }
    logger.info(json.dumps(audit_data))
    
    return Event(output=clean_content, route="__DEFAULT__")

def security_event_handler(node_input: str) -> types.Content:
    """Formats the security violation message for the web UI."""
    return types.Content(
        role="model",
        parts=[types.Part.from_text(text=f"⚠️ {node_input}")]
    )

# 4. Human-In-The-Loop Approval Node
def ensure_content(val: Any) -> types.Content:
    if isinstance(val, types.Content):
        return val
    elif isinstance(val, str):
        return types.Content(role="model", parts=[types.Part.from_text(text=val)])
    elif isinstance(val, dict):
        if "parts" in val:
            parts = []
            for part in val["parts"]:
                if isinstance(part, dict) and "text" in part:
                    parts.append(types.Part.from_text(text=part["text"]))
                elif isinstance(part, str):
                    parts.append(types.Part.from_text(text=part))
                else:
                    parts.append(part)
            return types.Content(role=val.get("role", "model"), parts=parts)
        elif "text" in val:
            return types.Content(role="model", parts=[types.Part.from_text(text=val["text"])])
    return types.Content(role="model", parts=[types.Part.from_text(text=str(val))])

async def hitl_approval(ctx: Context, node_input: Any) -> AsyncGenerator[Event, None]:
    node_input = ensure_content(node_input)
    if ctx.state.get("appointment_pending"):
        if not ctx.resume_inputs or "approve_appointment" not in ctx.resume_inputs:
            details = ctx.state.get("appointment_details", "vet appointment")
            yield RequestInput(
                interrupt_id="approve_appointment",
                message=f"Please confirm if you want to book this appointment: {details}. Reply 'yes' to book or 'no' to cancel."
            )
            return
        
        user_response = ctx.resume_inputs["approve_appointment"].strip().lower()
        ctx.state["appointment_pending"] = False
        
        if user_response in ["yes", "y", "confirm", "approve"]:
            details = ctx.state.get("appointment_details")
            ctx.state["booked_appointments"] = ctx.state.get("booked_appointments", []) + [details]
            output_text = f"✅ Appointment confirmed and booked! Details: {details}."
        else:
            output_text = "❌ Appointment booking cancelled by user request."
            
        content_obj = types.Content(role='model', parts=[types.Part.from_text(text=output_text)])
        yield Event(
            output=content_obj,
            content=content_obj
        )
    else:
        yield Event(output=node_input, content=node_input)

hitl_approval_node = FunctionNode(
    func=hitl_approval,
    rerun_on_resume=True,
)

def final_output(node_input: Any) -> types.Content:
    return ensure_content(node_input)

security_checkpoint_node = FunctionNode(func=security_checkpoint)
security_event_handler_node = FunctionNode(func=security_event_handler)
final_output_node = FunctionNode(func=final_output)

# 5. Workflow definition
root_agent = Workflow(
    name="root_agent",
    edges=[
        (START, security_checkpoint_node),
        Edge(from_node=security_checkpoint_node, to_node=security_event_handler_node, route="SECURITY_EVENT"),
        Edge(from_node=security_checkpoint_node, to_node=pet_care_orchestrator_agent, route="__DEFAULT__"),
        (pet_care_orchestrator_agent, hitl_approval_node),
        (hitl_approval_node, final_output_node),
        (security_event_handler_node, final_output_node),
    ],
)

app = App(
    root_agent=root_agent,
    name="app",
    resumability_config=ResumabilityConfig(is_resumable=True)
)
