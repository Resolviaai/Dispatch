import React from 'react';
import { RefreshCw } from 'lucide-react';

export type ClipLibraryLoadState = 'loading' | 'loaded' | 'error';

interface ClipLibraryNoticeProps {
  state: ClipLibraryLoadState;
  error?: string | null;
  onRetry: () => void;
}

export const ClipLibraryNotice: React.FC<ClipLibraryNoticeProps> = ({ state, error, onRetry }) => {
  if (state === 'loading') {
    return <div className="p-6 text-center text-text-muted" role="status">Loading clips…</div>;
  }

  if (state === 'error') {
    return (
      <div className="mx-auto max-w-md p-6 text-center" role="alert">
        <h2 className="text-[14px] font-semibold text-text-main">Couldn’t load clips</h2>
        <p className="mt-2 text-[12px] text-text-muted">{error || 'Check the PC connection and try again.'}</p>
        <button
          type="button"
          onClick={onRetry}
          className="mt-4 inline-flex min-h-[44px] items-center justify-center gap-2 rounded-lg border border-border bg-surface-200 px-4 text-[12px] font-medium text-text-main hover:bg-surface-300 active:scale-[0.98]"
        >
          <RefreshCw size={14} />
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="p-6 text-center" role="status">
      <h2 className="text-[14px] font-semibold text-text-main">No clips available</h2>
      <p className="mt-2 text-[12px] text-text-muted">Clips will appear here when they are ready for review.</p>
    </div>
  );
};
