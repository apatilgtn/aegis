import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';
import vm from 'node:vm';
const api={};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(new URL('../lib/view.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,{exports:api,Date});
const base={id:'request',capability:'access_grant',status:'completed',resource:{},policy:{allowed:true,reasons:[],policyIds:[]},servicenow:{},github:{},delivery:{},triage:{},createdAt:'2026-11-08T00:00:00Z',expiresAt:'2100-01-01T00:00:00Z'};
test('pipeline success cannot claim access live without cloud verification',()=>{
 assert.equal(api.statusOf(base).tone,'warn');
 assert.match(api.statusOf(base).label,/unverified/);
 assert.equal(api.stepsOf(base).find(s=>s.key==='verification').state,'current');
});
test('verified binding and mismatched read-back have distinct outcomes',()=>{
 assert.equal(api.statusOf({...base,verification:{state:'verified'}}).tone,'good');
 assert.equal(api.statusOf({...base,verification:{state:'failed'}}).tone,'bad');
 assert.match(api.statusOf({...base,verification:{state:'unavailable'}}).label,/unavailable/);
});
test('execution authorization is separate from completed pipeline and read-back',()=>{
 const steps=api.stepsOf({...base,execution:{verdict:'authorized',checkpoint:'pre_apply',at:'2026-11-08T00:00:00Z'}});
 assert.equal(steps.find(s=>s.key==='execution_gate').state,'done');
 assert.notEqual(steps.find(s=>s.key==='verification').state,'done');
});
test('incident completion does not require infrastructure read-back',()=>{
 assert.equal(api.statusOf({...base,capability:'incident_triage'}).label,'Incident raised');
});
const journey={};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(new URL('../lib/journey.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,{exports:journey,Date});
test('overview never draws a live grant span from delivery success alone',()=>{
 const events=['policy.allowed','approval.approved','github.proposal_created','delivery.merged','delivery.completed'].map((type,i)=>({type,at:`2026-11-08T0${i+1}:00:00Z`}));
 const request={...base,updatedAt:'2026-11-08T05:00:00Z',expiresAt:'2026-11-08T10:00:00Z'};
 const now=new Date('2026-11-08T06:00:00Z').getTime();
 assert.ok(!journey.segmentsOf(request,events,now).some(s=>s.stage==='live'));
 const verified=journey.segmentsOf({...request,verification:{state:'verified',checkedAt:'2026-11-08T05:30:00Z'}},events,now).find(s=>s.stage==='live');
 assert.equal(verified.start,new Date('2026-11-08T05:30:00Z').getTime());
});
test('approval dates display an explicit stable timezone',()=>{
 assert.match(api.formatDate('2026-11-08T12:00:00Z'),/12:00 pm UTC$/);
});
