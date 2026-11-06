import { ChangeReviewWorkspace } from "@/components/change-review";
import { getRequest, requireContext } from "@/lib/data";
import { nvidiaConfigured } from "@/lib/nvidia";
import type { ChangeReview } from "@/lib/change-review";
import { notFound } from "next/navigation";
export default async function ChangeReviewPage({searchParams}:{searchParams:Promise<{requestId?:string}>}) {
 const context=await requireContext();const params=await searchParams;
 if(params.requestId&&!(await getRequest(context,params.requestId)))notFound();
 let query=context.supabase.from("change_assessments").select("id,input_sha256,created_at,report").eq("tenant_id",context.tenantId).order("created_at",{ascending:false}).limit(20);
 if(params.requestId)query=query.eq("action_request_id",params.requestId);
 const{data}=await query;
 return <ChangeReviewWorkspace aiEnabled={nvidiaConfigured()} requestId={params.requestId} history={(data??[]) as {id:string;input_sha256:string;created_at:string;report:ChangeReview}[]}/>;
}
