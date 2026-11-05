import Image from "next/image";

export default function Loading() {
  return <main className="route-loading"><div><Image src="/brand/aegis-mark.svg" width={48} height={48} alt="" /><strong>Loading your control plane</strong><span>Connecting identity, catalog and workflows…</span></div></main>;
}
