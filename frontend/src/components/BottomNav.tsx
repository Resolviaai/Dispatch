import React from 'react';
import { Camera, Film, Sparkles, Settings as SettingsIcon } from 'lucide-react';

export type TabType = 'record' | 'sessions' | 'clips' | 'settings';

interface BottomNavProps {
    currentTab: TabType;
  onTabChange: (tab: TabType) => void;
}

/**
 * Dispatch bottom navigation (Design System v3 §2.5).
 * 4 tabs — Record | Sessions | Clips | Settings. Active tab uses a dual cue:
 * accent-colored thicker-stroke icon + bold label + dot. Inactive tabs stay
 * muted with thinner stroke. 44×64 minimum targets, thin top divider, and the
 * bar clears the home-indicator safe area.
 */
export const BottomNav: React.FC<BottomNavProps> = ({ currentTab, onTabChange }) => {
  const tabs = [
    { id: 'record' as TabType, label: 'Record', icon: Camera },
    { id: 'sessions' as TabType, label: 'Sessions', icon: Film },
    { id: 'clips' as TabType, label: 'Clips', icon: Sparkles },
    { id: 'settings' as TabType, label: 'Settings', icon: SettingsIcon },
  ];

  return (
    <nav
      className="w-full bg-surface-100 border-t border-border px-2 z-30 select-none"
      style={{ paddingBottom: 'max(6px, env(safe-area-inset-bottom))' }}
      aria-label="Primary"
    >
      <div className="flex justify-around items-stretch max-w-md mx-auto pt-1">
        {tabs.map((tab) => {
          const Icon = tab.icon;
          const isActive = currentTab === tab.id;
          return (
            <button
              key={tab.id}
              onClick={() => onTabChange(tab.id)}
              aria-current={isActive ? 'page' : undefined}
              className={`flex flex-col items-center justify-center min-w-[64px] min-h-[44px] rounded-xl transition-all active:scale-[0.98] duration-150 ${
                isActive ? 'text-primary' : 'text-text-muted hover:text-text-secondary'
              }`}
            >
              <div className="relative flex items-center">
                <Icon size={20} strokeWidth={isActive ? 2.4 : 1.75} />
                {isActive && (
                  <span className="absolute -bottom-[5px] left-1/2 -translate-x-1/2 w-1 h-1 bg-primary rounded-full" />
                )}
              </div>
              <span className={`text-xs mt-1 tracking-tight ${isActive ? 'font-semibold text-text-main' : 'font-medium text-text-muted'}`}>
                {tab.label}
              </span>
            </button>
          );
        })}
      </div>
    </nav>
  );
};
