import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';
import vm from 'node:vm';
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const api = {};
const source = fs.readFileSync(path.join(__dirname, '../lib/change-review.ts'), 'utf8');
vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, { exports: api, TextEncoder });
const { parsePlan, dependentsOf, SAMPLE_PLAN } = api;
const plan = (resources, extra = {}) => JSON.stringify({ format_version: '1.2', resource_changes: resources, ...extra });
const resource = (type, after, extra = {}) => ({ address: `${type}.test`, type, mode: 'managed', change: { actions: ['create'], after, ...extra } });

test('detects destructive database changes, privilege escalation and transitive declared impact', () => {
  const report = parsePlan(SAMPLE_PLAN);
  assert.equal(report.nodes.length, 4);
  assert.ok(report.findings.some(f => f.title === 'Resource replacement' && f.severity === 'critical'));
  assert.ok(report.findings.some(f => f.title === 'Privileged IAM role'));
  assert.ok(report.findings.some(f => f.title === 'Access granted to a public principal'));
  assert.equal(dependentsOf(report, 'google_sql_database_instance.orders').length, 2);
  assert.equal(dependentsOf(report, 'google_project_iam_member.agent').length, 0);
});
test('never retains raw plan values or exposes sensitive IAM attributes in findings', () => {
  const report = parsePlan(plan([resource('google_project_iam_member', { role: 'roles/editor', member: 'allUsers', secret: 'secret-value' }, { after_sensitive: true })]));
  assert.ok(report.findings.some(f => f.title === 'Sensitive values excluded from detailed checks'));
  assert.ok(!JSON.stringify(report).includes('secret-value'));
  assert.ok(!report.findings.some(f => f.title === 'Privileged IAM role'));
});
test('does not flag public access expansion when a public grant is only being deleted', () => {
  const report = parsePlan(plan([resource('google_project_iam_member', null, { actions: ['delete'], before: { member: 'allUsers' } })]));
  assert.ok(!report.findings.some(f => f.title === 'Access granted to a public principal'));
  assert.ok(report.findings.some(f => f.title === 'Resource deletion'));
});
test('marks incomplete plans, unknown values and unsupported providers as uncertainty', () => {
  const report = parsePlan(plan([resource('aws_s3_bucket', {}, { after_unknown: { arn: true } })], { complete: false }));
  assert.equal(report.incomplete, true);
  assert.equal(report.unsupported, 1);
  assert.ok(report.findings.some(f => f.title === 'Some values are unknown until apply'));
});
test('resolves indexed resources in child modules and handles dependency cycles', () => {
  const resources = ['a', 'b'].map(name => ({ ...resource('google_storage_bucket', {}), address: `module.store.google_storage_bucket.${name}[0]` }));
  const configuration = { root_module: { module_calls: { store: { module: { resources: [
    { address: 'google_storage_bucket.a', expressions: { label: { references: ['google_storage_bucket.b.id'] } } },
    { address: 'google_storage_bucket.b', expressions: { label: { references: ['google_storage_bucket.a.id'] } } },
  ] } } } } };
  const report = parsePlan(plan(resources, { configuration }));
  assert.equal(dependentsOf(report, resources[0].address).length, 1);
});
test('ignores data reads and unchanged managed resources', () => {
  const report = parsePlan(plan([resource('google_storage_bucket', {}, { actions: ['no-op'] }), { ...resource('google_project', {}, { actions: ['read'] }), mode: 'data' }]));
  assert.equal(report.nodes.length, 0);
  assert.equal(report.findings.length, 0);
});
test('rejects malformed, unsupported and oversized plans instead of producing a clean result', () => {
  for (const input of ['not JSON', '{}', '{"format_version":"2.0","resource_changes":[]}', ' '.repeat(1_000_001), plan([{}])]) assert.throws(() => parsePlan(input));
});

test('inspects public and privileged grants inside authoritative policy_data JSON', () => {
 const report=parsePlan(plan([resource('google_project_iam_policy',{policy_data:JSON.stringify({bindings:[{role:'roles/owner',members:['allUsers']}]})})]));
 assert.ok(report.findings.some(f=>f.title==='Access granted to a public principal'));
 assert.ok(report.findings.some(f=>f.title==='Privileged IAM role'));
 const hidden=parsePlan(plan([resource('google_project_iam_policy',{policy_data:JSON.stringify({bindings:[{role:'roles/owner',members:['allUsers']}]})},{after_sensitive:{policy_data:true}})]));
 assert.ok(!hidden.findings.some(f=>f.title==='Access granted to a public principal'));
});
