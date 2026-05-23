"use client";

import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
  info: ErrorInfo | null;
}

/**
 * Top-level error boundary so React-thrown errors render a useful message
 * with a "Reload" button instead of an empty white page.
 *
 * Catches: render errors, lifecycle errors. Does NOT catch: async errors,
 * event handler errors, errors during initial SSR — those need their own
 * try/catch.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, info: null };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error("[ErrorBoundary]", error, info);
    this.setState({ error, info });
  }

  render() {
    const { error, info } = this.state;
    if (!error) return this.props.children;

    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-cream p-6 text-ink">
        <div className="max-w-lg rounded-2xl border border-persimmon/40 bg-white p-6 shadow-sm">
          <div className="mb-2 text-[10px] uppercase tracking-widest text-persimmon">
            Page error
          </div>
          <div className="mb-3 font-serif text-2xl">Something broke on this page.</div>
          <div className="mb-4 break-words font-mono text-sm text-ink/70">
            {error.name}: {error.message}
          </div>
          {info?.componentStack ? (
            <details className="mb-4">
              <summary className="cursor-pointer text-xs uppercase tracking-widest text-ink/40">
                Component stack
              </summary>
              <pre className="mt-2 max-h-48 overflow-auto rounded bg-cream/60 p-2 text-[10px] text-ink/60">
                {info.componentStack}
              </pre>
            </details>
          ) : null}
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="rounded-full bg-moss px-5 py-2 text-xs font-semibold uppercase tracking-widest text-cream hover:bg-moss/85"
          >
            Reload
          </button>
        </div>
        <div className="text-[11px] text-ink/40">
          If this keeps happening, share the error above with the developer console.
        </div>
      </div>
    );
  }
}
