import React from 'react';
import { AlertCircle, RotateCw } from 'lucide-react';

interface ScreenErrorBoundaryProps {
  children: React.ReactNode;
  title: string;
  resetKey?: string | number;
}

interface ScreenErrorBoundaryState {
  error: Error | null;
  retryCount: number;
}

export class ScreenErrorBoundary extends React.Component<ScreenErrorBoundaryProps, ScreenErrorBoundaryState> {
  state: ScreenErrorBoundaryState = { error: null, retryCount: 0 };

  static getDerivedStateFromError(error: Error): Partial<ScreenErrorBoundaryState> {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error(`[Dispatch] ${this.props.title}`, error, info.componentStack);
  }

  componentDidUpdate(previousProps: ScreenErrorBoundaryProps) {
    if (previousProps.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null });
    }
  }

  private retry = () => this.setState((state) => ({ error: null, retryCount: state.retryCount + 1 }));

  render() {
    const { error, retryCount } = this.state;
    if (!error) {
      return <React.Fragment key={retryCount}>{this.props.children}</React.Fragment>;
    }

    return (
      <main role="alert" className="h-full w-full flex flex-col items-center justify-center gap-3 bg-studio px-6 text-center text-text-main">
        <div className="flex h-11 w-11 items-center justify-center rounded-xl border border-danger/30 bg-surface-100 text-danger">
          <AlertCircle size={21} />
        </div>
        <h2 className="text-sm font-semibold">{this.props.title}</h2>
        <p className="max-w-xl break-words text-xs text-text-secondary">
          {error.message || 'An unexpected rendering error occurred.'}
        </p>
        <button
          type="button"
          onClick={this.retry}
          className="min-h-11 px-4 rounded-lg bg-primary text-white text-xs font-semibold shadow-hero-glow hover:bg-primary-hover active:scale-[0.98] transition-all flex items-center gap-2"
        >
          <RotateCw size={13} />
          <span>Retry screen</span>
        </button>
      </main>
    );
  }
}
