export function How({ steps }: { steps: string[] }) {
  return (
    <aside className="card card-pad">
      <h3 style={{ marginBottom: 10 }}>What happens next</h3>
      <ol style={{ margin: 0, paddingLeft: 18, display: "grid", gap: 6, color: "var(--text-2)" }}>
        {steps.map((step) => <li key={step}>{step}</li>)}
      </ol>
    </aside>
  );
}
