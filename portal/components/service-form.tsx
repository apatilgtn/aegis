"use client";

import { useActionState } from "react";
import { LoaderCircle } from "lucide-react";
import { createService, type ActionState } from "@/app/actions";

const initial: ActionState = { ok: false, message: "" };

export function ServiceForm() {
  const [state, action, pending] = useActionState(createService, initial);
  return (
    <form action={action} className="form">
      <label className="field"><span>Name</span><input name="name" required minLength={2} placeholder="Payments API" /></label>
      <label className="field"><span>Description</span><textarea name="description" maxLength={500} placeholder="What it does and who uses it." /></label>
      <label className="field">
        <span>Lifecycle</span>
        <select name="lifecycle" defaultValue="production">
          <option value="production">Production</option><option value="experimental">Experimental</option><option value="deprecated">Deprecated</option>
        </select>
      </label>
      <label className="field">
        <span>Tier</span>
        <select name="tier" defaultValue="tier2"><option value="tier1">Tier 1</option><option value="tier2">Tier 2</option><option value="tier3">Tier 3</option></select>
      </label>
      <label className="field"><span>Repository URL</span><input name="repositoryUrl" type="url" placeholder="https://github.com/org/repo" /></label>
      <label className="field"><span>Owning team</span><input name="ownerLabel" maxLength={120} placeholder="Payments platform" /></label>
      <label className="field"><span>Runbook URL</span><input name="runbookUrl" type="url" placeholder="https://docs.example.com/runbook" /></label>
      {state.message && <div className={`alert ${state.ok ? "good" : "bad"}`}>{state.message}</div>}
      <div><button className="btn btn-primary" disabled={pending}>{pending && <LoaderCircle className="spin" size={14} />} Add service</button></div>
    </form>
  );
}
