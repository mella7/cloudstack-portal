import type {
  Catalog,
  FieldErrors,
  Job,
  ProvisionRequest,
  VirtualMachine,
  VmAction,
  VmActionResult,
} from './types'

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly fieldErrors: FieldErrors = {},
  ) {
    super(message)
  }
}

interface FieldErrorItem {
  field: string
  message: string
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let resp: Response
  try {
    resp = await fetch(`/api${path}`, {
      ...init,
      headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
    })
  } catch {
    throw new ApiError('Cannot reach the backend', 0)
  }
  if (resp.ok) return (await resp.json()) as T

  const body: unknown = await resp.json().catch(() => null)
  const detail = (body as { detail?: unknown } | null)?.detail
  if (Array.isArray(detail)) {
    const fieldErrors: Record<string, string> = {}
    for (const item of detail as FieldErrorItem[]) fieldErrors[item.field] ??= item.message
    throw new ApiError('Some fields are not valid', resp.status, fieldErrors)
  }
  throw new ApiError(typeof detail === 'string' ? detail : `Request failed (${resp.status})`, resp.status)
}

export const api = {
  health: () => request<{ status: string; dry_run: boolean; plan_ttl_minutes: number }>('/health'),
  catalog: () => request<Catalog>('/catalog'),
  listJobs: () => request<Job[]>('/jobs'),
  getJob: (id: string) => request<Job>(`/jobs/${encodeURIComponent(id)}`),
  createJob: (form: ProvisionRequest) => request<Job>('/jobs', { method: 'POST', body: JSON.stringify(form) }),
  confirmJob: (id: string) => request<Job>(`/jobs/${encodeURIComponent(id)}/confirm`, { method: 'POST' }),
  cancelJob: (id: string) => request<Job>(`/jobs/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),
  destroyJob: (id: string) => request<Job>(`/jobs/${encodeURIComponent(id)}/destroy`, { method: 'POST' }),
  listVms: () => request<VirtualMachine[]>('/vms'),
  vmAction: (id: string, action: VmAction) =>
    request<VmActionResult>(`/vms/${encodeURIComponent(id)}/${action}`, { method: 'POST' }),
  streamUrl: (id: string) => `/api/jobs/${encodeURIComponent(id)}/stream`,
}
