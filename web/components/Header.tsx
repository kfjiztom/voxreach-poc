export function Header() {
  return (
    <header className="shrink-0 border-b border-clay/60 bg-cream">
      <div className="mx-auto flex max-w-[1600px] items-center justify-between px-6 py-2">
        <div className="flex items-baseline gap-2.5">
          <span className="font-serif text-xl text-ink">헌앤패스</span>
          <span className="font-serif text-xl text-ink/80">Hearth &amp; Pass</span>
          <span className="hidden text-xs text-ink/50 md:inline">· Korean Kitchen</span>
        </div>
        <div className="flex items-center gap-3 text-[11px] uppercase tracking-widest text-ink/60">
          <span>VoxReach demo</span>
          <span className="rounded-full bg-persimmon/10 px-2.5 py-0.5 font-mono text-[10px] text-persimmonDark">
            PersonaPlex · open weights
          </span>
        </div>
      </div>
    </header>
  );
}
