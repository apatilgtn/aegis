import { getContext } from "@/lib/data";
import { evidenceEnvelope } from "@/lib/evidence";
export async function GET(request: Request) {
 const context = await getContext();
 if (!context || "pending" in context || !["platform_admin","auditor"].includes(context.role)) return Response.json({error:"Auditor or administrator required"},{status:403});
 const id = new URL(request.url).searchParams.get("requestId");
 if (id && !/^[0-9a-f-]{36}$/.test(id)) return Response.json({error:"Invalid request ID"},{status:400});
 const asOf = new Date().toISOString();
 let audit = context.supabase.from("audit_events").select("*",{count:"exact"}).eq("tenant_id",context.tenantId).lte("occurred_at",asOf).order("id",{ascending:false}).limit(1000);
 if (id) audit = audit.eq("object_id",id);
 const {data:events,error,count} = await audit;
 if(error) return Response.json({error:"Evidence could not be read"},{status:503});
 let change: Record<string,unknown> | null = null;
 if(id) {
  const {data:action,error:actionError} = await context.supabase.from("action_requests").select("*").eq("tenant_id",context.tenantId).eq("id",id).maybeSingle();
  if(actionError) return Response.json({error:"Request evidence could not be read"},{status:503});
  if(!action) return Response.json({error:"Request not found"},{status:404});
  const [{data:runs,error:runError},{data:receipt,error:receiptError},{data:verification,error:verifyError},{data:plans,error:planError},{data:assessments,error:assessmentError},{data:checkpoints,error:checkpointError,count:checkpointCount},{data:native,error:nativeError,count:nativeCount},{data:observations,error:observationError,count:observationCount},{data:monitor,error:monitorError}] = await Promise.all([
   context.supabase.from("workflow_runs").select("*").eq("action_request_id",id),
   context.supabase.from("proposal_receipts").select("*").eq("action_request_id",id),
   context.supabase.from("change_verifications").select("*").eq("action_request_id",id),
   context.supabase.from("plan_approvals").select("*").eq("tenant_id",context.tenantId).contains("request_ids",[id]),
   context.supabase.from("change_assessments").select("*").eq("tenant_id",context.tenantId).eq("action_request_id",id).limit(1000),
   context.supabase.from("execution_check_events").select("*",{count:"exact"}).eq("tenant_id",context.tenantId).eq("action_request_id",id).order("checked_at",{ascending:false}).limit(1000),
   context.supabase.from("native_access_assessments").select("*",{count:"exact"}).eq("tenant_id",context.tenantId).eq("action_request_id",id).order("checked_at",{ascending:false}).limit(1000),
   context.supabase.from("verification_observations").select("*",{count:"exact"}).eq("tenant_id",context.tenantId).eq("action_request_id",id).order("checked_at",{ascending:false}).limit(1000),
   context.supabase.from("assurance_monitors").select("*").eq("tenant_id",context.tenantId).eq("action_request_id",id).maybeSingle(),
  ]);
  if(runError||receiptError||verifyError||planError||assessmentError||checkpointError||nativeError||observationError||monitorError) return Response.json({error:"A required evidence source is unavailable"},{status:503});
  const {data:workflow,error:workflowError,count:workflowCount} = await context.supabase.from("workflow_events").select("*",{count:"exact"}).in("workflow_run_id",(runs??[]).map(r=>r.id)).order("sequence_number").limit(5000);
  if(workflowError) return Response.json({error:"Workflow evidence could not be read"},{status:503});
  change={monitor,verificationObservations:observations,observationLimit:1000,observationsTruncated:(observationCount??0)>(observations?.length??0),executionCheckpoints:checkpoints,nativeAccessAssessments:native,checkpointLimit:1000,nativeAssessmentLimit:1000,checkpointsTruncated:(checkpointCount??0)>(checkpoints?.length??0),nativeAssessmentsTruncated:(nativeCount??0)>(native?.length??0),advisoryAssessments:assessments,assessmentLimit:1000,request:action,runs,receipt,verification,planApprovals:plans,workflowEvents:workflow,workflowTruncated:(workflowCount??0)>(workflow?.length??0)};
 }
 const payload={schemaVersion:1,workspaceId:context.tenantId,exportedBy:context.userId,asOf,scope:id?"Single governed request":"Latest workspace audit events",requestId:id,auditEvents:events,auditTotal:count,auditTruncated:(count??0)>(events?.length??0),change,limitations:["An export signature proves integrity relative to the trusted signing key, not regulatory compliance.","Cloud Logging retention and recovery guarantees must be configured separately."]};
 try {
  const key=process.env.AEGIS_AUDIT_SIGNING_KEY_BASE64 ? Buffer.from(process.env.AEGIS_AUDIT_SIGNING_KEY_BASE64,"base64").toString("utf8") : undefined;
  return Response.json(evidenceEnvelope(payload,key),{headers:{"Content-Disposition":`attachment; filename="aegis-evidence-${id??"workspace"}.json"`,"Cache-Control":"no-store"}});
 } catch {return Response.json({error:"Evidence signing failed; no misleading unsigned export was substituted"},{status:503});}
}
