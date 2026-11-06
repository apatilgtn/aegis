"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { motion, useReducedMotion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { STAGES, duration, type Segment, type StageKey } from "@/lib/journey";

export type JourneyRow = { id: string; title: string; subtitle: string; segments: Segment[] };

const STAGE_LABEL = Object.fromEntries(STAGES.map((stage) => [stage.key, stage.label])) as Record<StageKey, string>;
const fmt = (ms: number, withTime: boolean) =>
  new Date(ms).toLocaleString("en-AU", withTime ? { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" } : { day: "numeric", month: "short" });

type Tip = { x: number; y: number; maxX: number; row: JourneyRow; segment: Segment };

export function JourneyChart({ rows, domain, now }: { rows: JourneyRow[]; domain: [number, number]; now: number }) {
  const reduce = useReducedMotion();
  const router = useRouter();
  const plotRef = useRef<HTMLDivElement>(null);
  const [tip, setTip] = useState<Tip | null>(null);
  const [start, end] = domain;
  const span = Math.max(end - start, 60_000);
  const pct = (ms: number) => `${Math.min(100, Math.max(0, ((ms - start) / span) * 100))}%`;
  const short = span <= 2 * 86_400_000;
  const ticks = Array.from({ length: 5 }, (_, index) => start + (span * index) / 4);
  const segmentEnd = (segment: Segment) => segment.stage === "live" ? fmt(segment.end, true) : segment.open ? "now" : fmt(segment.end, true);

  if (!rows.length) {
    return <div className="rows-empty" style={{ padding: "48px 0" }}><strong>No requests in this period</strong>Pick a longer range, or start a request.</div>;
  }

  return (
    <div className="journey" onMouseLeave={() => setTip(null)} onKeyDown={(event) => { if (event.key === "Escape") setTip(null); }}>
      <div className="journey-grid" ref={plotRef}>
        {rows.map((row, rowIndex) => (
          <div className="journey-row" key={row.id}>
            <Link className="journey-label" href={`/requests/${row.id}`} title={row.title}>
              <strong>{row.title}</strong>
              <span>{row.subtitle}</span>
            </Link>
            <div className="journey-track">
              {ticks.map((tick) => <span className="journey-gridline" key={tick} style={{ left: pct(tick) }} />)}
              {row.segments.map((segment, index) => {
                const visibleStart = Math.max(start, segment.start);
                const visibleEnd = Math.min(end, segment.end);
                if (visibleEnd <= visibleStart) return null;
                const left = pct(visibleStart);
                const width = `calc(${pct(visibleEnd)} - ${left})`;
                const wide = ((visibleEnd - visibleStart) / span) * 100 > 24;
                return (
                  <motion.button
                    type="button"
                    key={`${segment.stage}-${index}`}
                    className={`journey-bar stage-${segment.stage}`}
                    style={{ left, width, originX: 0 }}
                    onClick={() => router.push(`/requests/${row.id}`)}
                    initial={reduce ? false : { scaleX: 0, opacity: 0 }}
                    animate={{ scaleX: 1, opacity: 1 }}
                    transition={{ duration: 0.5, delay: rowIndex * 0.05 + index * 0.08, ease: [0.22, 1, 0.36, 1] }}
                    onMouseMove={(event) => {
                      const box = plotRef.current?.getBoundingClientRect();
                      if (box) setTip({ x: event.clientX - box.left, y: event.clientY - box.top, maxX: box.width - 250, row, segment });
                    }}
                    onFocus={(event) => {
                      const box = plotRef.current?.getBoundingClientRect();
                      const mark = event.currentTarget.getBoundingClientRect();
                      if (box) setTip({ x: mark.left - box.left + 8, y: mark.top - box.top, maxX: box.width - 250, row, segment });
                    }}
                    onBlur={() => setTip(null)}
                    aria-label={`${row.title}: ${STAGE_LABEL[segment.stage]} from ${fmt(segment.start, true)} to ${segmentEnd(segment)}`}
                  >
                    {wide && <span>{STAGE_LABEL[segment.stage]}</span>}
                  </motion.button>
                );
              })}
              {now >= start && now <= end && <span className="journey-now" style={{ left: pct(now) }} />}
            </div>
          </div>
        ))}
        <div className="journey-row journey-axis">
          <span />
          <div className="journey-track">
            {ticks.map((tick, index) => (
              <span key={tick} className="journey-tick" style={{ left: pct(tick), transform: index === 0 ? "none" : index === ticks.length - 1 ? "translateX(-100%)" : "translateX(-50%)" }}>
                {fmt(tick, short)}
              </span>
            ))}
          </div>
        </div>
        {tip && (
          <div className="journey-tip" style={{ left: Math.max(0, Math.min(tip.x + 14, tip.maxX)), top: tip.y + 14 }} role="status">
            <span className="journey-tip-stage"><i className={`journey-swatch stage-${tip.segment.stage}`} /> {STAGE_LABEL[tip.segment.stage]}</span>
            <strong>{tip.row.title}</strong>
            <span>{tip.segment.detail}</span>
            <span className="faint">
              {fmt(tip.segment.start, true)} to {segmentEnd(tip.segment)} · {duration((tip.segment.end - tip.segment.start) / 60000)}
            </span>
          </div>
        )}
      </div>
      <div className="journey-legend" aria-label="Legend">
        {STAGES.map((stage) => (
          <span key={stage.key}><i className={`journey-swatch stage-${stage.key}`} /> {stage.label}</span>
        ))}
        <span><i className="journey-now-key" /> Now</span>
      </div>
    </div>
  );
}

/** Animate changed metrics while keeping the initial server-rendered value readable. */
export function CountUp({ value, suffix = "" }: { value: number; suffix?: string }) {
  const reduce = useReducedMotion();
  const [shown, setShown] = useState(value);
  const previous = useRef(value);
  useEffect(() => {
    const from = previous.current;
    previous.current = value;
    let frame: number;
    const startedAt = performance.now();
    const step = (time: number) => {
      const progress = reduce ? 1 : Math.min(1, (time - startedAt) / 700);
      setShown(Math.round(from + (value - from) * (1 - Math.pow(1 - progress, 3))));
      if (progress < 1) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [value, reduce]);
  return <span>{shown}{suffix}</span>;
}
