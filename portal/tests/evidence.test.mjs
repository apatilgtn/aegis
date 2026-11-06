import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createHash, generateKeyPairSync, verify} from 'node:crypto';
import {createRequire} from 'node:module';
import ts from 'typescript';
import vm from 'node:vm';
const api = {};
const source = fs.readFileSync(new URL('../lib/evidence.ts',import.meta.url),'utf8');
vm.runInNewContext(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,{exports:api,require:createRequire(import.meta.url),Buffer});
test('evidence canonicalization is stable across object insertion order',()=>{
 assert.equal(api.canonicalJson({b:2,a:[{z:1,x:null}]}),api.canonicalJson({a:[{x:null,z:1}],b:2}));
});
test('signature verifies original evidence and rejects modified evidence',()=>{
 const {privateKey,publicKey}=generateKeyPairSync('ed25519');
 const envelope=api.evidenceEnvelope({workspace:'one',events:[{id:1}]},privateKey.export({type:'pkcs8',format:'pem'}));
 const original=Buffer.from(api.canonicalJson(envelope.payload));
 assert.equal(envelope.integrity.sha256,createHash('sha256').update(original).digest('hex'));
 assert.equal(verify(null,original,publicKey,Buffer.from(envelope.integrity.signature,'base64')),true);
 assert.equal(verify(null,Buffer.from(api.canonicalJson({workspace:'two',events:[]})),publicKey,Buffer.from(envelope.integrity.signature,'base64')),false);
});
test('unconfigured signing is explicit and unsuitable keys fail closed',()=>{
 assert.equal(api.evidenceEnvelope({}).integrity.signed,false);
 const {privateKey}=generateKeyPairSync('rsa',{modulusLength:2048});
 assert.throws(()=>api.evidenceEnvelope({},privateKey.export({type:'pkcs8',format:'pem'})),/Ed25519/);
});
