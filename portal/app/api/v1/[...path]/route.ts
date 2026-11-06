import { createClient } from "@supabase/supabase-js";
import { getSupabaseEnv } from "@/lib/supabase/env";
import { canonicalJson } from "@/lib/evidence";
import { parsePlan } from "@/lib/change-review";
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
async function apiContext(request:Request) {
 const token=request.headers.get("Authorization")?.match(/^Bearer (.+)$/)?.[1];
 if(!token) return null;
 const {url,publishableKey}=getSupabaseEnv();
 const supabase=createClient(url,publishableKey,{global:{headers:{Authorization:`Bearer ${token}`}},auth:{persistSession:false,autoRefreshToken:false}});
 const {data,error}=await supabase.auth.getUser(token);
 if(error||!data.user) return null;
 let query=supabase.from("tenant_memberships").select("tenant_id,role").eq("user_id",data.user.id).eq("status","active");
 const workspace=request.headers.get("X-Aegis-Workspace");if(workspace){if(!UUID.test(workspace))return null;query=query.eq("tenant_id",workspace);}
 const {data:memberships,error:memberError}=await query.limit(2);
 if(memberError||memberships?.length!==1)return null;
 return {supabase,userId:data.user.id,tenantId:memberships[0].tenant_id,role:memberships[0].role};
}
export async function GET(request:Request,{params}:{params:Promise<{path:string[]}>}) {
 const context=await apiContext(request);if(!context)return Response.json({error:"A valid user bearer token and unambiguous active workspace are required"},{status:401});
 const path=(await params).path;
 if(path.join("/")==="capabilities") {
  const [{data:policies,error},{data:settings,error:settingsError},{data:accounts,error:accountError}]=await Promise.all([context.supabase.from("capability_policies").select("*").eq("tenant_id",context.tenantId),context.supabase.from("governance_settings").select("*").eq("tenant_id",context.tenantId),context.supabase.from("tenant_cloud_accounts").select("project_id,environment,enabled").eq("tenant_id",context.tenantId)]);
  if(error||settingsError||accountError)return Response.json({error:"Governance configuration unavailable"},{status:503});
  return Response.json({workspaceId:context.tenantId,capabilities:policies,controls:settings,accounts});
 }
 if(path[0]==="requests"&&path.length===2&&UUID.test(path[1])) {
  const {data,error}=await context.supabase.from("action_requests").select("id,capability,status,resource,policy_decision,created_at,updated_at,expires_at").eq("tenant_id",context.tenantId).eq("id",path[1]).maybeSingle();
  if(error)return Response.json({error:"Request unavailable"},{status:503});
  return data?Response.json(data):Response.json({error:"Not found"},{status:404});
 }
 return Response.json({error:"Not found"},{status:404});
}
export async function POST(request:Request,{params}:{params:Promise<{path:string[]}>}) {
 const context=await apiContext(request);if(!context)return Response.json({error:"A valid user bearer token and active workspace are required"},{status:401});
 const path=(await params).path.join("/");
 const text=await request.text();if(new TextEncoder().encode(text).length>1_000_000)return Response.json({error:"Body too large"},{status:413});
 if(path==="review") {try{return Response.json(parsePlan(text));}catch(e){return Response.json({error:e instanceof Error?e.message:"Invalid plan"},{status:400});}}
 if(path!=="requests")return Response.json({error:"Not found"},{status:404});
 let body:Record<string,unknown>;try{body=JSON.parse(text);if(!body||typeof body!=="object"||Array.isArray(body))throw new Error("Invalid body");}catch{return Response.json({error:"Invalid JSON"},{status:400});}
 const key=request.headers.get("Idempotency-Key");if(!key||!UUID.test(key))return Response.json({error:"A UUID Idempotency-Key header is required"},{status:400});
 const capability=String(body.capability),justification=String(body.justification??"").trim();
 if(!["access_grant","label_fix","incident_triage"].includes(capability)||justification.length<10||justification.length>2000)return Response.json({error:"Invalid capability or justification"},{status:400});
 const {data:accounts,error:accountError}=await context.supabase.from("tenant_cloud_accounts").select("project_id,environment").eq("tenant_id",context.tenantId).eq("enabled",true).limit(2);
 if(accountError||accounts?.length!==1)return Response.json({error:"One governed project must be configured"},{status:409});
 const account=accounts[0];let serviceId:string|null=null;
 const resource:Record<string,unknown>={cloud:"gcp",environment:account.environment,project_id:account.project_id};
 if(capability==="access_grant") {
  if(!UUID.test(String(body.serviceId)))return Response.json({error:"A reviewed service is required"},{status:400});
  const {data:route,error}=await context.supabase.from("service_entitlements").select("*").eq("tenant_id",context.tenantId).eq("service_id",body.serviceId).eq("cloud","gcp").eq("environment",account.environment).eq("tier",body.tier).eq("enabled",true).maybeSingle();
  const hours=Number(body.ttlHours);
  if(error||!route||route.target_identifier!==account.project_id||!Number.isInteger(hours)||hours<1||hours>route.max_ttl_hours)return Response.json({error:"Invalid reviewed route or duration"},{status:400});
  serviceId=route.service_id;Object.assign(resource,{tier:route.tier,ttl_hours:hours,scope_type:route.scope_type,scope_identifier:route.scope_identifier});
 } else {
  const {data:service}=await context.supabase.from("services").select("id").eq("tenant_id",context.tenantId).order("created_at").limit(1).maybeSingle();serviceId=service?.id??null;
  if(capability==="incident_triage") {
   const hint=String(body.resourceHint??"").trim(),hours=Number(body.timeWindowHours);
   if(!/^[A-Za-z0-9 ._:/@-]{2,120}$/.test(hint)||![1,4,24,72,168].includes(hours))return Response.json({error:"Invalid log search"},{status:400});
   Object.assign(resource,{resource_hint:hint,time_window_hours:hours});
  } else {
   const bucket=String(body.bucket??""),center=String(body.costCenter??"");
   if(!/^[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]$/.test(bucket)||!/^[a-z0-9][a-z0-9_-]{1,62}$/.test(center))return Response.json({error:"Invalid bucket or cost center"},{status:400});
   Object.assign(resource,{bucket,cost_center:center});
  }
 }
 const {data:existing}=await context.supabase.from("action_requests").select("id,capability,status,requester_id,service_id,justification,resource").eq("tenant_id",context.tenantId).eq("idempotency_key",key).maybeSingle();
 if(existing){if(existing.requester_id!==context.userId||existing.capability!==capability||existing.justification!==justification||existing.service_id!==serviceId||canonicalJson(existing.resource)!==canonicalJson(resource))return Response.json({error:"Idempotency key already belongs to a different request"},{status:409});return Response.json({id:existing.id,status:existing.status,replayed:true});}
 const {data,error}=await context.supabase.from("action_requests").insert({tenant_id:context.tenantId,requester_id:context.userId,beneficiary_id:context.userId,service_id:serviceId,capability,justification,resource,status:"awaiting_policy",risk_level:"unknown",idempotency_key:key,submitted_at:new Date().toISOString()}).select("id,status").single();
 if(error)return Response.json({error:"The request could not be submitted"},{status:error.code==="23505"?409:400});
 return Response.json({...data,message:"Proposal queued for policy evaluation. No cloud mutation was performed."},{status:201});
}
