import React, { useRef, useState, useCallback, useEffect } from 'react';
import { RotateCw, ArrowDown } from 'lucide-react';

interface PullToRefreshProps {
  onRefresh: () => Promise<any> | void;
  children: React.ReactNode;
  className?: string;
  isRefreshing?: boolean;
  pullThreshold?: number;
}

export const PullToRefresh: React.FC<PullToRefreshProps> = ({
  onRefresh,
  children,
  className = '',
  isRefreshing = false,
  pullThreshold = 64,
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [pullDistance, setPullDistance] = useState(0);
  const [internalRefreshing, setInternalRefreshing] = useState(false);

  const startYRef = useRef(0);
  const isPullingRef = useRef(false);

  const refreshing = isRefreshing || internalRefreshing;

  const handleTouchStart = (e: React.TouchEvent<HTMLDivElement>) => {
    if (refreshing) return;
    const container = containerRef.current;
    if (!container) return;

    // Only allow pull to refresh when scrolled to the very top
    if (container.scrollTop <= 0) {
      startYRef.current = e.touches[0].clientY;
      isPullingRef.current = true;
    }
  };

  const handleTouchMove = (e: React.TouchEvent<HTMLDivElement>) => {
    if (!isPullingRef.current || refreshing) return;
    const container = containerRef.current;
    if (!container) return;

    if (container.scrollTop > 0) {
      isPullingRef.current = false;
      setPullDistance(0);
      return;
    }

    const currentY = e.touches[0].clientY;
    const diff = currentY - startYRef.current;

    if (diff > 0) {
      // Apply rubber-band dampening: sqrt / log scaling
      const dampening = 0.45;
      const calculated = Math.min(diff * dampening, 110);
      setPullDistance(calculated);
    } else {
      setPullDistance(0);
    }
  };

  const handleTouchEnd = async () => {
    if (!isPullingRef.current) return;
    isPullingRef.current = false;

    if (pullDistance >= pullThreshold && !refreshing) {
      setInternalRefreshing(true);
      setPullDistance(pullThreshold); // keep indicator visible while refreshing
      try {
        await Promise.resolve(onRefresh());
      } finally {
        setInternalRefreshing(false);
        setPullDistance(0);
      }
    } else {
      setPullDistance(0);
    }
  };

  // Keyboard / manual reset when refreshing props finishes
  useEffect(() => {
    if (!isRefreshing && pullDistance > 0 && !isPullingRef.current) {
      setPullDistance(0);
    }
  }, [isRefreshing, pullDistance]);

  const progress = Math.min(pullDistance / pullThreshold, 1);
  const isReady = pullDistance >= pullThreshold;

  return (
    <div
      ref={containerRef}
      onTouchStart={handleTouchStart}
      onTouchMove={handleTouchMove}
      onTouchEnd={handleTouchEnd}
      className={`relative overflow-y-auto overscroll-contain touch-pan-y ${className}`}
    >
      {/* Pull-to-refresh Visual Indicator Bubble */}
      <div
        className="absolute left-0 right-0 top-0 flex items-center justify-center pointer-events-none transition-transform duration-150 z-30"
        style={{
          transform: `translateY(${refreshing ? 16 : Math.max(pullDistance - 36, -40)}px)`,
          opacity: refreshing || pullDistance > 10 ? 1 : 0,
        }}
      >
        <div className="w-9 h-9 rounded-full bg-surface-2 border border-border shadow-md flex items-center justify-center text-primary backdrop-blur-md">
          {refreshing ? (
            <RotateCw size={16} className="animate-spin text-primary" />
          ) : isReady ? (
            <RotateCw size={16} className="text-primary rotate-180 transition-transform duration-200" />
          ) : (
            <ArrowDown
              size={15}
              className="text-text-muted transition-transform duration-150"
              style={{ transform: `rotate(${progress * 180}deg)` }}
            />
          )}
        </div>
      </div>

      {/* Main Content with subtle rubberband offset */}
      <div
        style={{
          transform: pullDistance > 0 ? `translateY(${pullDistance * 0.35}px)` : 'none',
          transition: isPullingRef.current ? 'none' : 'transform 200ms ease-out',
        }}
        className="w-full h-full min-h-full"
      >
        {children}
      </div>
    </div>
  );
};
