import { MAX_TAGS, MAX_USER_DATA_BYTES, type Catalog, type FieldErrors, type ProvisionRequest } from './api/types'

// Keep in sync with NAME_RE in backend/app/models/provision.py. The backend
// checks everything again; this is only for fast feedback.
const NAME_RE = /^[a-z]([a-z0-9-]{0,48}[a-z0-9])?$/
const GROUP_RE = /^[A-Za-z0-9._-]{1,64}$/
const TAG_KEY_RE = /^[A-Za-z][A-Za-z0-9_.:-]{0,62}$/

/** Parses "env=prod, team=ops" into a tag map. Returns an error message if it cannot. */
export function parseTags(text: string): { tags: Record<string, string> } | { error: string } {
  const tags: Record<string, string> = {}
  for (const part of text.split(',')) {
    const item = part.trim()
    if (!item) continue
    const eq = item.indexOf('=')
    const key = eq === -1 ? item : item.slice(0, eq).trim()
    const value = eq === -1 ? '' : item.slice(eq + 1).trim()
    if (!TAG_KEY_RE.test(key)) return { error: `"${key}" is not a valid tag key` }
    if (!value) return { error: `Tag "${key}" needs a value, like ${key}=something` }
    if (value.length > 255) return { error: `The value of tag "${key}" is too long` }
    if (key in tags) return { error: `Tag "${key}" is listed twice` }
    tags[key] = value
  }
  if (Object.keys(tags).length > MAX_TAGS) return { error: `At most ${MAX_TAGS} tags` }
  return { tags }
}

export function validateForm(form: ProvisionRequest, catalog: Catalog): FieldErrors {
  const errors: FieldErrors = {}
  const { limits } = catalog

  if (!form.name) errors.name = 'Name is required'
  else if (form.name.length > 50) errors.name = 'At most 50 characters'
  else if (!NAME_RE.test(form.name))
    errors.name = 'Lowercase letters, digits and hyphens; start with a letter, end with a letter or digit'

  if (!catalog.zones.some((z) => z.id === form.zone)) errors.zone = 'Pick a zone'
  if (!catalog.service_offerings.some((o) => o.id === form.service_offering))
    errors.service_offering = 'Pick a compute offering'

  const template = catalog.templates.find((t) => t.id === form.template)
  if (!template) errors.template = 'Pick a template'
  else if (template.zone_id !== null && template.zone_id !== form.zone)
    errors.template = 'This template is not available in the selected zone'

  const network = catalog.networks.find((n) => n.id === form.network)
  if (!network) errors.network = 'Pick a network'
  else if (network.zone_id !== form.zone) errors.network = 'This network is not in the selected zone'

  if (form.keypair !== null && !catalog.keypairs.includes(form.keypair)) errors.keypair = 'Pick a keypair from the list'

  if (!Number.isInteger(form.disk_size_gb)) errors.disk_size_gb = 'Enter a whole number'
  else if (form.disk_size_gb < limits.min_disk_gb || form.disk_size_gb > limits.max_disk_gb)
    errors.disk_size_gb = `Between ${limits.min_disk_gb} and ${limits.max_disk_gb} GB`

  if (!Number.isInteger(form.instance_count)) errors.instance_count = 'Enter a whole number'
  else if (form.instance_count < 1 || form.instance_count > limits.max_instances)
    errors.instance_count = `Between 1 and ${limits.max_instances}`

  if (form.group !== null && !GROUP_RE.test(form.group))
    errors.group = 'Letters, digits, dots, hyphens and underscores, at most 64'

  if (form.user_data !== null && new TextEncoder().encode(form.user_data).length > MAX_USER_DATA_BYTES)
    errors.user_data = 'User data is limited to 16 KB'

  if (form.data_disk_offering !== null) {
    const offering = catalog.disk_offerings.find((o) => o.id === form.data_disk_offering)
    if (!offering) errors.data_disk_offering = 'Pick a disk offering from the list'
    else if (offering.customized) {
      const size = form.data_disk_size_gb
      if (size === null || !Number.isInteger(size)) errors.data_disk_size_gb = 'This offering needs a size in GB'
      else if (size < 1 || size > limits.max_disk_gb) errors.data_disk_size_gb = `Between 1 and ${limits.max_disk_gb} GB`
    }
  }

  return errors
}
