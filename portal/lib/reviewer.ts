export type ReviewRow={request_id:string;capability:string;status:string;justification:string;created_at:string;owner_id:string|null;owner_name:string|null;due_at:string|null;approval_deadline:string|null;verification_state:string|null;checked_at:string|null;gate_verdict:string|null;gate_reason:string|null;change_number:string|null;change_url:string|null;pr_url:string|null;monitor_enabled:boolean|null;monitor_interval_minutes?:number|null;next_check_at:string|null;total_count:number};
export function nextReviewAction(r:ReviewRow,now:number){
 if(r.status==='completed'){
  if(r.verification_state==='failed')return {label:'Investigate cloud mismatch',tone:'bad'};
  if(r.verification_state==='expired')return {label:'Grant window ended',tone:'neutral'};
  if(r.verification_state!=='verified')return {label:'Recheck cloud outcome',tone:'warn'};
  if(r.monitor_enabled&&r.checked_at&&r.monitor_interval_minutes&&Date.parse(r.checked_at)<now-(r.monitor_interval_minutes+5)*60000)return {label:'Investigate overdue cloud read',tone:'warn'};
  if(r.monitor_enabled&&r.next_check_at&&Date.parse(r.next_check_at)<now-300000)return {label:'Investigate overdue monitoring',tone:'warn'};
  return {label:'Review verified evidence',tone:'good'};
 }
 if(['cancelled','rejected','expired','revoked'].includes(r.status))return {label:'Review closed request',tone:'neutral'};
 if(r.approval_deadline&&Date.parse(r.approval_deadline)<=now)return {label:'Fresh proposal required',tone:'bad'};
 if(r.gate_verdict==='denied')return {label:'Resolve blocked execution',tone:'bad'};
 if(r.status==='failed')return {label:'Investigate failed step',tone:'bad'};
 if(r.capability==='incident_triage'&&r.status==='awaiting_approval')return {label:'Requester decides on incident',tone:'warn'};
 if(r.status==='awaiting_approval')return {label:'Independent ServiceNow review',tone:'warn'};
 if(r.status==='pr_open')return {label:'Review pull request',tone:'info'};
 if(['merged','applying'].includes(r.status))return {label:'Review exact plan and checkpoint',tone:'info'};
 return {label:'Follow workflow progress',tone:'info'};
}
export function reviewDeadline(r:ReviewRow){
 const dates=[r.due_at,...(r.status==='completed'?[]:[r.approval_deadline])].filter((d):d is string=>!!d);
 return dates.sort((a,b)=>Date.parse(a)-Date.parse(b))[0]??null;
}
