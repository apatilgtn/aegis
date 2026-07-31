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
from contracts.capability_contract import AccessGrantRequest, Cloud, Environment, Tier
from contracts.entitlement_catalog import EntitlementCatalog, EntitlementCatalogEntry

CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "entitlement_catalog.yaml"

ENV_LABELS = {Environment.NONPROD: "Non-Prod", Environment.PROD: "Production"}

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

APPROVAL_STATE_LABELS = {
    "requested": "🟡 Pending",
    "approved": "✅ Approved",
    "rejected": "❌ Rejected",
    "cancelled": "⚪ Cancelled",
    "not requested": "⚪ Not yet requested",
}

PR_STATE_LABELS = {
    "open": "🟡 Open — not merged yet",
    "merged": "✅ Merged",
    "closed": "❌ Closed without merging",
}

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


# ---- conversation state helpers --------------------------------------------------

def init_state() -> None:
    if "conv_step" in st.session_state:
        return
    st.session_state.conv_step = "ask_cloud"
    st.session_state.draft = {}
    st.session_state.messages = [
        text_turn(
            "assistant",
            "Hi, I'm **Aegis** ⚡ — I can check your current cloud access or help you request new "
            "access. Nothing is ever applied without human approval.\n\nFirst: which cloud — Azure or GCP?",
        )
    ]


def text_turn(role: str, content: str) -> dict:
    return {"kind": "text", "role": role, "content": content}


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
    st.session_state.conv_step = "ask_cloud"
    st.session_state.draft = {}
    st.session_state.messages = [
        text_turn("assistant", "Let's start over — which cloud, Azure or GCP?")
    ]


# ---- step handlers ----------------------------------------------------------------

def handle_cloud_choice(cloud: Cloud) -> None:
    user_says(cloud.value.upper())
    st.session_state.draft["cloud"] = cloud
    st.session_state.conv_step = "ask_env"
    bot_says("Which environment — Non-Prod or Production?")


def handle_env_choice(environment: Environment) -> None:
    user_says(ENV_LABELS[environment])
    if environment == Environment.PROD:
        bot_says(
            "Production access isn't offered through this self-service flow — only Non-Prod is "
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
        bot_says("That doesn't look like a valid email — could you try again?")
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
            bot_says("There's nothing available to request for this combination yet — contact the platform team.")
            st.session_state.conv_step = "idle_done"


def handle_existing_followup(wants_more: bool) -> None:
    if wants_more:
        user_says("Request additional / different access")
        bot_says("Here's what's available:")
        st.session_state.conv_step = "ask_role"
    else:
        user_says("That's all, thanks")
        bot_says("Sounds good — I'm here if you need anything else. Use \"Start over\" in the sidebar anytime.")
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
        bot_says("I couldn't find a number of hours in that — how many hours do you need?")
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
    d = st.session_state.draft
    summary = (
        "**Here's what I'll submit:**\n"
        f"- Cloud: **{d['cloud'].value.upper()}**\n"
        f"- Business unit: **{d['business_unit'].title()}**\n"
        f"- Access level: **{human_tier(d['tier'])}**\n"
        f"- Duration: **{d['ttl_hours']} hours**\n"
        f"- Justification: _{d['justification']}_\n\n"
        "Shall I submit this request?"
    )
    st.session_state.conv_step = "confirm"
    bot_says(summary)


def handle_confirm(yes: bool, catalog: EntitlementCatalog) -> None:
    if not yes:
        user_says("No, cancel")
        bot_says("No problem — cancelled. Want to start a new request? Which cloud — Azure or GCP?")
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

    instance_url = os.getenv("SN_INSTANCE_URL", "")
    request_link = f"{instance_url}/sc_request.do?sys_id={result.approval.request_sys_id}" if instance_url else None
    approval_ref = f"[{result.approval.reference}]({request_link})" if request_link else f"**{result.approval.reference}**"

    content = (
        f"**🟡 Passed policy checks — sent for human approval.** Nothing has been granted yet.\n\n"
        f"Request {approval_ref} is now waiting in ServiceNow. **No pull request has been opened "
        "yet** — one will only be raised once a human approves this in ServiceNow, so a declined "
        "request never leaves a proposed change sitting on GitHub.\n\n"
        "Use \"Refresh pipeline status\" below to check — I'll open the PR automatically the moment "
        "it's approved, and offer to merge it for you."
    )
    st.session_state.messages.append(result_turn(content, result))
    st.session_state.conv_step = "done"


# ---- rendering ----------------------------------------------------------------

def render_technical_details(result) -> None:
    with st.expander("🔧 Technical details (for platform / audit team)"):
        pr_line = (
            f"- Pull request: [#{result.pull_request.number}]({result.pull_request.html_url}) "
            f"(branch `{result.pull_request.branch}`)\n"
            if result.pull_request
            else f"- Pull request: not yet raised — will open on branch `{result.branch_name}` once approved\n"
        )
        st.markdown(
            f"- Policy check: `{', '.join(result.policy_decision.policy_ids)}`\n"
            f"- Catalog entry: `{result.entitlement.catalog_entry_id}`\n"
            f"- Resource: `{result.entitlement.resource_id}` ({result.entitlement.resource_type})\n"
            f"- ServiceNow request: `{result.approval.reference}` "
            f"(request sys_id `{result.approval.request_sys_id}`, "
            f"approval sys_id `{result.approval.approval_sys_id}`, state at creation `{result.approval.state}`)\n"
            f"{pr_line}"
            f"- Expires (UTC): `{result.expires_at.isoformat()}`"
        )
        st.code(result.iac_diff, language="hcl")


def render_pipeline_steps(message: dict) -> None:
    result = message["result"]
    approval_state = message.get("last_checked_approval")
    pr_state = message.get("last_checked_pr")
    build = message.get("last_checked_build")

    lines = ["**Pipeline status:**", "1. ✅ Policy check — passed"]

    if message.get("rejected"):
        rejected_label = APPROVAL_STATE_LABELS.get(approval_state, f"❌ {approval_state}")
        lines.append(f"2. {rejected_label} — ServiceNow request `{result.approval.reference}`")
        lines.append("3. ⚪ No pull request was raised — a declined request never reaches GitHub")
        lines.append("4. ⚪ Nothing to apply")
        st.markdown("\n".join(lines))
        return

    approval_label = APPROVAL_STATE_LABELS.get(approval_state, "⚪ Not checked yet") if approval_state else "⚪ Not checked yet"
    lines.append(f"2. {approval_label} — ServiceNow request `{result.approval.reference}`")

    if result.pull_request is None:
        lines.append("3. ⚪ Not raised yet — opens automatically once approved")
        lines.append("4. ⚪ Waiting on approval, then PR merge, before the pipeline can apply")
        st.markdown("\n".join(lines))
        return

    pr_label = PR_STATE_LABELS.get(pr_state, "⚪ Not checked yet") if pr_state else "⚪ Not checked yet"
    lines.append(f"3. {pr_label} — [PR #{result.pull_request.number}]({result.pull_request.html_url})")

    if pr_state != "merged":
        lines.append("4. ⚪ Waiting on PR merge before the pipeline can apply")
    elif build is None:
        lines.append("4. 🟡 Merged — GitHub Actions run not found yet (may take a few seconds to start)")
    else:
        build_label = BUILD_STATUS_LABELS.get(build.status, build.status)
        lines.append(f"4. {build_label} — [GitHub Actions run]({build.log_url})")

    st.markdown("\n".join(lines))


def render_result_turn(message: dict, index: int) -> None:
    st.markdown(message["content"])
    result = message["result"]
    render_technical_details(result)
    render_pipeline_steps(message)

    if message.get("rejected"):
        return

    cols = st.columns(2)
    if cols[0].button("🔄 Refresh pipeline status", key=f"refresh_{index}"):
        with st.spinner("Checking ServiceNow — opening the PR now if it's just been approved..."):
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


def render_turn(message: dict, index: int) -> None:
    with st.chat_message(message["role"]):
        if message["kind"] == "result":
            render_result_turn(message, index)
        else:
            st.markdown(message["content"])


# ---- step-specific controls ----------------------------------------------------

def render_step_controls(catalog: EntitlementCatalog) -> str | None:
    """Renders buttons for the current step, if any. Returns a placeholder
    string for the chat_input, used generically for free-text steps."""
    step = st.session_state.conv_step

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
    st.set_page_config(page_title="AEGIS — Agentic Cloud Governance", layout="centered")
    st.markdown(NEOBRUTALISM_CSS, unsafe_allow_html=True)

    catalog = load_catalog()
    init_state()

    st.markdown(
        '<div class="aegis-logo"><span class="badge">⚡</span>'
        '<h1 style="margin:0;">AEGIS</h1></div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "A conversation, not a form — tell me what you need and I'll check policy, raise the "
        "approval, and follow the change through to apply. Nothing happens without a human saying yes."
    )

    with st.sidebar:
        st.subheader("About")
        st.caption(
            "Everything Aegis needs — cloud, environment, identity, role, duration, justification — "
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
            else:
                user_says(prompt)
                bot_says("Please use the buttons above to continue.")
            st.rerun()


if __name__ == "__main__":
    main()
