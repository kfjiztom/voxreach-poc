import type { RetrievalHit } from "@/lib/types";

interface RetrievalLogProps {
  hits: RetrievalHit[];
}

export function RetrievalLog({ hits }: RetrievalLogProps) {
  return (
    <div className="rounded-2xl bg-slate800/70 p-5">
      <div className="mb-3 flex items-center justify-between text-xs uppercase tracking-widest text-cream/70">
        <span className="flex items-center gap-2">
          <span>🔍</span>
          <span>Knowledge retrieved (RAG)</span>
        </span>
        <span className="font-mono text-[11px] text-cream/40">{hits.length} hits</span>
      </div>

      {hits.length === 0 ? (
        <div className="rounded-xl border border-dashed border-slate700 px-4 py-6 text-center text-xs italic text-cream/40">
          MoshiRAG will pull menu / hours / policies as Vox needs them.
        </div>
      ) : (
        <div className="scrollbar-dark max-h-48 space-y-1.5 overflow-y-auto pr-1 font-mono text-[12px]">
          {hits
            .slice()
            .reverse()
            .map((hit, idx) => (
              <div
                key={`${idx}-${hit.timestamp}`}
                className="animate-fade-in rounded-lg border border-slate700 bg-slate900/70 px-3 py-2"
              >
                <div className="flex items-center justify-between">
                  <span className="text-accentCyan">{hit.path}</span>
                  {hit.score !== null && (
                    <span className="text-cream/40">{hit.score.toFixed(2)}</span>
                  )}
                </div>
                <div className="mt-1 truncate text-cream/60">{hit.snippet}</div>
              </div>
            ))}
        </div>
      )}
    </div>
  );
}
