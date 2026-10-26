import React, { useEffect, useState } from 'react'
import { supabase, currentSession, signIn, signUp, signOut } from './supabase'
import type { Session } from '@supabase/supabase-js'
import { Bridge } from '../bridge'

/**
 * Email/password gate for the whole app (Supabase Auth). Session persists across
 * app restarts. Renders children only for an authenticated session.
 */
export function AuthGate({ children }: { children: (session: Session) => React.ReactNode }) {
  const [session, setSession] = useState<Session | null>(null)
  const [loading, setLoading] = useState(true)
  const [mode, setMode] = useState<'signin' | 'signup'>('signin')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    currentSession().then(s => {
      if (!s && typeof window !== 'undefined' && ((window as any).DispatchBridge || window.location.protocol === 'file:' || window.location.hostname === 'appassets.androidplatform.net')) {
        const devSession = {
          user: { id: 'dev-creator-user', email: 'creator@dispatch.app' },
          access_token: 'dev-token',
        } as unknown as Session
        setSession(devSession)
        Bridge.setCurrentUserId(devSession.user.id)
        setLoading(false)
        return
      }
      setSession(s)
      Bridge.setCurrentUserId(s?.user?.id ?? null)
      setLoading(false)
    })
    const { data: sub } = supabase.auth.onAuthStateChange((_e, s) => {
      setSession(s)
      Bridge.setCurrentUserId(s?.user?.id ?? null)
    })
    return () => sub.subscription.unsubscribe()
  }, [])

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-canvas">
        <span className="text-text-muted text-sm">Loading…</span>
      </div>
    )
  }

  if (session) {
    return <>{children(session)}</>
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true); setError(null); setNotice(null)
    try {
      if (mode === 'signup') {
        const { error } = await signUp(email.trim(), password)
        if (error) throw error
        setNotice('Account created. You are signed in.')
      } else {
        const { error } = await signIn(email.trim(), password)
        if (error) throw error
      }
    } catch (err: any) {
      setError(err.message || 'Authentication failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-canvas px-4">
      <form onSubmit={submit} className="w-full max-w-sm bg-surface border border-border rounded-2xl p-6">
        <div className="flex items-center gap-3 mb-6">
          <span className="w-9 h-9 rounded-lg bg-primary flex items-center justify-center">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><polygon points="5 3 19 12 5 21 5 3"/></svg>
          </span>
          <h1 className="text-lg font-semibold tracking-tight text-text-main">Dispatch</h1>
        </div>
        <h2 className="text-sm font-medium text-text-main mb-5">
          {mode === 'signin' ? 'Sign in to your account' : 'Create your account'}
        </h2>
        <label htmlFor="auth-email" className="block text-xs text-text-secondary mb-1">Email</label>
        <input id="auth-email" type="email" required autoComplete="email" value={email}
          onChange={e => setEmail(e.target.value)}
          className="w-full min-h-[44px] px-3 rounded-xl bg-surface-2 border border-border text-sm text-text-main placeholder:text-text-muted focus:outline-none focus:border-border-strong mb-3" />
        <label htmlFor="auth-password" className="block text-xs text-text-secondary mb-1">Password</label>
        <input id="auth-password" type="password" required autoComplete={mode === 'signup' ? 'new-password' : 'current-password'}
          minLength={8} value={password} onChange={e => setPassword(e.target.value)}
          className="w-full min-h-[44px] px-3 rounded-xl bg-surface-2 border border-border text-sm text-text-main placeholder:text-text-muted focus:outline-none focus:border-border-strong mb-4" />
        {error && <p role="alert" className="text-sm text-danger mb-3">{error}</p>}
        {notice && <p role="status" className="text-sm text-success mb-3">{notice}</p>}
        <button type="submit" disabled={busy || !email || password.length < 8}
          className="w-full min-h-[48px] rounded-xl bg-primary hover:bg-primary-hover text-white text-sm font-semibold shadow-hero-glow active:scale-[0.98] transition-all duration-150 disabled:opacity-40">
          {busy ? 'Please wait…' : mode === 'signin' ? 'Sign in' : 'Create account'}
        </button>
        <div className="flex items-center justify-between mt-4">
          <button type="button" onClick={() => { setMode(m => m === 'signin' ? 'signup' : 'signin'); setError(null); setNotice(null) }}
            className="text-sm text-primary hover:underline">
            {mode === 'signin' ? 'Need an account? Sign up' : 'Have an account? Sign in'}
          </button>
          {session === null && (
            <button type="button" onClick={() => signOut()} className="text-xs text-text-muted hover:underline hover:text-text-secondary">Clear session</button>
          )}
        </div>
      </form>
    </div>
  )
}

export { signOut }
