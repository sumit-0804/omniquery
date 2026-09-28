import { Component, type ReactNode } from "react";

/** Keeps a rendering bug in one answer from blanking the whole app. */
export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div role="alert" className="mx-auto max-w-[880px] pt-[22px] text-[13px] text-ink-3">
        This answer could not be shown: <span className="font-mono text-error">{this.state.error.message}</span>. Ask
        again, or reload the page.
      </div>
    );
  }
}
