export function Header() {
  return (
    <header className="border-b border-clay/60 bg-cream">
      <div className="mx-auto flex max-w-[1600px] items-center justify-between px-6 py-3">
        <div className="flex items-baseline gap-3">
          <span className="font-serif text-2xl text-ink">헌앤패스</span>
          <span className="font-serif text-2xl text-ink/80">Hearth &amp; Pass</span>
          <span className="hidden text-sm text-ink/50 md:inline">· Korean Kitchen</span>
        </div>
        <div className="flex items-center gap-4 text-xs uppercase tracking-widest text-ink/60">
          <span>VoxReach demo</span>
          <span className="rounded-full bg-persimmon/10 px-3 py-1 font-mono text-[10px] text-persimmonDark">
            MoshiRAG · open weights
          </span>
        </div>
      </div>
    </header>
  );
}
