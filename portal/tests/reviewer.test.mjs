import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
const api={};vm.runInNewContext(ts.transpileModule(fs.readFileSync(new URL('../lib/reviewer.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,{exports:api,Date});
const now=Date.parse('2026-11-08T12:00:00Z');
test('coordination cannot hide an earlier approval deadline',()=>{
 assert.equal(api.reviewDeadline({status:'pr_open',due_at:'2026-11-09T12:00:00Z',approval_deadline:'2026-11-08T10:00:00Z'}),'2026-11-08T10:00:00Z');
 assert.equal(api.nextReviewAction({status:'pr_open',approval_deadline:'2026-11-08T10:00:00Z'},now).label,'Fresh proposal required');
});
test('delivered changes remain verified despite their historical approval expiry',()=>{
 const r={status:'completed',verification_state:'verified',approval_deadline:'2026-11-08T10:00:00Z'};
 assert.equal(api.nextReviewAction(r,now).tone,'good');
 assert.equal(api.reviewDeadline(r),null);
});
test('mismatch, unavailable and stale polling each require distinct action',()=>{
 assert.match(api.nextReviewAction({status:'completed',verification_state:'failed'},now).label,/mismatch/);
 assert.match(api.nextReviewAction({status:'completed',verification_state:'unavailable'},now).label,/Recheck/);
 assert.match(api.nextReviewAction({status:'completed',verification_state:'verified',monitor_enabled:true,next_check_at:'2026-11-08T11:00:00Z'},now).label,/overdue/);
});
test('incident decisions are routed to requester instead of access approval',()=>{
 assert.match(api.nextReviewAction({status:'awaiting_approval',capability:'incident_triage'},now).label,/Requester/);
});
test('fresh scheduling does not hide an overdue cloud read',()=>{
 assert.match(api.nextReviewAction({status:'completed',verification_state:'verified',monitor_enabled:true,monitor_interval_minutes:15,next_check_at:'2026-11-08T12:10:00Z',checked_at:'2026-11-08T11:00:00Z'},now).label,/overdue cloud read/);
});
