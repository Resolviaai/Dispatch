import React, { useEffect, useState } from 'react'
import { ShieldIcon } from 'lucide-react'
import { getAuthHeaders, apiUrl } from '../bridge'

interface AdminUser {
  id: string; email: string; created_at: string;
  last_sign_in: string | null; banned: boolean;
  quota_gb?: number;
  quota_bytes?: number;
  queue_priority?: number;
}

/** Admin panel: visible only when the backend confirms the admin allowlist.
 *  All permission checks are server-side; this UI only displays state. */
export const AdminScreen: React.FC<{ pcHost?: string }> = ({ pcHost }) => {
  const [users, setUsers] = useState<AdminUser[] | null>(null)
  const [poolRemainingGb, setPoolRemainingGb] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [confirmId, setConfirmId] = useState<string | null>(null)

  const load = async () => {
    setError(null)
    try {
      const res = await fetch(apiUrl('/api/admin/users', pcHost), { headers: getAuthHeaders() })
      if (!res.ok) throw new Error(res.status === 403 ? 'Admin access required' : `Load failed (${res.status})`)
      const data = await res.json()
      setUsers(data.users)
      if (typeof data.pool_remaining_gb === 'number') {
        setPoolRemainingGb(data.pool_remaining_gb)
      }
    } catch (e: any) {
      setError(e.message || 'Failed to load users')
    }
  }
  useEffect(() => { load() }, [pcHost])

  const setBan = async (u: AdminUser, ban: boolean) => {
    setBusyId(u.id); setError(null)
    try {
      const res = await fetch(apiUrl(`/api/admin/users/${u.id}/${ban ? 'ban' : 'unban'}`, pcHost),
        { method: 'POST', headers: getAuthHeaders() })
      if (!res.ok) throw new Error(`Action failed (${res.status})`)
      await load()
    } catch (e: any) {
      setError(e.message || 'Action failed')
    } finally {
      setBusyId(null); setConfirmId(null)
    }
  }

  return (
    <div className="w-full h-full overflow-y-auto bg-studio text-text-main p-4">
      <h1 className="text-base font-semibold flex items-center gap-2 mb-1">
        <ShieldIcon size={18} className="text-primary" /> Admin
      </h1>
      <p className="text-xs text-text-muted mb-4">
        Registered accounts and status. Secrets are never shown.
        {poolRemainingGb !== null && (
          <span className="ml-2 font-mono text-primary font-medium">({poolRemainingGb}&nbsp;GB pool remaining)</span>
        )}
      </p>
      {error && <p role="alert" className="text-xs text-[#EF4444] mb-3">{error}</p>}
      {users === null && !error && <p className="text-sm text-text-muted">Loading…</p>}
      {users !== null && users.length === 0 && <p className="text-sm text-text-muted">No registered users yet.</p>}
      {users !== null && users.length > 0 && (
        <div className="space-y-2">
          {users.map(u => (
            <div key={u.id} className="bg-surface-100 border border-border rounded-xl p-3 flex items-center justify-between gap-3">
              <div className="min-w-0">
                <p className="text-sm font-medium truncate">{u.email}</p>
                <p className="text-xs text-text-muted">
                  {u.banned ? 'Disabled' : 'Active'} · joined {String(u.created_at || '').slice(0, 10)}
                  {u.last_sign_in ? ` · last seen ${String(u.last_sign_in).slice(0, 10)}` : ''}
                </p>
                <p className="text-[11px] text-text-muted font-mono mt-0.5">
                  Quota: {u.quota_gb ?? 0.5}&nbsp;GB · Priority: {u.queue_priority ?? 100}
                </p>
              </div>
              {!u.banned && confirmId === u.id ? (
                <div className="flex gap-2 shrink-0">
                  <button onClick={() => setBan(u, true)} disabled={busyId === u.id}
                    className="min-h-[44px] px-3 rounded-lg bg-[#EF4444] text-white text-xs font-semibold active:scale-[0.98] disabled:opacity-40">
                    Confirm disable
                  </button>
                  <button onClick={() => setConfirmId(null)} disabled={busyId === u.id}
                    className="min-h-[44px] px-3 rounded-lg bg-surface-200 border border-border text-xs">Cancel</button>
                </div>
              ) : (
                <button onClick={() => setConfirmId(u.id)} disabled={busyId === u.id || u.banned}
                  className="min-h-[44px] px-3 rounded-lg bg-surface-200 border border-border text-xs font-medium active:scale-[0.98] disabled:opacity-40">
                  {u.banned ? 'Disabled' : 'Disable account'}
                </button>
              )}
            </div>
          ))}
        </div>
      )}
      <button onClick={load} className="mt-4 text-xs text-primary-h hover:underline">Refresh</button>
    </div>
  )
}
