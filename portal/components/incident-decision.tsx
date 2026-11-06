"use client";

import { useActionState } from "react";
import { LoaderCircle } from "lucide-react";
import { decideIncident, type ActionState } from "@/app/actions";
import { ServiceNowOriginalIcon } from "./brand-icons";

const initial: ActionState = { ok: false, message: "" };

export function IncidentDecision({ requestId }: { requestId: string }) {
  const [state, action, pending] = useActionState(decideIncident, initial);
  return (
    <form action={action} style={{ display: "grid", gap: 8 }}>
      <input type="hidden" name="requestId" value={requestId} />
      <div className="actions">
        <button name="decision" value="raise" className="btn btn-primary" disabled={pending || state.ok}>
          {pending ? <LoaderCircle className="spin" size={14} /> : <ServiceNowOriginalIcon width={14} height={14} />}
          Raise ServiceNow incident
        </button>
        <button name="decision" value="close" className="btn" disabled={pending || state.ok}>
          No incident needed
        </button>
      </div>
      {state.message && <div className={`alert ${state.ok ? "good" : "bad"}`}>{state.message}</div>}
    </form>
  );
}
