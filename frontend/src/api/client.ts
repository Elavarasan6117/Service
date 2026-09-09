/**
 * API client.
 *
 * Two things worth noting:
 *  - Tokens live in memory with only the refresh token in localStorage. An
 *    access token in localStorage is readable by any injected script; keeping
 *    it in a module variable limits the window.
 *  - A 401 triggers exactly one refresh attempt, and concurrent requests share
 *    that single attempt rather than each firing their own.
 */

import type {
  AddressSuggestion,
  ApiErrorBody,
  AppConfigItem,
  ConfigAuditItem,
  Customer,
  CustomerCreateResponse,
  DashboardMetrics,
  MapOperations,
  Page,
  ServiceabilityCheck,
  ServiceabilityResult,
  ServiceLocation,
  ServiceLocationCreatePayload,
  ServiceLocationUpdatePayload,
  User,
  Warehouse,
  WarehouseCreatePayload,
  WarehouseUpdatePayload,
} from '../types/api'

const BASE = import.meta.env.VITE_API_BASE_URL ?? '/api/v1'
const REFRESH_KEY = 'svc.refreshToken'

let accessToken: string | null = null
let refreshInFlight: Promise<boolean> | null = null

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public requestId: string | null = null,
    public details: ApiErrorBody['error']['details'] = [],
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export function getAccessToken(): string | null {
  return accessToken
}

export function setTokens(access: string, refresh: string): void {
  accessToken = access
  try {
    localStorage.setItem(REFRESH_KEY, refresh)
  } catch {
    // Private browsing or blocked storage: the session still works, it just
    // will not survive a page reload.
  }
}

export function clearTokens(): void {
  accessToken = null
  try {
    localStorage.removeItem(REFRESH_KEY)
  } catch {
    /* ignore */
  }
}

export function hasStoredSession(): boolean {
  try {
    return Boolean(localStorage.getItem(REFRESH_KEY))
  } catch {
    return false
  }
}

async function attemptRefresh(): Promise<boolean> {
  let stored: string | null = null
  try {
    stored = localStorage.getItem(REFRESH_KEY)
  } catch {
    return false
  }
  if (!stored) return false

  try {
    const response = await fetch(`${BASE}/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refreshToken: stored }),
    })
    if (!response.ok) {
      clearTokens()
      return false
    }
    const body = await response.json()
    setTokens(body.accessToken, body.refreshToken)
    return true
  } catch {
    return false
  }
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  retryOn401 = true,
): Promise<T> {
  const headers = new Headers(init.headers)
  if (!(init.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json')
  }
  if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`)

  const response = await fetch(`${BASE}${path}`, { ...init, headers })

  if (response.status === 401 && retryOn401) {
    // Share one refresh across concurrent 401s instead of stampeding.
    refreshInFlight ??= attemptRefresh().finally(() => {
      refreshInFlight = null
    })
    if (await refreshInFlight) {
      return request<T>(path, init, false)
    }
  }

  if (response.status === 204) return undefined as T

  const contentType = response.headers.get('content-type') ?? ''
  if (!response.ok) {
    let code = 'HTTP_ERROR'
    let message = `Request failed with status ${response.status}`
    let requestId: string | null = response.headers.get('X-Request-ID')
    let details: ApiErrorBody['error']['details'] = []
    if (contentType.includes('application/json')) {
      try {
        const body = (await response.json()) as ApiErrorBody
        code = body.error?.code ?? code
        message = body.error?.message ?? message
        requestId = body.error?.requestId ?? requestId
        details = body.error?.details ?? []
      } catch {
        /* keep defaults */
      }
    }
    throw new ApiError(response.status, code, message, requestId, details)
  }

  if (!contentType.includes('application/json')) return undefined as T
  return (await response.json()) as T
}

// --- Auth ---------------------------------------------------------------

export const api = {
  async login(username: string, password: string): Promise<User> {
    const body = await request<{ accessToken: string; refreshToken: string }>(
      '/auth/login',
      { method: 'POST', body: JSON.stringify({ username, password }) },
      false,
    )
    setTokens(body.accessToken, body.refreshToken)
    return api.me()
  },

  async restoreSession(): Promise<User | null> {
    if (!hasStoredSession()) return null
    if (!(await attemptRefresh())) return null
    try {
      return await api.me()
    } catch {
      return null
    }
  },

  me: () => request<User>('/auth/me'),

  logout: async () => {
    try {
      await request('/auth/logout', { method: 'POST' })
    } finally {
      clearTokens()
    }
  },

  streamToken: () =>
    request<{ streamToken: string; expiresIn: number }>('/auth/stream-token', {
      method: 'POST',
    }),

  // --- Customers --------------------------------------------------------

  createCustomer: (payload: Record<string, unknown>) =>
    request<CustomerCreateResponse>('/customers', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  listCustomers: (params: Record<string, string | number | undefined> = {}) =>
    request<Page<Customer>>(`/customers${qs(params)}`),

  getCustomer: (id: string) => request<Customer>(`/customers/${id}`),

  runCheck: (id: string, forceRefresh = false) =>
    request<ServiceabilityResult>(`/customers/${id}/serviceability-check`, {
      method: 'POST',
      body: JSON.stringify({ forceRefresh }),
    }),

  previewServiceability: (payload: {
    latitude?: number
    longitude?: number
    address?: string
    serviceType?: string
  }) =>
    request<ServiceabilityResult>('/serviceability/preview', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  getServiceability: (id: string) =>
    request<ServiceabilityResult>(`/customers/${id}/serviceability`),

  updateCustomerLocation: (
    id: string,
    latitude: number,
    longitude: number,
    reason?: string,
  ) =>
    request<ServiceabilityResult>(`/customers/${id}/location`, {
      method: 'PATCH',
      body: JSON.stringify({ latitude, longitude, reason }),
    }),

  // --- Geocoding (proxied — no Google key in this browser) --------------

  autocomplete: (q: string, sessionToken: string) =>
    request<AddressSuggestion[]>(
      `/geocoding/autocomplete${qs({ q, session_token: sessionToken })}`,
    ),

  geocode: (address: string) =>
    request<{
      latitude: number
      longitude: number
      formattedAddress: string
      confidence: string
      needsVerification: boolean
    }>('/geocoding/geocode', {
      method: 'POST',
      body: JSON.stringify({ address }),
    }),

  reverseGeocode: (latitude: number, longitude: number) =>
    request<{ formattedAddress: string }>('/geocoding/reverse', {
      method: 'POST',
      body: JSON.stringify({ latitude, longitude }),
    }),

  // --- Map, dashboard, history -----------------------------------------

  mapOperations: (params: Record<string, string | number | undefined> = {}) =>
    request<MapOperations>(`/map/operations${qs(params)}`),

  dashboardMetrics: () => request<DashboardMetrics>('/dashboard/metrics'),

  listChecks: (params: Record<string, string | number | undefined> = {}) =>
    request<Page<ServiceabilityCheck>>(`/serviceability/checks${qs(params)}`),

  deleteCheck: (id: string) =>
    request<{ message: string }>(`/serviceability/checks/${id}`, {
      method: 'DELETE',
    }),

  listServiceLocations: (params: Record<string, string | number | undefined> = {}) =>
    request<Page<ServiceLocation>>(`/service-locations${qs(params)}`),

  createServiceLocation: (payload: ServiceLocationCreatePayload) =>
    request<ServiceLocation>('/service-locations', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  updateServiceLocation: (id: string, payload: ServiceLocationUpdatePayload) =>
    request<ServiceLocation>(`/service-locations/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),

  deleteServiceLocation: (id: string) =>
    request<ServiceLocation>(`/service-locations/${id}`, { method: 'DELETE' }),

  // --- Warehouses ---------------------------------------------------------

  listWarehouses: () => request<Warehouse[]>('/warehouses'),

  createWarehouse: (payload: WarehouseCreatePayload) =>
    request<Warehouse>('/warehouses', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  updateWarehouse: (id: string, payload: WarehouseUpdatePayload) =>
    request<Warehouse>(`/warehouses/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),

  deleteWarehouse: (id: string) =>
    request<Warehouse>(`/warehouses/${id}`, { method: 'DELETE' }),

  // --- Admin ------------------------------------------------------------

  listConfig: () => request<AppConfigItem[]>('/admin/config'),

  updateConfig: (key: string, value: string, reason: string) =>
    request<AppConfigItem>(`/admin/config/${encodeURIComponent(key)}`, {
      method: 'PUT',
      body: JSON.stringify({ value, reason }),
    }),

  configHistory: (key: string) =>
    request<ConfigAuditItem[]>(`/admin/config/${encodeURIComponent(key)}/history`),

  routingUsage: () =>
    request<{ provider: string; healthy: boolean; cache: Record<string, unknown> }>(
      '/admin/routing/usage',
    ),

  // --- Import -----------------------------------------------------------

  uploadImport: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<Record<string, unknown>>('/imports', {
      method: 'POST',
      body: form,
    })
  },

  commitImport: (batchId: string) =>
    request<{ committed: number; skipped: number; message: string }>(
      `/imports/${batchId}/commit`,
      { method: 'POST' },
    ),
}

function qs(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') {
      search.set(key, String(value))
    }
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

export const apiBase = BASE
