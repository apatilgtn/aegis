import Link from 'next/link';
import {PageHead} from '@/components/ui';
import {ReviewerInbox} from '@/components/reviewer-inbox';
import {requireContext} from '@/lib/data';
import {type ReviewRow} from '@/lib/reviewer';
export default async function Inbox({searchParams}:{searchParams:Promise<{view?:string;page?:string}>}){
 const context=await requireContext();if(!['platform_admin','auditor'].includes(context.role))return <><PageHead title="Reviewer inbox" lede="A coordinator workspace for administrators and auditors."/><Link className="btn" href="/requests">View your requests</Link></>;
 const p=await searchParams,view=['attention','mine','all'].includes(p.view??'')?p.view!:'attention',page=Math.max(1,Math.min(2001,Math.floor(Number(p.page)||1)));
 const {data,error}=await context.supabase.rpc('reviewer_inbox',{p_tenant:context.tenantId,p_view:view,p_offset:(page-1)*50});if(error)throw new Error('Reviewer inbox could not be read');
 const rows=(data??[]) as ReviewRow[];
 if(rows.length){const {data:cadences,error:cadenceError}=await context.supabase.from('assurance_monitors').select('action_request_id,interval_minutes').eq('tenant_id',context.tenantId).in('action_request_id',rows.map(r=>r.request_id));if(cadenceError)throw new Error('Monitoring cadence unavailable');for(const r of rows)r.monitor_interval_minutes=cadences?.find(m=>m.action_request_id===r.request_id)?.interval_minutes??null;}
 // eslint-disable-next-line react-hooks/purity -- one server response clock
 const now=Date.now();
 return <><PageHead title="Reviewer inbox" lede="Own the next step. See deadlines, blocked execution and cloud mismatches in one place."><Link className="btn" href="/assurance">Continuous assurance</Link></PageHead><div className="queue-tabs">{[['attention','Needs attention'],['mine','Owned by me'],['all','All requests']].map(([key,label])=><Link className={key===view?'on':''} href={`/inbox?view=${key}`} key={key}>{label}</Link>)}</div><p className="muted">Coordination ownership does not grant approval rights. The earliest approval or coordination deadline is shown; taking ownership cannot extend an approval.</p><ReviewerInbox rows={rows} userId={context.userId} admin={context.role==='platform_admin'} now={now}/><div className="pagination"><span>{rows[0]?.total_count??0} requests · page {page}</span><div>{page>1&&<Link className="btn" href={`/inbox?view=${view}&page=${page-1}`}>Previous</Link>}{rows.length===50&&page*50<(rows[0]?.total_count??0)&&<Link className="btn" href={`/inbox?view=${view}&page=${page+1}`}>Next</Link>}</div></div></>;
}
