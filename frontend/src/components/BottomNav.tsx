import React from 'react';
import { Camera, Film, Sparkles, Settings as SettingsIcon } from 'lucide-react';

export type TabType = 'record' | 'sessions' | 'clips' | 'settings';

interface BottomNavProps {
  currentTab: TabType;
  onTabChange: (tab: TabType) => void;
}

export const BottomNav: React.FC<BottomNavProps> = ({ currentTab, onTabChange }) => {
  const tabs = [
    { id: 'record' as TabType, label: 'Record', icon: Camera },
    { id: 'sessions' as TabType, label: 'Sessions', icon: Film },
    { id: 'clips' as TabType, label: 'Clips', icon: Sparkles },
    { id: 'settings' as TabType, label: 'Settings', icon: SettingsIcon },
  ];

  return (
    <nav className="w-full bg-surface-100 border-t border-border px-3 py-1.5 z-30 select-none">
      <div className="flex justify-around items-center max-w-md mx-auto">
        {tabs.map((tab) => {
          const Icon = tab.icon;
          const isActive = currentTab === tab.id;
          return (
            <button
              key={tab.id}
              onClick={() => onTabChange(tab.id)}
              className={`flex flex-col items-center justify-center py-1 px-1.5 sm:px-3 min-w-[50px] sm:min-w-[64px] min-h-[44px] rounded-lg transition-all active:scale-[0.98] duration-150 ${
                isActive ? 'text-primary' : 'text-text-muted hover:text-text-secondary'
              }`}
            >
              <div className="relative">
                <Icon size={19} strokeWidth={isActive ? 2.2 : 1.75} />
                {isActive && (
                  <span className="absolute -bottom-1.5 left-1/2 -translate-x-1/2 w-1.5 h-1.5 bg-primary rounded-full shadow-[0_0_8px_#2563EB]" />
                )}
              </div>
              <span className={`text-[10px] sm:text-[11px] font-sans mt-0.5 tracking-tight ${isActive ? 'font-semibold text-text-main' : 'font-normal text-text-muted'}`}>
                {tab.label}
              </span>
            </button>
          );
        })}
      </div>
    </nav>
  );
};
