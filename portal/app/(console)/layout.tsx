import { AccessPending } from "@/app/access-pending";
import { Shell } from "@/components/shell";
import { getContext, governanceState, listRequests } from "@/lib/data";
import { nvidiaConfigured } from "@/lib/nvidia";
import { isOpen, needsMe } from "@/lib/view";

export const dynamic = "force-dynamic";

export default async function ConsoleLayout({ children }: { children: React.ReactNode }) {
  const context = await getContext();
  if (!context || "pending" in context) {
    const pending = context && "pending" in context ? context.pending : { displayName: "there", email: "" };
    return <AccessPending displayName={pending.displayName} email={pending.email} />;
  }
  const [requests, governance] = await Promise.all([listRequests(context), governanceState(context)]);
  return (
    <Shell
      displayName={context.displayName}
      email={context.email}
      role={context.role}
      tenantId={context.tenantId}
      tenantName={context.tenantName}
      counts={{
        needsMe: requests.filter((request) => needsMe(request, context.userId)).length,
        open: requests.filter(isOpen).length,
      }}
      aiEnabled={nvidiaConfigured()}
    >
      {governance.settings && (!governance.settings.writes_enabled || governance.settings.proposals_paused || governance.settings.executions_paused) && <div className="workspace-control-banner"><span className="dot warn" /><span>{!governance.settings.writes_enabled ? "Governed writes are disabled." : governance.settings.executions_paused ? "New executions are paused." : "Proposal processing is paused."}</span><a href="/administration">View controls →</a></div>}
      {children}
    </Shell>
  );
}
