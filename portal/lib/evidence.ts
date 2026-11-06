import { createHash, createPrivateKey, createPublicKey, sign } from "node:crypto";

export function canonicalJson(value: unknown): string {
 if (value === null || typeof value !== "object") return JSON.stringify(value);
 if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
 return `{${Object.keys(value).sort().map(k => `${JSON.stringify(k)}:${canonicalJson((value as Record<string,unknown>)[k])}`).join(",")}}`;
}
export function evidenceEnvelope(payload: unknown, privateKey?: string) {
 const bytes = Buffer.from(canonicalJson(payload));
 const sha256 = createHash("sha256").update(bytes).digest("hex");
 if (!privateKey) return { payload, integrity: { canonicalization: "Sorted object keys, JSON serialization", sha256, signed: false, reason: "No audit signing key is configured" } };
 const key = createPrivateKey(privateKey);
 if (key.asymmetricKeyType !== "ed25519") throw new Error("Audit signing key must be Ed25519");
 return { payload, integrity: { canonicalization: "Sorted object keys, JSON serialization", sha256, signed: true, algorithm: "Ed25519", signature: sign(null, bytes, key).toString("base64"), publicKey: createPublicKey(key).export({type:"spki",format:"pem"}).toString(), trust: "Compare this public key with the separately published trusted audit key; a bundled key alone does not establish the signer." } };
}
