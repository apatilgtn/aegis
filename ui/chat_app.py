"""
Aegis demo chat front-end (Streamlit).

This is a rule-based stand-in for the real orchestrator agent described in
the solution doc (§5): it parses free text into an AccessGrantRequest
(ui/intent_parser.py) instead of calling an LLM, so the rest of the pipeline
— guardrail -> MCP identity/project resolution -> Terraform diff — can be
demoed end-to-end without API credentials. Swapping in a real LLM call only
means replacing parse_intent's call site below.

Only the access_grant capability is wired, matching MVP scope (§11.1).

Content design: the primary response is written for the person requesting
access — what was understood, whether it's allowed, what happens next, in
plain language. Terraform diffs, policy IDs, and catalog IDs are real and
important for audit, but they go in a collapsed "technical details" section,
not the headline — a requester shouldn't need to read HCL to know if their
access was granted.

Each chat turn is computed into a plain-data record (compute_assistant_record)
and rendered by a single function (render_turn) used both for the live turn
and for replaying history on rerun — so a colored status box never silently
degrades to plain text after the first Streamlit rerun.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st

from agents.access_grant_agent import (
    AccessGrantDenied,
    AlreadyGrantedError,
    ApprovalRequestFailed,
    check_approval_status,
    handle_access_grant,
)
from contracts.capability_contract import AccessGrantRequest, Cloud, Environment, Tier
from contracts.entitlement_catalog import EntitlementCatalog, EntitlementNotFoundError
from mcp_servers.azure_entra.directory_service import PrincipalNotFoundError
from mcp_servers.gcp_resource_manager.project_service import ProjectNotFoundError
from ui.intent_parser import IntentParseError, ParsedIntent, parse_intent

CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "entitlement_catalog.yaml"

ENV_LABELS = {
    Environment.NONPROD: "Non-Prod",
    Environment.PROD: "Production",
}

EXAMPLE_PROMPTS = [
    (
        "✅ Try: Tier-2 access",
        "Give me temporary Tier-2 access to payments non-prod for 8 hours, "
        "debugging a reconciliation issue",
    ),
    (
        "❌ Try: gets denied",
        "Give me Tier-2 access to payments non-prod for 48 hours, just in case",
    ),
]

NEOBRUTALISM_CSS = """
<style>
:root {
    --nb-border: #111111;
    --nb-yellow: #FFDE59;
    --nb-bg: #FFFBF0;
    --nb-card: #FFFFFF;
    --nb-text: #111111;
    --nb-shadow: 5px 5px 0 var(--nb-border);
}

/* Force a flat, opaque background + black text everywhere first, so no
   element can inherit a half-applied dark-theme background behind text. */
html, body, .stApp, [class*="css"] {
    background-color: var(--nb-bg) !important;
    color: var(--nb-text) !important;
    font-family: 'Helvetica Neue', Arial, sans-serif;
}
/* Scoped to text content only — NOT a blanket div/span rule, since that
   also strips background-color from widget internals like the radio
   button's selected-state dot. */
.stMarkdown, .stMarkdown p, .stMarkdown span, .stMarkdown li,
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] *,
h1, h2, h3, h4, h5, h6 {
    background-color: transparent !important;
    color: var(--nb-text) !important;
}

/* Hide Streamlit's dev toolbar for a cleaner look */
header[data-testid="stHeader"] {
    background-color: transparent !important;
    box-shadow: none !important;
}

h1, h2, h3 {
    font-weight: 900 !important;
    letter-spacing: 0.3px;
}

.aegis-logo {
    display: inline-flex;
    align-items: center;
    gap: 10px;
}
.aegis-logo .badge {
    background-color: var(--nb-yellow);
    border: 3px solid var(--nb-border);
    box-shadow: var(--nb-shadow);
    padding: 4px 10px;
    font-size: 1.6rem;
}

/* Sidebar: cream, not a solid color block — border is the accent */
section[data-testid="stSidebar"] {
    background-color: var(--nb-card) !important;
    border-right: 3px solid var(--nb-border);
}
section[data-testid="stSidebar"] h3 {
    border-bottom: 4px solid var(--nb-yellow);
    display: inline-block;
    padding-bottom: 2px;
}
section[data-testid="stSidebar"] .stTextInput input {
    border: 2.5px solid var(--nb-border) !important;
    border-radius: 0 !important;
    background: var(--nb-bg) !important;
    box-shadow: 3px 3px 0 var(--nb-border);
    padding: 8px;
}

/* Buttons: black fill, white text — the one inverted element for contrast */
.stButton>button {
    border: 2.5px solid var(--nb-border) !important;
    border-radius: 0 !important;
    background-color: var(--nb-border) !important;
    color: #ffffff !important;
    font-weight: 700 !important;
    box-shadow: var(--nb-shadow) !important;
    transition: transform 0.05s ease, box-shadow 0.05s ease;
}
.stButton>button:active {
    transform: translate(4px, 4px);
    box-shadow: 1px 1px 0 var(--nb-border) !important;
}

/* Chat messages — flat white cards, black text, hard shadow */
[data-testid="stChatMessage"] {
    background-color: var(--nb-card) !important;
    border: 2.5px solid var(--nb-border) !important;
    border-radius: 0 !important;
    box-shadow: var(--nb-shadow) !important;
    margin-bottom: 16px !important;
    padding: 6px !important;
}
[data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]) {
    background-color: #FFF3D6 !important;
}

/* Chat input */
[data-testid="stChatInput"] {
    border: 2.5px solid var(--nb-border) !important;
    border-radius: 0 !important;
    box-shadow: var(--nb-shadow) !important;
    background-color: var(--nb-card) !important;
}
[data-testid="stChatInput"] textarea {
    background-color: var(--nb-card) !important;
    color: var(--nb-text) !important;
}

/* Alerts (st.error / st.warning / st.info / st.success) keep their tint
   but gain the same border/shadow language */
div[data-testid="stAlert"] {
    border: 2.5px solid var(--nb-border) !important;
    border-radius: 0 !important;
    box-shadow: var(--nb-shadow) !important;
}
div[data-testid="stAlert"] p {
    color: var(--nb-text) !important;
}

/* Expander */
details {
    border: 2.5px solid var(--nb-border) !important;
    border-radius: 0 !important;
    box-shadow: var(--nb-shadow) !important;
    background-color: var(--nb-card) !important;
}

/* Code blocks */
pre, code {
    border: 2.5px solid var(--nb-border) !important;
    border-radius: 0 !important;
    background-color: #F5F5F5 !important;
    color: var(--nb-text) !important;
}

[data-testid="stCaptionContainer"] {
    font-weight: 500;
    opacity: 0.75;
}
</style>
"""


@st.cache_resource
def load_catalog() -> EntitlementCatalog:
    return EntitlementCatalog.load(CATALOG_PATH)


def human_tier(tier: Tier) -> str:
    return tier.value.replace("tier", "Tier ")


def render_understood(parsed: ParsedIntent) -> str:
    return (
        "**Here's what I understood:**\n"
        f"- Business unit: **{parsed.business_unit.title()}**\n"
        f"- Environment: **{ENV_LABELS[parsed.environment]}**\n"
        f"- Access level: **{human_tier(parsed.tier)}**\n"
        f"- Duration: **{parsed.ttl_hours} hours**\n"
        f"- Reason: _{parsed.justification}_"
    )


def render_technical_details(result) -> None:
    with st.expander("🔧 Technical details (for platform / audit team)"):
        st.markdown(
            f"- Policy check: `{', '.join(result.policy_decision.policy_ids)}`\n"
            f"- Catalog entry: `{result.entitlement.catalog_entry_id}`\n"
            f"- Resource: `{result.entitlement.resource_id}` ({result.entitlement.resource_type})\n"
            f"- ServiceNow request: `{result.approval.reference}` "
            f"(request sys_id `{result.approval.request_sys_id}`, "
            f"approval sys_id `{result.approval.approval_sys_id}`, state at creation `{result.approval.state}`)\n"
            f"- Expires (UTC): `{result.expires_at.isoformat()}`"
        )
        st.code(result.iac_diff, language="hcl")


def compute_assistant_record(prompt: str, requester: str, cloud: Cloud, catalog: EntitlementCatalog) -> dict:
    record = {
        "role": "assistant",
        "status": None,
        "understood": None,
        "message": "",
        "caption": None,
        "result": None,
        "technical_error": None,
        "last_checked_state": None,
    }
    parsed = None
    try:
        parsed = parse_intent(prompt, catalog.business_units())
        record["understood"] = render_understood(parsed)

        request = AccessGrantRequest(
            requester=requester,
            business_unit=parsed.business_unit,
            environment=parsed.environment,
            tier=parsed.tier,
            justification=parsed.justification,
            ttl_hours=parsed.ttl_hours,
        )
        result = handle_access_grant(request, cloud, catalog)

        record["status"] = "pending_approval"
        record["message"] = (
            f"**🟡 Passed policy checks — sent for human approval.** Nothing has been granted yet. "
            f"If approved, \"{result.entitlement.display_name}\" will be granted on "
            f"{result.entitlement.cloud.value.upper()}, expiring "
            f"{result.expires_at.strftime('%d %b %Y, %H:%M UTC')}."
        )
        instance_url = os.getenv("SN_INSTANCE_URL", "")
        request_link = (
            f"{instance_url}/sc_request.do?sys_id={result.approval.request_sys_id}" if instance_url else None
        )
        approval_line = f"Request **{result.approval.reference}** is now waiting in ServiceNow for a human to approve."
        if request_link:
            approval_line = (
                f"Request [{result.approval.reference}]({request_link}) is now waiting in ServiceNow "
                "for a human to approve."
            )
        record["caption"] = (
            f"{approval_line} Nothing is applied to any cloud until that happens — use "
            "\"Check approval status\" below once it's been actioned."
        )
        record["result"] = result

    except IntentParseError as exc:
        record["status"] = "parse_error"
        record["message"] = (
            f"**🤔 I need a bit more detail.** {exc}\n\n"
            "Try something like: _\"Give me Tier 2 access to payments non-prod for 8 hours, "
            "for debugging a reconciliation issue.\"_"
        )

    except AccessGrantDenied as exc:
        record["status"] = "denied"
        record["message"] = "**❌ This can't be approved automatically:**\n\n" + "\n".join(
            f"- {reason}" for reason in exc.decision.reasons
        )
        record["caption"] = "Try adjusting your request — for example, a shorter duration."

    except AlreadyGrantedError:
        record["status"] = "already_granted"
        record["message"] = "**ℹ️ You already have this access.** Nothing more to do."

    except ApprovalRequestFailed as exc:
        record["status"] = "lookup_error"
        record["message"] = (
            "**The request was allowed by policy, but raising the approval ticket failed.** "
            "Please try again in a moment, or contact the platform team."
        )
        record["technical_error"] = str(exc)

    except (EntitlementNotFoundError, PrincipalNotFoundError, ProjectNotFoundError) as exc:
        record["status"] = "lookup_error"
        record["message"] = (
            "**Can't process this request yet.** This combination isn't set up, or your "
            "identity isn't on file for automatic resolution — the platform team can help."
        )
        record["technical_error"] = str(exc)

    return record


APPROVAL_STATE_LABELS = {
    "requested": "🟡 Still pending in ServiceNow",
    "approved": "✅ Approved in ServiceNow — would now be applied by CI/CD",
    "rejected": "❌ Rejected in ServiceNow",
    "cancelled": "⚪ Cancelled",
    "not requested": "⚪ Not yet requested",
}


def render_turn(message: dict, index: int) -> None:
    with st.chat_message(message["role"]):
        if message["role"] == "user":
            st.markdown(message["content"])
            return

        if message["understood"]:
            st.markdown(message["understood"])

        status = message["status"]
        if status == "pending_approval":
            st.warning(message["message"])
        elif status in ("denied", "lookup_error"):
            st.error(message["message"])
        elif status == "parse_error":
            st.warning(message["message"])
        elif status == "already_granted":
            st.info(message["message"])

        if message["caption"]:
            st.caption(message["caption"])

        if message["result"]:
            render_technical_details(message["result"])

            if message.get("last_checked_state"):
                label = APPROVAL_STATE_LABELS.get(message["last_checked_state"], message["last_checked_state"])
                st.markdown(f"**Last checked status:** {label}")

            if st.button("🔄 Check approval status", key=f"check_status_{index}"):
                message["last_checked_state"] = check_approval_status(message["result"].approval)
                st.rerun()

        if message["technical_error"]:
            with st.expander("🔧 Technical details (for platform / audit team)"):
                st.code(message["technical_error"])


def main() -> None:
    st.set_page_config(page_title="AEGIS — Agentic Cloud Governance", layout="centered")
    st.markdown(NEOBRUTALISM_CSS, unsafe_allow_html=True)

    catalog = load_catalog()

    st.markdown(
        '<div class="aegis-logo"><span class="badge">⚡</span>'
        '<h1 style="margin:0;">AEGIS</h1></div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "Ask for cloud access in plain English. Aegis checks it against policy instantly and "
        "prepares the change for approval — access is only ever applied after a human signs off."
    )

    with st.sidebar:
        st.subheader("Request context")
        requester = st.text_input("Requester email", value="jane.doe@example.com")
        cloud = st.radio("Target cloud", options=[Cloud.AZURE, Cloud.GCP], format_func=lambda c: c.value.upper())
        st.caption(
            "Which cloud this business unit's environment lives in — normally figured out "
            "automatically, exposed here so you can prove the same request works on either cloud."
        )

        st.subheader("Available access levels")
        st.caption("What you can actually ask for — anything else will be refused, not guessed.")
        for entry in sorted(catalog.all_entries(), key=lambda e: (e.cloud.value, e.tier.value)):
            st.markdown(
                f"- **{entry.display_name}** ({entry.cloud.value.upper()}) — "
                f"max {entry.max_ttl_hours}h"
            )

    if "messages" not in st.session_state:
        st.session_state.messages = []

    for index, message in enumerate(st.session_state.messages):
        render_turn(message, index)

    if not st.session_state.messages:
        st.markdown("**Try an example:**")
        cols = st.columns(len(EXAMPLE_PROMPTS))
        for col, (label, example_text) in zip(cols, EXAMPLE_PROMPTS):
            if col.button(label, key=f"example_{label}"):
                st.session_state["pending_prompt"] = example_text

    placeholder = "e.g. give me temporary Tier-2 access to payments non-prod for a debugging task"
    prompt = st.chat_input(placeholder) or st.session_state.pop("pending_prompt", None)
    if prompt:
        user_message = {"role": "user", "content": prompt}
        assistant_message = compute_assistant_record(prompt, requester, cloud, catalog)

        st.session_state.messages.append(user_message)
        st.session_state.messages.append(assistant_message)

        render_turn(user_message, len(st.session_state.messages) - 2)
        render_turn(assistant_message, len(st.session_state.messages) - 1)


if __name__ == "__main__":
    main()
