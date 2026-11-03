import React, { useEffect, useState } from 'react'
import { Eye, EyeOff, ArrowLeft } from 'lucide-react'
import { supabase, currentSession, signIn, signUp, signOut, resetPassword, updatePassword } from './supabase'
import type { Session } from '@supabase/supabase-js'
import { Bridge } from '../bridge'
import { MorphingSquare } from '@/components/ui/morphing-square'

/**
 * Email/password gate for the whole app (Supabase Auth). Session persists across
 * app restarts. Handles Sign in, Sign up, Forgot Password, and Reset Password.
 */
export function AuthGate({ children }: { children: (session: Session) => React.ReactNode }) {
  const [session, setSession] = useState<Session | null>(null)
  const [loading, setLoading] = useState(true)
  const [mode, setMode] = useState<'signin' | 'signup' | 'forgot' | 'reset'>('signin')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [showConfirmPassword, setShowConfirmPassword] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    // Detect recovery token from email redirect
    if (typeof window !== 'undefined') {
      const hash = window.location.hash || ''
      if (hash.includes('type=recovery') || hash.includes('access_token=')) {
        setMode('reset')
      }
    }

    currentSession().then(s => {
      const env = ((import.meta as any).env || {}) as Record<string, unknown>
      const allowDevAuth = Boolean(env.DEV) && env.VITE_ALLOW_DEV_AUTH === '1'
      if (!s && allowDevAuth) {
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

    const { data: sub } = supabase.auth.onAuthStateChange((event, s) => {
      if (event === 'PASSWORD_RECOVERY') {
        setMode('reset')
      }
      setSession(s)
      Bridge.setCurrentUserId(s?.user?.id ?? null)
    })

    return () => sub.subscription.unsubscribe()
  }, [])

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-canvas">
        <MorphingSquare
          className="bg-primary shadow-hero-glow"
          message="Loading Dispatch…"
        />
      </div>
    )
  }

  // Only render app children if authenticated and not in an active password reset flow
  if (session && mode !== 'reset') {
    return <>{children(session)}</>
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    setNotice(null)

    try {
      if (mode === 'signup') {
        const { error } = await signUp(email.trim(), password)
        if (error) throw error
        setNotice('Account created. You are signed in.')
      } else if (mode === 'signin') {
        const { error } = await signIn(email.trim(), password)
        if (error) throw error
      } else if (mode === 'forgot') {
        const { error } = await resetPassword(email.trim())
        if (error) throw error
        setNotice('If an account exists for this email, password reset instructions have been sent.')
      } else if (mode === 'reset') {
        if (password.length < 8) {
          throw new Error('Password must be at least 8 characters.')
        }
        if (password !== confirmPassword) {
          throw new Error('Passwords do not match.')
        }
        const { error } = await updatePassword(password)
        if (error) throw error
        setNotice('Password updated successfully. You can now sign in.')
        setMode('signin')
        setPassword('')
        setConfirmPassword('')
      }
    } catch (err: any) {
      setError(err.message || 'Authentication failed')
    } finally {
      setBusy(false)
    }
  }

  const resetMode = (newMode: 'signin' | 'signup' | 'forgot' | 'reset') => {
    setMode(newMode)
    setError(null)
    setNotice(null)
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

        <h2 className="text-sm font-medium text-text-main mb-1">
          {mode === 'signin' && 'Sign in to your account'}
          {mode === 'signup' && 'Create your account'}
          {mode === 'forgot' && 'Reset your password'}
          {mode === 'reset' && 'Set new password'}
        </h2>

        {mode === 'forgot' && (
          <p className="text-xs text-text-muted mb-4">
            Enter your email and we will send you instructions to reset your password.
          </p>
        )}
        {mode === 'reset' && (
          <p className="text-xs text-text-muted mb-4">
            Enter your new password below.
          </p>
        )}
        {(mode === 'signin' || mode === 'signup') && (
          <div className="mb-4" />
        )}

        {/* Email Field (for signin, signup, forgot) */}
        {mode !== 'reset' && (
          <>
            <label htmlFor="auth-email" className="block text-xs text-text-secondary mb-1">Email</label>
            <input
              id="auth-email"
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={e => setEmail(e.target.value)}
              placeholder="you@domain.com"
              className="w-full min-h-[44px] px-3 rounded-xl bg-surface-2 border border-border text-sm text-text-main placeholder:text-text-muted focus:outline-none focus:border-border-strong mb-3"
            />
          </>
        )}

        {/* Password Field (for signin, signup, reset) */}
        {mode !== 'forgot' && (
          <>
            <div className="flex items-center justify-between mb-1">
              <label htmlFor="auth-password" className="block text-xs text-text-secondary">
                {mode === 'reset' ? 'New Password' : 'Password'}
              </label>
              {mode === 'signin' && (
                <button
                  type="button"
                  onClick={() => resetMode('forgot')}
                  className="text-xs text-primary hover:underline"
                >
                  Forgot password?
                </button>
              )}
            </div>
            <div className="relative mb-3">
              <input
                id="auth-password"
                type={showPassword ? 'text' : 'password'}
                required
                autoComplete={mode === 'signup' || mode === 'reset' ? 'new-password' : 'current-password'}
                minLength={8}
                value={password}
                onChange={e => setPassword(e.target.value)}
                placeholder="••••••••"
                className="w-full min-h-[44px] pl-3 pr-11 rounded-xl bg-surface-2 border border-border text-sm text-text-main placeholder:text-text-muted focus:outline-none focus:border-border-strong"
              />
              <button
                type="button"
                onClick={() => setShowPassword(p => !p)}
                aria-label={showPassword ? 'Hide password' : 'Show password'}
                className="absolute right-0 top-0 bottom-0 w-11 flex items-center justify-center text-text-muted hover:text-text-main transition-colors active:scale-95"
              >
                {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
          </>
        )}

        {/* Confirm Password Field (for reset mode) */}
        {mode === 'reset' && (
          <>
            <label htmlFor="auth-confirm-password" className="block text-xs text-text-secondary mb-1">
              Confirm New Password
            </label>
            <div className="relative mb-4">
              <input
                id="auth-confirm-password"
                type={showConfirmPassword ? 'text' : 'password'}
                required
                autoComplete="new-password"
                minLength={8}
                value={confirmPassword}
                onChange={e => setConfirmPassword(e.target.value)}
                placeholder="••••••••"
                className="w-full min-h-[44px] pl-3 pr-11 rounded-xl bg-surface-2 border border-border text-sm text-text-main placeholder:text-text-muted focus:outline-none focus:border-border-strong"
              />
              <button
                type="button"
                onClick={() => setShowConfirmPassword(p => !p)}
                aria-label={showConfirmPassword ? 'Hide password' : 'Show password'}
                className="absolute right-0 top-0 bottom-0 w-11 flex items-center justify-center text-text-muted hover:text-text-main transition-colors active:scale-95"
              >
                {showConfirmPassword ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
          </>
        )}

        {error && <p role="alert" className="text-sm text-danger mb-3">{error}</p>}
        {notice && <p role="status" className="text-sm text-success mb-3">{notice}</p>}

        <button
          type="submit"
          disabled={busy || (mode !== 'reset' && !email) || (mode !== 'forgot' && password.length < 8)}
          className="w-full min-h-[48px] rounded-xl bg-primary hover:bg-primary-hover text-white text-sm font-semibold shadow-hero-glow active:scale-[0.98] transition-all duration-150 disabled:opacity-40"
        >
          {busy ? 'Please wait…' :
            mode === 'signin' ? 'Sign in' :
            mode === 'signup' ? 'Create account' :
            mode === 'forgot' ? 'Send Reset Link' :
            'Update Password'}
        </button>

        <div className="flex items-center justify-between mt-4">
          {mode === 'forgot' || mode === 'reset' ? (
            <button
              type="button"
              onClick={() => resetMode('signin')}
              className="text-sm text-primary hover:underline flex items-center gap-1.5"
            >
              <ArrowLeft size={14} />
              <span>Back to Sign in</span>
            </button>
          ) : (
            <button
              type="button"
              onClick={() => resetMode(mode === 'signin' ? 'signup' : 'signin')}
              className="text-sm text-primary hover:underline"
            >
              {mode === 'signin' ? 'Need an account? Sign up' : 'Have an account? Sign in'}
            </button>
          )}

          {session === null && (
            <button
              type="button"
              onClick={() => signOut()}
              className="text-xs text-text-muted hover:underline hover:text-text-secondary"
            >
              Clear session
            </button>
          )}
        </div>
      </form>
    </div>
  )
}

export { signOut }
