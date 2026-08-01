"""
Aegis demo chat front-end (Streamlit).

Fully conversational by design: every piece of information (cloud,
environment, email, role, duration, justification) is gathered as a guided
back-and-forth in the chat itself, not via sidebar widgets. The bot also
checks the requester's *current* access before asking what they want, so an
existing holder is told what they already have instead of being walked
through a request they don't need.

State machine (st.session_state.conv_step):
  ask_cloud -> ask_env -> ask_email -> [check current access]
    -> existing_access_found -> (loop back to ask_role) | idle_done
    -> ask_role -> ask_duration -> ask_justification -> confirm -> done

"done" is not a dead end: the submitted request's chat turn carries its own
"Refresh pipeline status" and (once approved) "Merge now" actions, so the
whole propose -> approve -> merge -> apply loop is followable from the chat
without leaving it — merging is still a deliberate human click, just one
that lives here instead of on GitHub.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st

from agents.access_check_agent import check_current_access
from agents.access_grant_agent import (
    AccessGrantDenied,
    AlreadyGrantedError,
    ApprovalRejected,
    ApprovalRequestFailed,
    MergeNotAllowed,
    PullRequestFailed,
    advance_pipeline,
    check_approval_status,
    check_pipeline_status,
    handle_access_grant,
    merge_access_grant,
)
from agents.incident_triage_agent import IncidentRequestFailed, raise_incident, triage_incident
from contracts.capability_contract import AccessGrantRequest, Cloud, Environment, IncidentTriageRequest, Tier
from contracts.entitlement_catalog import EntitlementCatalog, EntitlementCatalogEntry

CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "entitlement_catalog.yaml"

ENV_LABELS = {Environment.NONPROD: "Non-Prod", Environment.PROD: "Production"}

CARD_UI_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

:root {
    --border: #1A1A1A;
    --bg: #EDEDED;
    --card: #F7F5F0;
    --text: #111111;
    --text-secondary: #6B6B6B;
    --yellow: #F0C64A;
    --pink: #F6D3E4;
    --lavender: #DAD6F5;
    --mint: #B7F0C4;
    --mint-text: #1E8E3E;
    --radius-lg: 18px;
    --radius-sm: 10px;
    --shadow-soft: 0 1px 3px rgba(0, 0, 0, 0.06);
}

html, body, .stApp, [class*="css"] {
    background-color: var(--bg) !important;
    color: var(--text) !important;
    font-family: 'Inter', 'Helvetica Neue', Arial, sans-serif;
}
/* Subtle grain texture on the page background, approximated without an
   image asset via a fine repeating dot pattern. */
.stApp {
    background-image: radial-gradient(rgba(0, 0, 0, 0.035) 1px, transparent 1px);
    background-size: 3px 3px;
}

.stMarkdown, .stMarkdown p, .stMarkdown span, .stMarkdown li,
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] *,
h1, h2, h3, h4, h5, h6 {
    background-color: transparent !important;
    color: var(--text) !important;
}

header[data-testid="stHeader"] {
    background-color: transparent !important;
    box-shadow: none !important;
}

h1, h2, h3 {
    font-weight: 800 !important;
    letter-spacing: -0.01em;
}

.aegis-logo {
    display: inline-flex;
    align-items: center;
    gap: 10px;
}
.aegis-logo .badge {
    background-color: var(--yellow);
    border: 1.5px solid var(--border);
    border-radius: var(--radius-sm);
    box-shadow: var(--shadow-soft);
    padding: 4px 10px;
    font-size: 1.5rem;
}

/* Sidebar: a bordered rules panel, matching the card language */
section[data-testid="stSidebar"] {
    background-color: var(--card) !important;
    border-right: 1.5px solid var(--border);
}
section[data-testid="stSidebar"] h3 {
    border-bottom: 3px solid var(--yellow);
    display: inline-block;
    padding-bottom: 2px;
    font-weight: 700 !important;
}

/* Buttons: black-fill pill, the reference's primary CTA style */
.stButton>button {
    border: 1.5px solid var(--border) !important;
    border-radius: 999px !important;
    background-color: var(--border) !important;
    color: #ffffff !important;
    font-weight: 600 !important;
    box-shadow: none !important;
    transition: opacity 0.1s ease, transform 0.05s ease;
}
.stButton>button:hover {
    opacity: 0.85;
}
.stButton>button:active {
    transform: scale(0.98);
}

/* Chat messages: clean rounded cards, thin border, soft shadow */
[data-testid="stChatMessage"] {
    background-color: var(--card) !important;
    border: 1.5px solid var(--border) !important;
    border-radius: var(--radius-lg) !important;
    box-shadow: var(--shadow-soft) !important;
    margin-bottom: 14px !important;
    padding: 10px !important;
}
[data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]) {
    background-color: #FFFFFF !important;
}

[data-testid="stChatInput"] {
    border: 1.5px solid var(--border) !important;
    border-radius: var(--radius-lg) !important;
    box-shadow: var(--shadow-soft) !important;
    background-color: var(--card) !important;
}
[data-testid="stChatInput"] textarea {
    background-color: var(--card) !important;
    color: var(--text) !important;
}

div[data-testid="stAlert"] {
    border: 1.5px solid var(--border) !important;
    border-radius: var(--radius-sm) !important;
    box-shadow: var(--shadow-soft) !important;
}
div[data-testid="stAlert"] p {
    color: var(--text) !important;
}

details {
    border: 1.5px solid var(--border) !important;
    border-radius: var(--radius-sm) !important;
    box-shadow: var(--shadow-soft) !important;
    background-color: #FFFFFF !important;
}

pre, code {
    border: 1.5px solid var(--border) !important;
    border-radius: var(--radius-sm) !important;
    background-color: #F5F5F5 !important;
    color: var(--text) !important;
}

[data-testid="stCaptionContainer"] {
    font-weight: 400;
    color: var(--text-secondary) !important;
}

/* Stat chips: the row of colored boxes from the reference design */
.stat-chip-row {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    margin: 8px 0 12px;
}
.stat-chip {
    border: 1.5px solid var(--border);
    border-radius: var(--radius-sm);
    padding: 8px 14px;
    min-width: 110px;
}
.stat-chip-label {
    font-size: 0.7rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    opacity: 0.65;
    margin-bottom: 2px;
}
.stat-chip-value {
    font-size: 1.05rem;
    font-weight: 700;
}
</style>
"""

APPROVAL_STATE_LABELS = {
    "requested": "🟡 Pending",
    "approved": "✅ Approved",
    "rejected": "❌ Rejected",
    "cancelled": "⚪ Cancelled",
    "not requested": "⚪ Not yet requested",
}

PR_STATE_LABELS = {
    "open": "🟡 Open, not merged yet",
    "merged": "✅ Merged",
    "closed": "❌ Closed without merging",
}

CHIP_COLORS = ["#F0C64A", "#F6D3E4", "#DAD6F5", "#B7F0C4"]

BUILD_STATUS_LABELS = {
    "queued": "🟡 Queued",
    "in_progress": "🟡 Running",
    "success": "✅ Applied successfully",
    "failure": "❌ Failed",
    "timed_out": "❌ Timed out",
    "cancelled": "❌ Cancelled",
}


@st.cache_resource
def load_catalog() -> EntitlementCatalog:
    return EntitlementCatalog.load(CATALOG_PATH)


def human_tier(tier: Tier) -> str:
    return tier.value.replace("tier", "Tier ")


def external_link(url: str, text: str) -> str:
    """Opens in a new tab — clicking a same-tab link would navigate away
    from the app and lose the whole conversation's session state."""
    return f'<a href="{url}" target="_blank" rel="noopener noreferrer">{text}</a>'


def servicenow_request_url(request_sys_id: str) -> str | None:
    instance_url = os.getenv("SN_INSTANCE_URL", "")
    return f"{instance_url}/sc_request.do?sys_id={request_sys_id}" if instance_url else None


def servicenow_link_or_bold(reference: str, request_sys_id: str) -> str:
    url = servicenow_request_url(request_sys_id)
    return external_link(url, reference) if url else f"<strong>{reference}</strong>"


def servicenow_incident_url(sys_id: str) -> str | None:
    instance_url = os.getenv("SN_INSTANCE_URL", "")
    return f"{instance_url}/incident.do?sys_id={sys_id}" if instance_url else None


def servicenow_incident_link_or_bold(number: str, sys_id: str) -> str:
    url = servicenow_incident_url(sys_id)
    return external_link(url, number) if url else f"<strong>{number}</strong>"


def render_stat_chips(pairs: list[tuple[str, str]]) -> None:
    """Renders the colored stat-chip row. Only ever called with system
    values (cloud names, tiers, durations) — never raw user-typed text —
    so this is safe to render as HTML."""
    chips = "".join(
        f'<div class="stat-chip" style="background-color:{CHIP_COLORS[i % len(CHIP_COLORS)]};">'
        f'<div class="stat-chip-label">{label}</div>'
        f'<div class="stat-chip-value">{value}</div></div>'
        for i, (label, value) in enumerate(pairs)
    )
    st.markdown(f'<div class="stat-chip-row">{chips}</div>', unsafe_allow_html=True)


# ---- conversation state helpers --------------------------------------------------

def init_state() -> None:
    if "conv_step" in st.session_state:
        return
    st.session_state.conv_step = "ask_intent"
    st.session_state.draft = {}
    st.session_state.messages = [
        text_turn(
            "assistant",
            "Hi, I'm **Aegis** ⚡. Nothing is ever applied without human approval.\n\n"
            "What would you like to do: request cloud access, or report an issue?",
        )
    ]


def text_turn(role: str, content: str) -> dict:
    return {"kind": "text", "role": role, "content": content}


def summary_turn(draft: dict) -> dict:
    return {"kind": "summary", "role": "assistant", "draft": dict(draft)}


def incident_result_turn(result, requester: str, justification: str) -> dict:
    return {
        "kind": "incident_result",
        "role": "assistant",
        "result": result,
        "requester": requester,
        "justification": justification,
        "declined": False,
    }


def result_turn(content: str, result) -> dict:
    return {
        "kind": "result",
        "role": "assistant",
        "content": content,
        "result": result,
        "last_checked_approval": None,
        "last_checked_pr": None,
        "last_checked_build": None,
        "rejected": False,
    }


def bot_says(text: str) -> None:
    st.session_state.messages.append(text_turn("assistant", text))


def user_says(text: str) -> None:
    st.session_state.messages.append(text_turn("user", text))


def reset_conversation() -> None:
    st.session_state.conv_step = "ask_intent"
    st.session_state.draft = {}
    st.session_state.messages = [
        text_turn("assistant", "Let's start over. What would you like to do: request cloud access, or report an issue?")
    ]


# ---- intent selection ----------------------------------------------------------------

def handle_intent_choice(wants_access: bool) -> None:
    if wants_access:
        user_says("Request cloud access")
        st.session_state.conv_step = "ask_cloud"
        bot_says("Which cloud, Azure or GCP?")
    else:
        user_says("Report an issue")
        st.session_state.conv_step = "ask_incident_cloud"
        bot_says("Which cloud is affected, Azure or GCP?")


# ---- step handlers: incident_triage ------------------------------------------------

def handle_incident_cloud_choice(cloud: Cloud) -> None:
    user_says(cloud.value.upper())
    st.session_state.draft["cloud"] = cloud
    st.session_state.conv_step = "ask_incident_email"
    bot_says("What's your email address?")


def handle_incident_email_input(email: str) -> None:
    user_says(email)
    if "@" not in email:
        bot_says("That doesn't look like a valid email. Could you try again?")
        return
    st.session_state.draft["email"] = email
    st.session_state.conv_step = "ask_resource_hint"
    bot_says('What\'s the affected service or resource? (a short name, e.g. "payments-api")')


def handle_resource_hint_input(text: str) -> None:
    if len(text.strip()) == 0:
        bot_says("I need at least a short name to search for. What's the affected service or resource?")
        return
    user_says(text)
    st.session_state.draft["resource_hint"] = text.strip()
    st.session_state.conv_step = "ask_incident_window"
    bot_says("How many hours back should I look? (max 168)")


def handle_incident_window_input(text: str) -> None:
    match = re.search(r"(\d+)", text)
    if not match or not (1 <= int(match.group(1)) <= 168):
        bot_says("Please give me a number of hours between 1 and 168.")
        return

    user_says(text)
    st.session_state.draft["time_window_hours"] = int(match.group(1))
    st.session_state.conv_step = "ask_incident_justification"
    bot_says("What prompted this investigation? (a brief reason)")


def handle_incident_justification_input(text: str) -> None:
    user_says(text)
    if len(text.strip()) < 10:
        bot_says("Could you give a bit more detail (at least 10 characters)?")
        return

    d = st.session_state.draft
    request = IncidentTriageRequest(
        requester=d["email"],
        cloud=d["cloud"],
        resource_hint=d["resource_hint"],
        time_window_hours=d["time_window_hours"],
        justification=text.strip(),
    )
    result = triage_incident(request)
    st.session_state.messages.append(incident_result_turn(result, d["email"], text.strip()))
    st.session_state.conv_step = "idle_done"


# ---- step handlers: access_grant ---------------------------------------------------

def handle_cloud_choice(cloud: Cloud) -> None:
    user_says(cloud.value.upper())
    st.session_state.draft["cloud"] = cloud
    st.session_state.conv_step = "ask_env"
    bot_says("Which environment: Non-Prod or Production?")


def handle_env_choice(environment: Environment) -> None:
    user_says(ENV_LABELS[environment])
    if environment == Environment.PROD:
        bot_says(
            "Production access isn't offered through this self-service flow. Only Non-Prod is "
            "available today. Continuing with Non-Prod."
        )
        st.session_state.draft["environment"] = Environment.NONPROD
    else:
        st.session_state.draft["environment"] = environment
    st.session_state.conv_step = "ask_email"
    bot_says("What's your email address?")


def handle_email_input(email: str, catalog: EntitlementCatalog) -> None:
    user_says(email)
    if "@" not in email:
        bot_says("That doesn't look like a valid email. Could you try again?")
        return

    st.session_state.draft["email"] = email
    cloud = st.session_state.draft["cloud"]
    current = check_current_access(email, cloud, catalog)
    st.session_state.draft["current_access"] = current

    if current:
        names = ", ".join(f"**{e.display_name}**" for e in current)
        bot_says(
            f"You already have: {names}. Would you like to request additional or different access, "
            "or is that all for now?"
        )
        st.session_state.conv_step = "existing_access_found"
    else:
        entries = available_entries(catalog)
        if entries:
            bot_says(
                f"You don't currently have any access on {cloud.value.upper()} for this business "
                "unit. Here's what's available:"
            )
            st.session_state.conv_step = "ask_role"
        else:
            bot_says("There's nothing available to request for this combination yet. Contact the platform team.")
            st.session_state.conv_step = "idle_done"


def handle_existing_followup(wants_more: bool) -> None:
    if wants_more:
        user_says("Request additional / different access")
        bot_says("Here's what's available:")
        st.session_state.conv_step = "ask_role"
    else:
        user_says("That's all, thanks")
        bot_says("Sounds good. I'm here if you need anything else. Use \"Start over\" in the sidebar anytime.")
        st.session_state.conv_step = "idle_done"


def available_entries(catalog: EntitlementCatalog) -> list[EntitlementCatalogEntry]:
    draft = st.session_state.draft
    return [
        e
        for e in catalog.all_entries()
        if e.cloud == draft["cloud"] and e.environment == draft["environment"]
    ]


def handle_role_choice(entry: EntitlementCatalogEntry) -> None:
    user_says(entry.display_name)
    st.session_state.draft["business_unit"] = entry.business_unit
    st.session_state.draft["tier"] = entry.tier
    st.session_state.draft["max_ttl_hours"] = entry.max_ttl_hours
    st.session_state.conv_step = "ask_duration"
    bot_says(f"How many hours do you need this for? (max {entry.max_ttl_hours}h)")


def handle_duration_input(text: str) -> None:
    match = re.search(r"(\d+)", text)
    if not match:
        bot_says("I couldn't find a number of hours in that. How many hours do you need?")
        return

    user_says(text)
    st.session_state.draft["ttl_hours"] = int(match.group(1))
    st.session_state.conv_step = "ask_justification"
    bot_says("And what's the justification for this access?")


def handle_justification_input(text: str, catalog: EntitlementCatalog) -> None:
    user_says(text)
    if len(text.strip()) < 10:
        bot_says("Could you give a bit more detail (at least 10 characters)?")
        return

    st.session_state.draft["justification"] = text.strip()
    st.session_state.conv_step = "confirm"
    st.session_state.messages.append(summary_turn(st.session_state.draft))


def handle_confirm(yes: bool, catalog: EntitlementCatalog) -> None:
    if not yes:
        user_says("No, cancel")
        bot_says("No problem, cancelled. Want to start a new request? Which cloud, Azure or GCP?")
        st.session_state.draft = {}
        st.session_state.conv_step = "ask_cloud"
        return

    user_says("Yes, submit it")
    d = st.session_state.draft
    request = AccessGrantRequest(
        requester=d["email"],
        business_unit=d["business_unit"],
        environment=d["environment"],
        tier=d["tier"],
        justification=d["justification"],
        ttl_hours=d["ttl_hours"],
    )

    try:
        result = handle_access_grant(request, d["cloud"], catalog)
    except AccessGrantDenied as exc:
        bot_says("**❌ This can't be approved automatically:**\n\n" + "\n".join(f"- {r}" for r in exc.decision.reasons))
        st.session_state.conv_step = "ask_duration"
        bot_says(f"Want to try a different duration? (max {d['max_ttl_hours']}h)")
        return
    except AlreadyGrantedError:
        bot_says("**ℹ️ You already have this access.** Nothing more to do.")
        st.session_state.conv_step = "idle_done"
        return
    except ApprovalRequestFailed as exc:
        bot_says(f"**Something went wrong raising this request.** {exc}\n\nPlease try again in a moment.")
        st.session_state.conv_step = "confirm"
        return

    approval_ref = servicenow_link_or_bold(result.approval.reference, result.approval.request_sys_id)

    content = (
        f"**🟡 Passed policy checks. Sent for human approval.** Nothing has been granted yet.\n\n"
        f"Request {approval_ref} is now waiting in ServiceNow (opens in a new tab). **No pull "
        "request has been opened yet.** One will only be raised once a human approves this in "
        "ServiceNow, so a declined request never leaves a proposed change sitting on GitHub.\n\n"
        "Use \"Refresh pipeline status\" below to check. I'll open the PR automatically the moment "
        "it's approved, and offer to merge it for you."
    )
    st.session_state.messages.append(result_turn(content, result))
    st.session_state.conv_step = "done"


# ---- rendering ----------------------------------------------------------------

def render_technical_details(result) -> None:
    with st.expander("🔧 Technical details (for platform / audit team)"):
        sn_ref = servicenow_link_or_bold(result.approval.reference, result.approval.request_sys_id)
        pr_line = (
            f"- Pull request: {external_link(result.pull_request.html_url, f'#{result.pull_request.number}')} "
            f"(branch `{result.pull_request.branch}`)\n"
            if result.pull_request
            else f"- Pull request: not yet raised. Will open on branch `{result.branch_name}` once approved\n"
        )
        st.markdown(
            f"- Policy check: `{', '.join(result.policy_decision.policy_ids)}`\n"
            f"- Catalog entry: `{result.entitlement.catalog_entry_id}`\n"
            f"- Resource: `{result.entitlement.resource_id}` ({result.entitlement.resource_type})\n"
            f"- ServiceNow request: {sn_ref} "
            f"(request sys_id `{result.approval.request_sys_id}`, "
            f"approval sys_id `{result.approval.approval_sys_id}`, state at creation `{result.approval.state}`)\n"
            f"{pr_line}"
            f"- Expires (UTC): `{result.expires_at.isoformat()}`",
            unsafe_allow_html=True,
        )
        st.code(result.iac_diff, language="hcl")


def render_pipeline_steps(message: dict) -> None:
    result = message["result"]
    approval_state = message.get("last_checked_approval")
    pr_state = message.get("last_checked_pr")
    build = message.get("last_checked_build")
    sn_ref = servicenow_link_or_bold(result.approval.reference, result.approval.request_sys_id)

    lines = ["**Pipeline status:**", "1. ✅ Policy check: passed"]

    if message.get("rejected"):
        rejected_label = APPROVAL_STATE_LABELS.get(approval_state, f"❌ {approval_state}")
        lines.append(f"2. {rejected_label}: ServiceNow request {sn_ref}")
        lines.append("3. ⚪ No pull request was raised. A declined request never reaches GitHub")
        lines.append("4. ⚪ Nothing to apply")
        st.markdown("\n".join(lines), unsafe_allow_html=True)
        return

    approval_label = APPROVAL_STATE_LABELS.get(approval_state, "⚪ Not checked yet") if approval_state else "⚪ Not checked yet"
    lines.append(f"2. {approval_label}: ServiceNow request {sn_ref}")

    if result.pull_request is None:
        lines.append("3. ⚪ Not raised yet. Opens automatically once approved")
        lines.append("4. ⚪ Waiting on approval, then PR merge, before the pipeline can apply")
        st.markdown("\n".join(lines), unsafe_allow_html=True)
        return

    pr_link = external_link(result.pull_request.html_url, f"PR #{result.pull_request.number}")
    pr_label = PR_STATE_LABELS.get(pr_state, "⚪ Not checked yet") if pr_state else "⚪ Not checked yet"
    lines.append(f"3. {pr_label}: {pr_link}")

    if pr_state != "merged":
        lines.append("4. ⚪ Waiting on PR merge before the pipeline can apply")
    elif build is None:
        lines.append("4. 🟡 Merged. GitHub Actions run not found yet (may take a few seconds to start)")
    else:
        build_label = BUILD_STATUS_LABELS.get(build.status, build.status)
        build_link = external_link(build.log_url, "GitHub Actions run")
        lines.append(f"4. {build_label}: {build_link}")

    st.markdown("\n".join(lines), unsafe_allow_html=True)


def render_result_turn(message: dict, index: int) -> None:
    # unsafe_allow_html is scoped to this bot-generated content only (it
    # embeds a ServiceNow link) — the plain user/bot text turns in
    # render_turn deliberately never get it, since those can contain raw
    # user-typed text (email, justification) and must not be interpreted as HTML.
    st.markdown(message["content"], unsafe_allow_html=True)
    result = message["result"]
    render_technical_details(result)
    render_pipeline_steps(message)

    if message.get("rejected"):
        return

    cols = st.columns(2)
    if cols[0].button("🔄 Refresh pipeline status", key=f"refresh_{index}"):
        with st.spinner("Checking ServiceNow. Opening the PR now if it's just been approved..."):
            try:
                result = advance_pipeline(result)
            except ApprovalRejected as exc:
                message["rejected"] = True
                message["last_checked_approval"] = exc.state
                st.rerun()
                return

            message["result"] = result
            message["last_checked_approval"] = check_approval_status(result.approval)
            if result.pull_request is not None:
                pr_state, build = check_pipeline_status(result.pull_request)
                message["last_checked_pr"] = pr_state
                message["last_checked_build"] = build
        st.rerun()

    can_merge = (
        result.pull_request is not None
        and message.get("last_checked_approval") == "approved"
        and message.get("last_checked_pr") != "merged"
    )
    if can_merge and cols[1].button("✅ Merge now", key=f"merge_{index}"):
        with st.spinner("Merging..."):
            try:
                merge_access_grant(result.approval, result.pull_request)
                message["last_checked_pr"] = "merged"
                st.success("Merged! The GitHub Actions pipeline will apply this shortly.")
            except MergeNotAllowed as exc:
                st.error(str(exc))
        st.rerun()


def render_summary_turn(message: dict) -> None:
    d = message["draft"]
    st.markdown("**Here's what I'll submit:**")
    render_stat_chips(
        [
            ("Cloud", d["cloud"].value.upper()),
            ("Business unit", d["business_unit"].title()),
            ("Access level", human_tier(d["tier"])),
            ("Duration", f"{d['ttl_hours']}h"),
        ]
    )
    # Plain markdown, no unsafe_allow_html: justification is raw user-typed
    # text and must never be interpreted as HTML.
    st.markdown(f"**Reason:** {d['justification']}")
    st.markdown("Shall I submit this request?")


def render_incident_result_turn(message: dict, index: int) -> None:
    result = message["result"]

    # Plain markdown throughout: resource_hint/justification are user-typed,
    # and log messages come from external systems (GCP/Azure) — neither is
    # trusted enough to render as HTML. Only the incident link below (a
    # system-generated number + sys_id) gets unsafe_allow_html.
    if result.findings:
        st.markdown(f"**{len(result.findings)} log entries found for `{result.resource_hint}` ({result.cloud.value.upper()}):**")
        with st.expander("🔍 Log findings"):
            for finding in result.findings[:20]:
                st.markdown(f"- `{finding.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}` **{finding.severity}**: {finding.message}")
    else:
        st.markdown(f"**No matching log entries for `{result.resource_hint}` ({result.cloud.value.upper()}).**")

    st.markdown(f"**Summary:** {result.summary}")

    if result.incident:
        ref = servicenow_incident_link_or_bold(result.incident.number, result.incident.sys_id)
        st.markdown(f"✅ Incident {ref} raised (opens in a new tab).", unsafe_allow_html=True)
        return

    if message["declined"]:
        st.caption("No incident was raised.")
        return

    st.caption("Raising an incident is immediate: it needs no approval, since nothing changes in any cloud.")
    cols = st.columns(2)
    if cols[0].button("🚨 Raise incident", key=f"raise_incident_{index}"):
        with st.spinner("Raising incident in ServiceNow..."):
            try:
                message["result"] = raise_incident(result, message["requester"], message["justification"])
            except IncidentRequestFailed as exc:
                st.error(str(exc))
        st.rerun()
    if cols[1].button("No thanks", key=f"skip_incident_{index}"):
        message["declined"] = True
        st.rerun()


def render_turn(message: dict, index: int) -> None:
    with st.chat_message(message["role"]):
        if message["kind"] == "result":
            render_result_turn(message, index)
        elif message["kind"] == "summary":
            render_summary_turn(message)
        elif message["kind"] == "incident_result":
            render_incident_result_turn(message, index)
        else:
            st.markdown(message["content"])


# ---- step-specific controls ----------------------------------------------------

def render_step_controls(catalog: EntitlementCatalog) -> str | None:
    """Renders buttons for the current step, if any. Returns a placeholder
    string for the chat_input, used generically for free-text steps."""
    step = st.session_state.conv_step

    if step == "ask_intent":
        cols = st.columns(2)
        if cols[0].button("Request cloud access", key="intent_access"):
            handle_intent_choice(True)
            st.rerun()
        if cols[1].button("Report an issue", key="intent_incident"):
            handle_intent_choice(False)
            st.rerun()
        return None

    if step == "ask_incident_cloud":
        cols = st.columns(2)
        if cols[0].button("Azure", key="incident_cloud_azure"):
            handle_incident_cloud_choice(Cloud.AZURE)
            st.rerun()
        if cols[1].button("GCP", key="incident_cloud_gcp"):
            handle_incident_cloud_choice(Cloud.GCP)
            st.rerun()
        return None

    if step == "ask_incident_email":
        return "Type your email address..."

    if step == "ask_resource_hint":
        return 'e.g. "payments-api"'

    if step == "ask_incident_window":
        cols = st.columns(3)
        for i, (col, preset) in enumerate(zip(cols, (1, 24, 168))):
            if col.button(f"{preset}h", key=f"incident_window_preset_{i}"):
                handle_incident_window_input(str(preset))
                st.rerun()
        return "Or type how many hours..."

    if step == "ask_incident_justification":
        return "Type a brief reason..."

    if step == "ask_cloud":
        cols = st.columns(2)
        if cols[0].button("Azure", key="cloud_azure"):
            handle_cloud_choice(Cloud.AZURE)
            st.rerun()
        if cols[1].button("GCP", key="cloud_gcp"):
            handle_cloud_choice(Cloud.GCP)
            st.rerun()
        return None

    if step == "ask_env":
        cols = st.columns(2)
        if cols[0].button("Non-Prod", key="env_nonprod"):
            handle_env_choice(Environment.NONPROD)
            st.rerun()
        if cols[1].button("Production", key="env_prod"):
            handle_env_choice(Environment.PROD)
            st.rerun()
        return None

    if step == "ask_email":
        return "Type your email address..."

    if step == "existing_access_found":
        cols = st.columns(2)
        if cols[0].button("Request additional / different access", key="existing_more"):
            handle_existing_followup(True)
            st.rerun()
        if cols[1].button("That's all, thanks", key="existing_done"):
            handle_existing_followup(False)
            st.rerun()
        return None

    if step == "ask_role":
        entries = available_entries(catalog)
        for entry in entries:
            if st.button(f"{entry.display_name} (max {entry.max_ttl_hours}h)", key=f"role_{entry.id}"):
                handle_role_choice(entry)
                st.rerun()
        return None

    if step == "ask_duration":
        max_hours = st.session_state.draft.get("max_ttl_hours", 24)
        presets = sorted({p for p in (8, 24, max_hours) if p <= max_hours})
        cols = st.columns(len(presets))
        for i, (col, preset) in enumerate(zip(cols, presets)):
            if col.button(f"{preset} hours", key=f"duration_preset_{i}"):
                handle_duration_input(str(preset))
                st.rerun()
        return "Or type how many hours..."

    if step == "ask_justification":
        return "Type your justification..."

    if step == "confirm":
        cols = st.columns(2)
        if cols[0].button("✅ Yes, submit it", key="confirm_yes"):
            handle_confirm(True, catalog)
            st.rerun()
        if cols[1].button("✋ No, cancel", key="confirm_no"):
            handle_confirm(False, catalog)
            st.rerun()
        return None

    return None


def main() -> None:
    st.set_page_config(page_title="AEGIS: Agentic Cloud Governance", layout="centered")
    st.markdown(CARD_UI_CSS, unsafe_allow_html=True)

    catalog = load_catalog()
    init_state()

    st.markdown(
        '<div class="aegis-logo"><span class="badge">⚡</span>'
        '<h1 style="margin:0;">AEGIS</h1></div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "A conversation, not a form. Tell me what you need and I'll check policy, raise the "
        "approval, and follow the change through to apply. Nothing happens without a human saying yes."
    )

    with st.sidebar:
        st.subheader("Market Rules")
        st.caption(
            "Everything Aegis needs (cloud, environment, identity, role, duration, justification) "
            "is gathered right here in the chat. Access is only ever applied after ServiceNow approval "
            "and a merged pull request."
        )
        if st.button("🔄 Start over"):
            reset_conversation()
            st.rerun()

    for index, message in enumerate(st.session_state.messages):
        render_turn(message, index)

    placeholder = render_step_controls(catalog)

    if placeholder is not None:
        prompt = st.chat_input(placeholder)
        if prompt:
            step = st.session_state.conv_step
            if step == "ask_email":
                handle_email_input(prompt, catalog)
            elif step == "ask_duration":
                handle_duration_input(prompt)
            elif step == "ask_justification":
                handle_justification_input(prompt, catalog)
            elif step == "ask_incident_email":
                handle_incident_email_input(prompt)
            elif step == "ask_resource_hint":
                handle_resource_hint_input(prompt)
            elif step == "ask_incident_window":
                handle_incident_window_input(prompt)
            elif step == "ask_incident_justification":
                handle_incident_justification_input(prompt)
            else:
                user_says(prompt)
                bot_says("Please use the buttons above to continue.")
            st.rerun()


if __name__ == "__main__":
    main()
