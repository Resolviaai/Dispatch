import React, { useEffect, useState, useMemo } from 'react';
import {
  Shield,
  Users,
  HardDrive,
  RefreshCw,
  Search,
  CheckCircle2,
  AlertTriangle,
  ArrowUp,
  ArrowDown,
  Layers,
  Database,
  Cloud,
  Laptop
} from 'lucide-react';
import { getAuthHeaders, apiUrl } from '../bridge';
import { WORKER_BASE } from '../accounts/accountsService';
import { currentSession } from '../auth/supabase';

interface AdminUser {
  id: string;
  email: string;
  created_at: string;
  last_sign_in: string | null;
  banned: boolean;
  used_bytes?: number;
  used_gb?: number;
  quota_gb?: number;
  quota_bytes?: number;
  queue_priority?: number;
  connected_providers?: string[];
}

interface AdminOverview {
  user_counts?: {
    total?: number;
    active?: number;
    disabled?: number;
  };
  storage?: {
    actual_stored_bytes?: number;
    actual_stored_gb?: number;
    pending_reserved_bytes?: number;
    pending_reserved_gb?: number;
    allocated_quota_bytes?: number;
    allocated_quota_gb?: number;
    unallocated_quota_bytes?: number;
    unallocated_quota_gb?: number;
    global_budget_gb?: number;
  };
  jobs?: {
    total?: number;
    queued?: number;
    running?: number;
    done?: number;
    failed?: number;
  };
}

export const AdminScreen: React.FC<{ pcHost?: string }> = ({ pcHost }) => {
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [overview, setOverview] = useState<AdminOverview | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<{ message: string; isError?: boolean } | null>(null);

  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<'all' | 'active' | 'disabled'>('all');

  const [busyUserId, setBusyUserId] = useState<string | null>(null);
  const [confirmDisableUser, setConfirmDisableUser] = useState<AdminUser | null>(null);
  const [customQuotaInputs, setCustomQuotaInputs] = useState<Record<string, string>>({});
  const [customPriorityInputs, setCustomPriorityInputs] = useState<Record<string, string>>({});
  const [connectionMode, setConnectionMode] = useState<'cloud' | 'lan'>('cloud');

  const showToast = (message: string, isError = false) => {
    setFeedback({ message, isError });
    setTimeout(() => setFeedback(null), 5000);
  };

  const getActiveAuthHeaders = async (): Promise<{ headers: Record<string, string>; base: string; isCloud: boolean }> => {
    // If LAN pcHost is specified, check if it is reachable
    if (pcHost && pcHost.trim()) {
      try {
        const lanCheck = await fetch(apiUrl('/api/admin/whoami', pcHost), {
          headers: getAuthHeaders(),
          signal: AbortSignal.timeout(2000),
        });
        if (lanCheck.ok) {
          setConnectionMode('lan');
          return {
            headers: getAuthHeaders(),
            base: apiUrl('/api', pcHost),
            isCloud: false,
          };
        }
      } catch {
        // Fall back to Cloud Worker
      }
    }

    // Default to Cloudflare Worker with Supabase JWT
    setConnectionMode('cloud');
    const sess = await currentSession();
    const token = sess?.access_token || '';
    return {
      headers: {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      base: WORKER_BASE,
      isCloud: true,
    };
  };

  const loadData = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const { headers, base, isCloud } = await getActiveAuthHeaders();
      const url = isCloud ? `${base}/admin/users` : `${base}/admin/users`;
      const res = await fetch(url, { headers });

      if (res.status === 401 || res.status === 403) {
        throw new Error('Admin authorization required. Please sign in with an authorized administrator account.');
      }
      if (!res.ok) {
        throw new Error(`Failed to load operations data (HTTP ${res.status})`);
      }

      const data = await res.json();
      const loadedUsers: AdminUser[] = Array.isArray(data.users) ? data.users : [];
      setUsers(loadedUsers);

      // Pre-fill inputs
      const initialQuotas: Record<string, string> = {};
      const initialPriorities: Record<string, string> = {};
      loadedUsers.forEach(u => {
        initialQuotas[u.id] = (u.quota_gb ?? 2.0).toFixed(1);
        initialPriorities[u.id] = String(u.queue_priority ?? 100);
      });
      setCustomQuotaInputs(initialQuotas);
      setCustomPriorityInputs(initialPriorities);

      if (data.overview) {
        setOverview(data.overview);
      } else {
        // Synthesize overview if coming from LAN python API
        const activeCount = loadedUsers.filter(u => !u.banned).length;
        const disabledCount = loadedUsers.filter(u => u.banned).length;
        const totalAllocatedGb = loadedUsers.reduce((sum, u) => sum + (u.quota_gb || 0), 0);
        const unallocatedGb = Math.max(0, 10.0 - totalAllocatedGb);

        setOverview({
          user_counts: {
            total: loadedUsers.length,
            active: activeCount,
            disabled: disabledCount,
          },
          storage: {
            allocated_quota_gb: totalAllocatedGb,
            unallocated_quota_gb: data.pool_remaining_gb ?? unallocatedGb,
            global_budget_gb: 10.0,
            actual_stored_gb: 0,
            pending_reserved_gb: 0,
          },
          jobs: { total: 0, queued: 0, running: 0, done: 0, failed: 0 },
        });
      }
    } catch (e: any) {
      setError(e.message || 'Error loading admin data');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, [pcHost]);

  const handleSetBan = async (user: AdminUser, ban: boolean) => {
    setBusyUserId(user.id);
    try {
      const { headers, base, isCloud } = await getActiveAuthHeaders();
      const url = isCloud
        ? `${base}/admin/users/${user.id}/${ban ? 'ban' : 'unban'}`
        : `${base}/admin/users/${user.id}/${ban ? 'ban' : 'unban'}`;

      const res = await fetch(url, { method: 'POST', headers });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error || `Action failed (HTTP ${res.status})`);
      }

      showToast(`User ${user.email || user.id} ${ban ? 'disabled' : 'enabled'} successfully.`);
      await loadData();
    } catch (e: any) {
      showToast(e.message || 'Status update failed', true);
    } finally {
      setBusyUserId(null);
      setConfirmDisableUser(null);
    }
  };

  const handleUpdateQuota = async (userId: string, quotaGb: number) => {
    if (!Number.isFinite(quotaGb) || quotaGb <= 0 || quotaGb > 10) {
      showToast('Quota must be between 0.1 and 10.0 GB (decimal)', true);
      return;
    }
    setBusyUserId(userId);
    try {
      const { headers, base, isCloud } = await getActiveAuthHeaders();
      const url = isCloud
        ? `${base}/admin/users/${userId}/quota`
        : `${base}/admin/users/${userId}/quota`;

      const res = await fetch(url, {
        method: 'POST',
        headers,
        body: JSON.stringify({ quota_gb: quotaGb }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.error || data.detail || `Quota update failed (${res.status})`);
      }

      showToast(`Storage quota updated to ${quotaGb}&nbsp;GB.`);
      await loadData();
    } catch (e: any) {
      showToast(e.message || 'Failed to update quota', true);
    } finally {
      setBusyUserId(null);
    }
  };

  const handleUpdatePriority = async (userId: string, priority: number) => {
    if (!Number.isInteger(priority) || priority < 1 || priority > 1000) {
      showToast('Priority must be an integer between 1 and 1000 (lower runs first)', true);
      return;
    }
    setBusyUserId(userId);
    try {
      const { headers, base, isCloud } = await getActiveAuthHeaders();
      const url = isCloud
        ? `${base}/admin/users/${userId}/priority`
        : `${base}/admin/users/${userId}/priority`;

      const res = await fetch(url, {
        method: 'POST',
        headers,
        body: JSON.stringify({ priority }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.error || data.detail || `Priority update failed (${res.status})`);
      }

      showToast(`Queue priority updated to ${priority}.`);
      await loadData();
    } catch (e: any) {
      showToast(e.message || 'Failed to update priority', true);
    } finally {
      setBusyUserId(null);
    }
  };

  const filteredUsers = useMemo(() => {
    if (!users) return [];
    const q = searchQuery.trim().toLowerCase();
    return users.filter(u => {
      const matchQuery = !q || (u.email && u.email.toLowerCase().includes(q)) || u.id.toLowerCase().includes(q);
      const matchStatus = statusFilter === 'all' || (statusFilter === 'active' && !u.banned) || (statusFilter === 'disabled' && u.banned);
      return matchQuery && matchStatus;
    });
  }, [users, searchQuery, statusFilter]);

  return (
    <div className="w-full h-full overflow-y-auto bg-studio text-text-main pb-24 select-none">
      {/* SCREEN HEADER */}
      <div className="sticky top-0 z-20 px-4 py-3 border-b border-border bg-studio/95 backdrop-blur flex items-center justify-between gap-3">
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="w-8 h-8 rounded-xl bg-primary/10 border border-primary/20 flex items-center justify-center shrink-0">
            <Shield size={17} className="text-primary" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="text-base font-semibold tracking-tight text-text-main truncate">Operations Console</h1>
              <span className="px-1.5 py-0.5 rounded text-[10px] font-bold tracking-wider uppercase bg-primary text-white">Admin</span>
            </div>
            <p className="text-xs text-text-muted flex items-center gap-1.5 truncate">
              {connectionMode === 'cloud' ? (
                <>
                  <Cloud size={11} className="text-primary" />
                  <span>Cloud Controller</span>
                </>
              ) : (
                <>
                  <Laptop size={11} className="text-emerald-400" />
                  <span>Studio LAN (Direct)</span>
                </>
              )}
              <span>· Zero Secrets Expose Law</span>
            </p>
          </div>
        </div>

        <button
          onClick={loadData}
          disabled={isLoading}
          className="min-h-[44px] px-3 rounded-xl bg-surface-100 hover:bg-surface-200 border border-border text-xs font-medium text-text-main flex items-center gap-1.5 active:scale-[0.98] transition-all disabled:opacity-40"
          title="Refresh Operations Data"
        >
          <RefreshCw size={13} className={isLoading ? 'animate-spin text-primary' : ''} />
          <span className="hidden sm:inline">Refresh</span>
        </button>
      </div>

      <div className="p-4 space-y-4 max-w-5xl mx-auto">
        {/* FEEDBACK TOAST */}
        {feedback && (
          <div
            role="alert"
            className={`p-3 rounded-xl border text-xs font-medium flex items-center gap-2 ${
              feedback.isError
                ? 'bg-danger/10 border-danger/30 text-danger'
                : 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
            }`}
          >
            {feedback.isError ? <AlertTriangle size={15} /> : <CheckCircle2 size={15} />}
            <span dangerouslySetInnerHTML={{ __html: feedback.message }} />
          </div>
        )}

        {/* ERROR STATE */}
        {error && (
          <div className="p-4 rounded-xl bg-danger/10 border border-danger/30 space-y-2">
            <div className="flex items-center gap-2 text-sm font-semibold text-danger">
              <AlertTriangle size={16} />
              <span>Authorization Notice</span>
            </div>
            <p className="text-xs text-text-muted">{error}</p>
            <button
              onClick={loadData}
              className="mt-2 min-h-[44px] px-3 rounded-lg bg-surface-200 border border-border text-xs font-medium text-text-main active:scale-[0.98]"
            >
              Retry Connection
            </button>
          </div>
        )}

        {/* OVERVIEW METRICS ROW */}
        {overview && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2.5">
            {/* Total Users */}
            <div className="p-3.5 rounded-xl bg-surface-100 border border-border space-y-1.5">
              <div className="flex items-center justify-between text-xs font-semibold text-text-secondary uppercase tracking-wider">
                <span className="flex items-center gap-1.5"><Users size={13} className="text-primary" /> Users</span>
                <span className="px-1.5 py-0.5 rounded-full text-[10px] bg-emerald-500/10 text-emerald-400 font-mono">
                  {overview.user_counts?.active ?? 0}&nbsp;active
                </span>
              </div>
              <p className="text-xl font-bold font-mono tabular-nums text-text-main">
                {overview.user_counts?.total ?? 0}
              </p>
              <p className="text-[11px] text-text-muted font-mono tabular-nums">
                {overview.user_counts?.disabled ?? 0}&nbsp;disabled accounts
              </p>
            </div>

            {/* Actual Stored R2 */}
            <div className="p-3.5 rounded-xl bg-surface-100 border border-border space-y-1.5">
              <div className="flex items-center justify-between text-xs font-semibold text-text-secondary uppercase tracking-wider">
                <span className="flex items-center gap-1.5"><HardDrive size={13} className="text-emerald-400" /> Stored Media</span>
              </div>
              <p className="text-xl font-bold font-mono tabular-nums text-text-main">
                {(overview.storage?.actual_stored_gb ?? 0).toFixed(2)}&nbsp;GB
              </p>
              <p className="text-[11px] text-text-muted font-mono tabular-nums">
                {(overview.storage?.pending_reserved_gb ?? 0).toFixed(2)}&nbsp;GB reserved
              </p>
            </div>

            {/* Allocated vs Unallocated Quota */}
            <div className="p-3.5 rounded-xl bg-surface-100 border border-border space-y-1.5">
              <div className="flex items-center justify-between text-xs font-semibold text-text-secondary uppercase tracking-wider">
                <span className="flex items-center gap-1.5"><Database size={13} className="text-amber-400" /> Quota Pool</span>
              </div>
              <p className="text-xl font-bold font-mono tabular-nums text-text-main">
                {(overview.storage?.allocated_quota_gb ?? 0).toFixed(1)}&nbsp;GB
              </p>
              <p className="text-[11px] text-text-muted font-mono tabular-nums">
                {(overview.storage?.unallocated_quota_gb ?? 0).toFixed(1)}&nbsp;GB pool left (of 10&nbsp;GB)
              </p>
            </div>

            {/* Processing Jobs */}
            <div className="p-3.5 rounded-xl bg-surface-100 border border-border space-y-1.5">
              <div className="flex items-center justify-between text-xs font-semibold text-text-secondary uppercase tracking-wider">
                <span className="flex items-center gap-1.5"><Layers size={13} className="text-primary" /> Job Queue</span>
                <span className="px-1.5 py-0.5 rounded-full text-[10px] bg-primary/10 text-primary font-mono">
                  {overview.jobs?.queued ?? 0}&nbsp;queued
                </span>
              </div>
              <p className="text-xl font-bold font-mono tabular-nums text-text-main">
                {overview.jobs?.total ?? 0}
              </p>
              <p className="text-[11px] text-text-muted font-mono tabular-nums">
                {overview.jobs?.running ?? 0}&nbsp;active · {overview.jobs?.failed ?? 0}&nbsp;failed
              </p>
            </div>
          </div>
        )}

        {/* SEARCH AND FILTER BAR */}
        <div className="p-3 rounded-xl bg-surface-100 border border-border flex flex-col sm:flex-row gap-2.5">
          <div className="relative flex-1">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
            <input
              type="text"
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              placeholder="Search by user email or ID…"
              className="w-full min-h-[44px] pl-9 pr-3 rounded-lg bg-surface-200 border border-border text-xs text-text-main placeholder:text-text-muted outline-none focus:border-primary"
            />
          </div>
          <select
            value={statusFilter}
            onChange={e => setStatusFilter(e.target.value as any)}
            className="min-h-[44px] px-3 rounded-lg bg-surface-200 border border-border text-xs font-medium text-text-main outline-none focus:border-primary shrink-0"
          >
            <option value="all">All Accounts ({users?.length ?? 0})</option>
            <option value="active">Active Accounts Only</option>
            <option value="disabled">Disabled Accounts Only</option>
          </select>
        </div>

        {/* USERS LIST */}
        {users === null && !error && (
          <div className="p-8 text-center text-xs text-text-muted space-y-2">
            <RefreshCw size={20} className="mx-auto animate-spin text-primary" />
            <p>Loading authoritative user data…</p>
          </div>
        )}

        {users !== null && filteredUsers.length === 0 && (
          <div className="p-8 text-center rounded-xl bg-surface-100 border border-border text-xs text-text-muted">
            No accounts match the current filter criteria.
          </div>
        )}

        {users !== null && filteredUsers.length > 0 && (
          <div className="space-y-3">
            {filteredUsers.map(user => {
              const usedGb = user.used_gb ?? (user.used_bytes ? user.used_bytes / 1_000_000_000 : 0);
              const quotaGb = user.quota_gb ?? 2.0;
              const pct = Math.min(100, Math.round((usedGb / Math.max(0.1, quotaGb)) * 100));
              const prio = user.queue_priority ?? 100;
              const provs = Array.isArray(user.connected_providers) ? user.connected_providers : [];
              const isBusy = busyUserId === user.id;

              return (
                <div
                  key={user.id}
                  className="p-4 rounded-xl bg-surface-100 border border-border space-y-3 transition-colors"
                >
                  {/* Row Top: Identity & Status */}
                  <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-border pb-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-semibold text-sm text-text-main truncate">{user.email || 'Anonymous User'}</span>
                        <span
                          className={`px-2 py-0.5 rounded-full text-[10px] font-semibold tracking-wide uppercase ${
                            user.banned
                              ? 'bg-danger/10 border border-danger/30 text-danger'
                              : 'bg-emerald-500/10 border border-emerald-500/30 text-emerald-400'
                          }`}
                        >
                          {user.banned ? 'Disabled' : 'Active'}
                        </span>
                      </div>
                      <p className="text-[11px] text-text-muted font-mono mt-0.5">
                        ID: {user.id.slice(0, 12)}… · Joined {String(user.created_at || '').slice(0, 10)}
                        {user.last_sign_in ? ` · Last seen ${String(user.last_sign_in).slice(0, 10)}` : ''}
                      </p>
                    </div>

                    {/* Disable / Enable Button */}
                    <div className="shrink-0">
                      {user.banned ? (
                        <button
                          onClick={() => handleSetBan(user, false)}
                          disabled={isBusy}
                          className="min-h-[44px] px-3.5 rounded-lg bg-emerald-500/10 hover:bg-emerald-500/20 border border-emerald-500/30 text-xs font-semibold text-emerald-400 active:scale-[0.98] transition-all disabled:opacity-40"
                        >
                          Enable Account
                        </button>
                      ) : (
                        <button
                          onClick={() => setConfirmDisableUser(user)}
                          disabled={isBusy}
                          className="min-h-[44px] px-3.5 rounded-lg bg-surface-200 hover:bg-danger/15 hover:text-danger hover:border-danger/30 border border-border text-xs font-medium text-text-secondary active:scale-[0.98] transition-all disabled:opacity-40"
                        >
                          Disable Account
                        </button>
                      )}
                    </div>
                  </div>

                  {/* Connected Providers Badges */}
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span className="text-[11px] font-semibold uppercase tracking-wider text-text-muted mr-1">
                      Destinations:
                    </span>
                    {provs.length > 0 ? (
                      provs.map((prov, i) => (
                        <span
                          key={i}
                          className="px-2 py-0.5 rounded bg-surface-200 border border-border text-[11px] font-medium text-text-main capitalize"
                        >
                          {prov === 'twitter' ? 'X (Twitter)' : prov}
                        </span>
                      ))
                    ) : (
                      <span className="text-[11px] text-text-muted italic">No external destinations connected</span>
                    )}
                  </div>

                  {/* Row Controls: Quota & Priority */}
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3 pt-1">
                    {/* Storage Quota Control */}
                    <div className="p-3 rounded-lg bg-surface-200 border border-border space-y-2">
                      <div className="flex items-center justify-between text-xs">
                        <span className="font-medium text-text-secondary">Storage Quota</span>
                        <span className="font-mono tabular-nums text-text-muted text-[11px]">
                          {usedGb.toFixed(2)}&nbsp;/&nbsp;{quotaGb.toFixed(1)}&nbsp;GB ({pct}%)
                        </span>
                      </div>

                      {/* Storage Progress Bar */}
                      <div className="w-full h-1.5 rounded-full bg-surface-300 overflow-hidden">
                        <div
                          className={`h-full transition-all duration-300 ${
                            pct > 90 ? 'bg-danger' : pct > 75 ? 'bg-amber-400' : 'bg-primary'
                          }`}
                          style={{ width: `${pct}%` }}
                        />
                      </div>

                      <div className="flex items-center gap-1.5 pt-0.5">
                        {[1.0, 2.0, 5.0].map(val => (
                          <button
                            key={val}
                            onClick={() => {
                              setCustomQuotaInputs(prev => ({ ...prev, [user.id]: String(val) }));
                              handleUpdateQuota(user.id, val);
                            }}
                            disabled={isBusy}
                            className="min-h-[36px] px-2 rounded-md bg-surface-100 hover:bg-surface-300 border border-border text-[11px] font-mono text-text-secondary active:scale-[0.98] transition-all disabled:opacity-40"
                          >
                            {val}G
                          </button>
                        ))}
                        <input
                          type="number"
                          step="0.1"
                          min="0.1"
                          max="10.0"
                          value={customQuotaInputs[user.id] ?? quotaGb.toFixed(1)}
                          onChange={e => {
                            const val = e.target.value;
                            setCustomQuotaInputs(prev => ({ ...prev, [user.id]: val }));
                          }}
                          disabled={isBusy}
                          className="w-16 min-h-[36px] px-2 rounded-md bg-surface-100 border border-border text-xs font-mono text-right text-text-main outline-none focus:border-primary"
                        />
                        <button
                          onClick={() => {
                            const parsed = parseFloat(customQuotaInputs[user.id] || '');
                            if (!isNaN(parsed)) handleUpdateQuota(user.id, parsed);
                          }}
                          disabled={isBusy}
                          className="min-h-[36px] px-2.5 rounded-md bg-primary hover:bg-primary-hover text-white text-xs font-medium active:scale-[0.98] transition-all disabled:opacity-40"
                        >
                          Set
                        </button>
                      </div>
                    </div>

                    {/* Processing Priority Control */}
                    <div className="p-3 rounded-lg bg-surface-200 border border-border space-y-2">
                      <div className="flex items-center justify-between text-xs">
                        <span className="font-medium text-text-secondary">Queue Priority</span>
                        <span className="font-mono tabular-nums text-text-muted text-[11px]">
                          Rank #{prio} (1–1000, lower runs first)
                        </span>
                      </div>

                      <div className="flex items-center gap-1.5 pt-1.5">
                        <button
                          onClick={() => {
                            const current = parseInt(customPriorityInputs[user.id] || String(prio), 10);
                            const updated = Math.max(1, current - 10);
                            setCustomPriorityInputs(prev => ({ ...prev, [user.id]: String(updated) }));
                            handleUpdatePriority(user.id, updated);
                          }}
                          disabled={isBusy}
                          title="Increase Priority (Run Earlier)"
                          className="min-h-[36px] px-2.5 rounded-md bg-surface-100 hover:bg-surface-300 border border-border text-xs font-mono text-text-secondary flex items-center gap-1 active:scale-[0.98] transition-all disabled:opacity-40"
                        >
                          <ArrowUp size={12} className="text-emerald-400" />
                          <span>-10</span>
                        </button>

                        <input
                          type="number"
                          step="1"
                          min="1"
                          max="1000"
                          value={customPriorityInputs[user.id] ?? String(prio)}
                          onChange={e => {
                            const val = e.target.value;
                            setCustomPriorityInputs(prev => ({ ...prev, [user.id]: val }));
                          }}
                          disabled={isBusy}
                          className="w-16 min-h-[36px] px-2 rounded-md bg-surface-100 border border-border text-xs font-mono text-right text-text-main outline-none focus:border-primary"
                        />

                        <button
                          onClick={() => {
                            const current = parseInt(customPriorityInputs[user.id] || String(prio), 10);
                            const updated = Math.min(1000, current + 10);
                            setCustomPriorityInputs(prev => ({ ...prev, [user.id]: String(updated) }));
                            handleUpdatePriority(user.id, updated);
                          }}
                          disabled={isBusy}
                          title="Decrease Priority (Run Later)"
                          className="min-h-[36px] px-2.5 rounded-md bg-surface-100 hover:bg-surface-300 border border-border text-xs font-mono text-text-secondary flex items-center gap-1 active:scale-[0.98] transition-all disabled:opacity-40"
                        >
                          <ArrowDown size={12} className="text-amber-400" />
                          <span>+10</span>
                        </button>

                        <button
                          onClick={() => {
                            const parsed = parseInt(customPriorityInputs[user.id] || '', 10);
                            if (!isNaN(parsed)) handleUpdatePriority(user.id, parsed);
                          }}
                          disabled={isBusy}
                          className="min-h-[36px] px-2.5 rounded-md bg-surface-100 hover:bg-surface-300 border border-border text-text-main text-xs font-medium active:scale-[0.98] transition-all disabled:opacity-40"
                        >
                          Save
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* CONFIRM DISABLE MODAL */}
      {confirmDisableUser && (
        <div className="fixed inset-0 z-50 bg-black/75 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="w-full max-w-sm rounded-2xl bg-surface-100 border border-border p-5 space-y-4 shadow-xl">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl bg-danger/10 border border-danger/30 flex items-center justify-center text-danger shrink-0">
                <AlertTriangle size={20} />
              </div>
              <div>
                <h3 className="text-sm font-semibold text-text-main">Disable User Account?</h3>
                <p className="text-xs text-text-muted mt-0.5">{confirmDisableUser.email || confirmDisableUser.id}</p>
              </div>
            </div>

            <p className="text-xs text-text-secondary leading-relaxed bg-surface-200 p-3 rounded-xl border border-border">
              The user will be immediately blocked from signing in or submitting new processing jobs. All existing media, clips, and R2 storage remain completely preserved and intact. This action is fully reversible.
            </p>

            <div className="flex items-center justify-end gap-2.5 pt-1">
              <button
                onClick={() => setConfirmDisableUser(null)}
                disabled={busyUserId === confirmDisableUser.id}
                className="min-h-[44px] px-4 rounded-xl bg-surface-200 hover:bg-surface-300 border border-border text-xs font-medium text-text-main active:scale-[0.98] transition-all"
              >
                Cancel
              </button>
              <button
                onClick={() => handleSetBan(confirmDisableUser, true)}
                disabled={busyUserId === confirmDisableUser.id}
                className="min-h-[44px] px-4 rounded-xl bg-danger hover:bg-danger/90 text-white text-xs font-semibold active:scale-[0.98] transition-all shadow-md shadow-danger/20"
              >
                Confirm Disable
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
