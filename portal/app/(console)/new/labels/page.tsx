import Link from "next/link";
import { LabelsForm } from "@/components/forms";
import { PageHead } from "@/components/ui";
import { cloudAccount, latestSnapshot, requireContext, type LabelSnapshot } from "@/lib/data";
import { timeAgo } from "@/lib/view";

export default async function NewLabelsPage({ searchParams }: { searchParams: Promise<{ bucket?: string; costCenter?: string; reason?: string }> }) {
  const context = await requireContext();
  const params = await searchParams;
  const [snapshot,account] = await Promise.all([latestSnapshot<LabelSnapshot>(context,"label_compliance"),cloudAccount(context)]);
  const buckets = (snapshot?.buckets ?? []).filter((bucket) => bucket.missing.length).map((bucket) => ({ bucket: bucket.bucket, missing: bucket.missing, labels: bucket.labels }));
  return (
    <>
      <PageHead title="Fix missing labels" lede={`Adds only the required labels a bucket is missing.${snapshot ? ` Labels last checked ${timeAgo(snapshot.capturedAt)}.` : ""}`} />

          {buckets.length ? (
            <LabelsForm identity={context.email} project={account?.projectId} environment={account?.environment} buckets={buckets} prefill={{ bucket: params.bucket, costCenter: params.costCenter, reason: params.reason }} />
          ) : (
            <div className="rows-empty">
              <strong>{snapshot ? snapshot.total ? "Every bucket is fully labelled" : "No buckets were observed" : "Labels have not been checked yet"}</strong>
              {snapshot ? "There is nothing to fix in this snapshot." : "The worker checks every 15 minutes."} <Link className="link" href="/labels">View label compliance</Link>
            </div>
          )}

    </>
  );
}
