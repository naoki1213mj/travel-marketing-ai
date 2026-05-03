import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiUrl } from './api-base'

const ORIGINAL_BASE_URL = import.meta.env.BASE_URL

afterEach(() => {
  // Restore the BASE_URL stub between tests so other suites observing
  // import.meta.env.BASE_URL are unaffected.
  vi.stubEnv('BASE_URL', ORIGINAL_BASE_URL)
  vi.unstubAllEnvs()
})

describe('apiUrl', () => {
  it('returns the path unchanged in dev (BASE_URL="/")', () => {
    vi.stubEnv('BASE_URL', '/')
    expect(apiUrl('/api/conversations/abc')).toBe('/api/conversations/abc')
    expect(apiUrl('api/conversations/abc')).toBe('/api/conversations/abc')
  })

  it('prepends the /app/ prefix in production (BASE_URL="/app/")', () => {
    vi.stubEnv('BASE_URL', '/app/')
    // Bug C regression: APIM SPA reverse proxy registers an API at path
    // `/app` with a catch-all `/{*path}` operation. If the frontend builds
    // `/api/conversations/...` directly, APIM responds 404. apiUrl() must
    // prepend `/app/` so the request actually reaches the backend.
    expect(apiUrl('/api/conversations/abc')).toBe('/app/api/conversations/abc')
    expect(apiUrl('/api/chat/c1/manager-approval-request')).toBe(
      '/app/api/chat/c1/manager-approval-request',
    )
    expect(apiUrl('/api/sources/source-1/review')).toBe('/app/api/sources/source-1/review')
  })

  it('handles non-/api paths consistently', () => {
    vi.stubEnv('BASE_URL', '/app/')
    expect(apiUrl('/auth-redirect.html')).toBe('/app/auth-redirect.html')
    expect(apiUrl('auth-redirect.html')).toBe('/app/auth-redirect.html')
  })

  it('falls back to "/" when BASE_URL is empty string', () => {
    vi.stubEnv('BASE_URL', '')
    expect(apiUrl('/api/health')).toBe('/api/health')
  })
})
