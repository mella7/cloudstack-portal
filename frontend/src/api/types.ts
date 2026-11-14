export type JobStatus =
  | 'queued'
  | 'planning'
  | 'awaiting_confirmation'
  | 'applying'
  | 'done'
  | 'failed'
  | 'cancelled'

export const TERMINAL: readonly JobStatus[] = ['done', 'failed', 'cancelled']

export interface ProvisionRequest {
  name: string
  zone: string
  service_offering: string
  template: string
  network: string
  disk_size_gb: number
  instance_count: number
  keypair: string | null
  start_vm: boolean
  group: string | null
  tags: Record<string, string>
  /** What the user typed. In jobs returned by the API this is "[hidden]" when set. */
  user_data: string | null
  data_disk_offering: string | null
  data_disk_size_gb: number | null
}

export type FormField = keyof ProvisionRequest
export type FieldErrors = Partial<Record<FormField | 'form', string>>

export const MAX_TAGS = 10
export const MAX_USER_DATA_BYTES = 16 * 1024

export interface Named {
  id: string
  name: string
}

export interface Catalog {
  zones: Named[]
  service_offerings: Named[]
  templates: (Named & { zone_id: string | null })[]
  networks: (Named & { zone_id: string })[]
  keypairs: string[]
  disk_offerings: (Named & { size_gb: number | null; customized: boolean })[]
  limits: { min_disk_gb: number; max_disk_gb: number; max_instances: number }
}

export interface PlannedResource {
  address: string
  type: string
  name: string
  action: string
  details: Record<string, string>
}

export interface PlanSummary {
  add: number
  change: number
  destroy: number
  resources: PlannedResource[]
}

export interface Job {
  id: string
  kind: 'create' | 'destroy'
  source_job_id: string | null
  status: JobStatus
  request: ProvisionRequest
  plan_summary: PlanSummary | null
  error: string | null
  created_at: string
  updated_at: string
  finished_at: string | null
  destroyed_at: string | null
  destroyable: boolean
}

export interface LogLine {
  seq: number
  ts: string
  line: string
}

export interface VirtualMachine {
  id: string
  name: string
  state: string
  zone_name: string | null
  service_offering_name: string | null
  template_name: string | null
  ip_address: string | null
}

export type VmAction = 'start' | 'stop' | 'reboot'

export interface VmActionResult {
  vm_id: string
  action: VmAction
  async_job_id: string | null
  completed: boolean
}
