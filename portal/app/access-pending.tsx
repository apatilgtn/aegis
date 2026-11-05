import { Clock3, LockKeyhole } from "lucide-react";
import Image from "next/image";

export function AccessPending({ displayName, email }: { displayName: string; email: string }) {
  return (
    <main className="auth-shell">
      <section className="pending-card">
        <span className="auth-mark"><Image src="/brand/aegis-mark.svg" width={31} height={31} alt="Aegis" priority /></span>
        <div className="eyebrow"><span className="pulse" /> Identity verified</div>
        <h1>Welcome, {displayName}.</h1>
        <p>Your account is authenticated, but it has not been assigned to an Aegis workspace yet.</p>
        <div className="identity-row"><LockKeyhole size={17} /><div><strong>{email}</strong><span>Verified Supabase identity</span></div></div>
        <div className="pending-note"><Clock3 size={16} /> A platform administrator must grant a tenant role before access is enabled.</div>
        <form action="/auth/signout" method="post"><button className="secondary-button full" type="submit">Sign out</button></form>
      </section>
    </main>
  );
}
