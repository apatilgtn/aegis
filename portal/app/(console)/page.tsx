import { Overview } from "@/components/overview";
import { latestSnapshot, listRequestsWithEvents, requireContext, type LabelSnapshot } from "@/lib/data";

export default async function OverviewPage({ searchParams }: { searchParams: Promise<{ range?: string; type?: string; who?: string }> }) {
  const context = await requireContext();
  const [params, { requests, eventsByRequest }, labels] = await Promise.all([
    searchParams,
    listRequestsWithEvents(context),
    latestSnapshot<LabelSnapshot>(context, "label_compliance"),
  ]);
  // eslint-disable-next-line react-hooks/purity -- fresh clock for this server response
  const now = Date.now();
  return <Overview requests={requests} eventsByRequest={eventsByRequest} labels={labels} userId={context.userId} params={params} now={now} />;
}
