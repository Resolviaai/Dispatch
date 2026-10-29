import { createClient, type Session } from '@supabase/supabase-js'
import { clearClipsCache } from '../offline/clipsCache'

// Public anon key (safe for clients, protected by RLS). Loaded at BUILD time from
// frontend/.env (git-ignored). The service-role key NEVER lives in the bundle.
const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL as string
const SUPABASE_ANON_KEY = import.meta.env.VITE_SUPABASE_ANON_KEY as string
if (!SUPABASE_URL || !SUPABASE_ANON_KEY) {
  throw new Error('Missing VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY - create frontend/.env before building')
}

export const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
  auth: { persistSession: true, autoRefreshToken: true },
})

export async function currentSession(): Promise<Session | null> {
  const { data } = await supabase.auth.getSession()
  return data.session ?? null
}

export async function signIn(email: string, password: string) {
  return supabase.auth.signInWithPassword({ email, password })
}

export async function signUp(email: string, password: string) {
  return supabase.auth.signUp({ email, password })
}

export async function signOut() {
  let userId: string | null = null
  try {
    const { data } = await supabase.auth.getSession()
    userId = data.session?.user?.id ?? null
  } catch {}
  await supabase.auth.signOut()
  if (userId) {
    await clearClipsCache(userId)
  }
  try {
    if (typeof window !== 'undefined' && (window as any).DispatchBridge?.setCurrentUserId) {
      (window as any).DispatchBridge.setCurrentUserId(null)
    }
  } catch {}
}
