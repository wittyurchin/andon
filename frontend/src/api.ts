import type { RestaurantConfig, SituationResponse, StatusResponse } from './types'

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, signal?: AbortSignal): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, { signal, headers: { Accept: 'application/json' } })
  } catch (error) {
    if ((error as Error).name === 'AbortError') throw error
    throw new ApiError('Could not reach the situation service. Is the backend running?')
  }

  if (!response.ok) {
    let detail = `Request failed (${response.status})`
    try {
      const body = await response.json()
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : detail
    } catch {
      /* response had no JSON body */
    }
    throw new ApiError(detail, response.status)
  }

  return (await response.json()) as T
}

export function fetchSituation(
  config: RestaurantConfig,
  options: { force?: boolean; poll?: boolean; signal?: AbortSignal } = {},
): Promise<SituationResponse> {
  const params = new URLSearchParams({
    name: config.name,
    lat: String(config.lat),
    lon: String(config.lon),
  })
  if (config.refreshMs > 0) {
    // Stored with the restaurant, so a server-side poller uses the same cadence.
    params.set('refresh_interval_s', String(Math.max(30, Math.round(config.refreshMs / 1000))))
  }
  if (options.force) params.set('force', 'true')
  // A page load: replay the last polled response, no provider contacted at all.
  if (options.poll === false) params.set('poll', 'false')
  return request<SituationResponse>(`/api/situation?${params}`, options.signal)
}

export function fetchStatus(signal?: AbortSignal): Promise<StatusResponse> {
  return request<StatusResponse>('/api/status', signal)
}

export function fetchSourceDetail(
  restaurantId: string,
  sourceId: string,
  signal?: AbortSignal,
): Promise<import('./types').SourceDetail> {
  return request(`/api/restaurants/${encodeURIComponent(restaurantId)}/sources/${encodeURIComponent(sourceId)}`, signal)
}
