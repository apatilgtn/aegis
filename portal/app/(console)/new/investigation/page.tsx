import { InvestigationForm } from "@/components/forms";
import { PageHead } from "@/components/ui";
import { cloudAccount, requireContext } from "@/lib/data";
import { How } from "../how";

export default async function NewInvestigationPage({ searchParams }: { searchParams: Promise<{ hint?: string; hours?: string; reason?: string }> }) {
  const context = await requireContext();
  const params = await searchParams;
  const account = await cloudAccount(context);
  return (
    <>
      <PageHead title="Investigate an issue" lede={`Search recent warnings and errors in ${account?.projectId ?? "your GCP project"}. Reading logs changes nothing, so no approval is needed.`} />
      <div className="grid-main">
        <div className="card card-pad"><InvestigationForm prefill={{ hint: params.hint, hours: Number(params.hours) || undefined, reason: params.reason }} /></div>
        <How steps={[
          "Aegis checks the search against policy.",
          "It searches Cloud Logging for warnings and errors that mention what you entered.",
          "You get a plain summary and the latest entries, usually within seconds.",
          "You decide: raise a ServiceNow incident with the findings, or close it.",
        ]} />
      </div>
    </>
  );
}
