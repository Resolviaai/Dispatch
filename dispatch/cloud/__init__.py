"""Cloud layer for Dispatch: identity/metadata (Supabase), private clip media (R2 via a
Cloudflare Worker control plane), and server-enforced quotas.

All video processing stays local on the Windows PC; originals stay on YouTube.
See docs/architecture/cloud-migration-plan.md for decisions D1-D11.
"""
