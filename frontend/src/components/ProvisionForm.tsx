import { useEffect, useState, type FormEvent } from 'react'
import { api, ApiError } from '../api/client'
import type { Catalog, FieldErrors, FormField, Job, Named, ProvisionRequest } from '../api/types'
import { parseTags, validateForm } from '../validation'

const ADVANCED: FormField[] = ['group', 'tags', 'user_data', 'data_disk_offering', 'data_disk_size_gb']

function initialForm(catalog: Catalog): ProvisionRequest {
  const zone = catalog.zones[0]?.id ?? ''
  return {
    name: '',
    zone,
    service_offering: catalog.service_offerings[0]?.id ?? '',
    template: catalog.templates.find((t) => t.zone_id === null || t.zone_id === zone)?.id ?? '',
    network: catalog.networks.find((n) => n.zone_id === zone)?.id ?? '',
    disk_size_gb: Math.max(20, catalog.limits.min_disk_gb),
    instance_count: 1,
    keypair: null,
    start_vm: true,
    group: null,
    tags: {},
    user_data: null,
    data_disk_offering: null,
    data_disk_size_gb: null,
  }
}

export function ProvisionForm({ onCreated }: { onCreated: (job: Job) => void }) {
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)

  useEffect(() => {
    api
      .catalog()
      .then(setCatalog)
      .catch((e: unknown) => setLoadError(e instanceof Error ? e.message : 'Could not load the catalog'))
  }, [])

  if (loadError) return <p className="error-box">Could not load zones and offerings: {loadError}</p>
  if (!catalog) return <p className="muted">Loading zones and offerings...</p>
  return <Form catalog={catalog} onCreated={onCreated} />
}

function Form({ catalog, onCreated }: { catalog: Catalog; onCreated: (job: Job) => void }) {
  const [form, setForm] = useState<ProvisionRequest>(() => initialForm(catalog))
  const [touched, setTouched] = useState<Partial<Record<FormField, boolean>>>({})
  const [serverErrors, setServerErrors] = useState<FieldErrors>({})
  const [submitting, setSubmitting] = useState(false)
  // Tags are typed as free text and parsed on every change.
  const [tagsText, setTagsText] = useState('')
  const [tagsError, setTagsError] = useState<string | undefined>()
  const [advancedOpen, setAdvancedOpen] = useState(false)

  const networks = catalog.networks.filter((n) => n.zone_id === form.zone)
  const templates = catalog.templates.filter((t) => t.zone_id === null || t.zone_id === form.zone)
  const clientErrors: FieldErrors = { ...validateForm(form, catalog), ...(tagsError ? { tags: tagsError } : {}) }
  const diskOffering = catalog.disk_offerings.find((o) => o.id === form.data_disk_offering)

  const errorFor = (field: FormField) => (touched[field] ? clientErrors[field] : undefined) ?? serverErrors[field]

  function set<K extends FormField>(field: K, value: ProvisionRequest[K]) {
    setForm((prev) => ({ ...prev, [field]: value }))
    setServerErrors((prev) => ({ ...prev, [field]: undefined, form: undefined }))
  }

  function setZone(zone: string) {
    // Network and template depend on the zone: keep them valid.
    setForm((prev) => ({
      ...prev,
      zone,
      network: catalog.networks.find((n) => n.zone_id === zone)?.id ?? '',
      template: catalog.templates.some((t) => t.id === prev.template && (t.zone_id === null || t.zone_id === zone))
        ? prev.template
        : (catalog.templates.find((t) => t.zone_id === null || t.zone_id === zone)?.id ?? ''),
    }))
    setServerErrors({})
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    setTouched({
      name: true,
      zone: true,
      service_offering: true,
      template: true,
      network: true,
      disk_size_gb: true,
      instance_count: true,
      keypair: true,
      group: true,
      tags: true,
      user_data: true,
      data_disk_offering: true,
      data_disk_size_gb: true,
    })
    // Never leave an error hidden inside the collapsed section.
    if (ADVANCED.some((f) => clientErrors[f])) setAdvancedOpen(true)
    if (Object.keys(clientErrors).length > 0) return
    setSubmitting(true)
    try {
      onCreated(await api.createJob(form))
    } catch (e) {
      if (e instanceof ApiError && Object.keys(e.fieldErrors).length > 0) {
        setServerErrors(e.fieldErrors)
        if (ADVANCED.some((f) => e.fieldErrors[f])) setAdvancedOpen(true)
      } else setServerErrors({ form: e instanceof Error ? e.message : 'Request failed' })
      setSubmitting(false)
    }
  }

  const select = (field: 'zone' | 'network' | 'service_offering' | 'template', label: string, options: Named[]) => (
    <Field label={label} error={errorFor(field)} htmlFor={field}>
      <select
        id={field}
        value={form[field]}
        onChange={(e) => (field === 'zone' ? setZone(e.target.value) : set(field, e.target.value as never))}
        onBlur={() => setTouched((t) => ({ ...t, [field]: true }))}
        aria-invalid={Boolean(errorFor(field))}
      >
        {options.length === 0 && <option value="">Nothing available</option>}
        {options.map((o) => (
          <option key={o.id} value={o.id}>
            {o.name}
          </option>
        ))}
      </select>
    </Field>
  )

  const number = (field: 'disk_size_gb' | 'instance_count', label: string, min: number, max: number) => (
    <Field label={label} error={errorFor(field)} htmlFor={field} hint={`${min} to ${max}`}>
      <input
        id={field}
        type="number"
        min={min}
        max={max}
        step={1}
        value={Number.isNaN(form[field]) ? '' : form[field]}
        onChange={(e) => set(field, e.target.value === '' ? Number.NaN : Number(e.target.value))}
        onBlur={() => setTouched((t) => ({ ...t, [field]: true }))}
        aria-invalid={Boolean(errorFor(field))}
      />
    </Field>
  )

  return (
    <form onSubmit={submit} noValidate className="form">
      <Field
        label="Name"
        error={errorFor('name')}
        htmlFor="name"
        hint={form.instance_count > 1 && form.name ? `Instances will be ${form.name}-1, ${form.name}-2, ...` : undefined}
      >
        <input
          id="name"
          value={form.name}
          maxLength={50}
          placeholder="web"
          autoComplete="off"
          spellCheck={false}
          onChange={(e) => set('name', e.target.value)}
          onBlur={() => setTouched((t) => ({ ...t, name: true }))}
          aria-invalid={Boolean(errorFor('name'))}
        />
      </Field>
      <div className="grid-2">
        {select('zone', 'Zone', catalog.zones)}
        {select('network', 'Network', networks)}
        {select('service_offering', 'Compute offering', catalog.service_offerings)}
        {select('template', 'Template / OS', templates)}
        {number('disk_size_gb', 'Root disk size (GB)', catalog.limits.min_disk_gb, catalog.limits.max_disk_gb)}
        {number('instance_count', 'Number of instances', 1, catalog.limits.max_instances)}
        <Field
          label="SSH keypair"
          error={errorFor('keypair')}
          htmlFor="keypair"
          hint={form.keypair === null ? 'Without a keypair you may not be able to log in' : undefined}
        >
          <select
            id="keypair"
            value={form.keypair ?? ''}
            onChange={(e) => set('keypair', e.target.value === '' ? null : e.target.value)}
            onBlur={() => setTouched((t) => ({ ...t, keypair: true }))}
            aria-invalid={Boolean(errorFor('keypair'))}
          >
            <option value="">None</option>
            {catalog.keypairs.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <details className="advanced" open={advancedOpen} onToggle={(e) => setAdvancedOpen(e.currentTarget.open)}>
        <summary>More options</summary>
        <div className="grid-2">
          <Field label="Instance group" error={errorFor('group')} htmlFor="group" hint="Optional, for organising VMs">
            <input
              id="group"
              value={form.group ?? ''}
              maxLength={64}
              autoComplete="off"
              spellCheck={false}
              onChange={(e) => set('group', e.target.value === '' ? null : e.target.value)}
              onBlur={() => setTouched((t) => ({ ...t, group: true }))}
              aria-invalid={Boolean(errorFor('group'))}
            />
          </Field>
          <Field label="Tags" error={errorFor('tags')} htmlFor="tags" hint="Like env=prod, team=ops">
            <input
              id="tags"
              value={tagsText}
              autoComplete="off"
              spellCheck={false}
              onChange={(e) => {
                setTagsText(e.target.value)
                const parsed = parseTags(e.target.value)
                setTagsError('error' in parsed ? parsed.error : undefined)
                if ('tags' in parsed) set('tags', parsed.tags)
              }}
              onBlur={() => setTouched((t) => ({ ...t, tags: true }))}
              aria-invalid={Boolean(errorFor('tags'))}
            />
          </Field>
          <Field
            label="Data disk"
            error={errorFor('data_disk_offering')}
            htmlFor="data_disk_offering"
            hint="An extra volume attached to each instance"
          >
            <select
              id="data_disk_offering"
              value={form.data_disk_offering ?? ''}
              onChange={(e) => {
                const id = e.target.value === '' ? null : e.target.value
                const custom = catalog.disk_offerings.find((o) => o.id === id)?.customized ?? false
                setForm((prev) => ({ ...prev, data_disk_offering: id, data_disk_size_gb: custom ? 50 : null }))
                setServerErrors({})
              }}
              aria-invalid={Boolean(errorFor('data_disk_offering'))}
            >
              <option value="">None</option>
              {catalog.disk_offerings.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name}
                </option>
              ))}
            </select>
          </Field>
          {diskOffering?.customized && (
            <Field
              label="Data disk size (GB)"
              error={errorFor('data_disk_size_gb')}
              htmlFor="data_disk_size_gb"
              hint={`1 to ${catalog.limits.max_disk_gb}`}
            >
              <input
                id="data_disk_size_gb"
                type="number"
                min={1}
                max={catalog.limits.max_disk_gb}
                step={1}
                value={form.data_disk_size_gb === null || Number.isNaN(form.data_disk_size_gb) ? '' : form.data_disk_size_gb}
                onChange={(e) => set('data_disk_size_gb', e.target.value === '' ? Number.NaN : Number(e.target.value))}
                onBlur={() => setTouched((t) => ({ ...t, data_disk_size_gb: true }))}
                aria-invalid={Boolean(errorFor('data_disk_size_gb'))}
              />
            </Field>
          )}
        </div>
        <Field
          label="User data (cloud-init)"
          error={errorFor('user_data')}
          htmlFor="user_data"
          hint="Runs on first boot. Kept out of logs and never shown again after you submit."
        >
          <textarea
            id="user_data"
            rows={6}
            spellCheck={false}
            placeholder={'#cloud-config\npackages:\n  - nginx'}
            value={form.user_data ?? ''}
            onChange={(e) => set('user_data', e.target.value === '' ? null : e.target.value)}
            onBlur={() => setTouched((t) => ({ ...t, user_data: true }))}
            aria-invalid={Boolean(errorFor('user_data'))}
          />
        </Field>
        <label className="check">
          <input type="checkbox" checked={form.start_vm} onChange={(e) => set('start_vm', e.target.checked)} />
          Start the instances after creating them
        </label>
      </details>
      {serverErrors.form && <p className="error-box">{serverErrors.form}</p>}
      <div className="actions">
        <button type="submit" className="primary" disabled={submitting}>
          {submitting ? 'Starting...' : 'Create plan'}
        </button>
        <span className="muted">Nothing is created until you confirm the plan.</span>
      </div>
    </form>
  )
}

function Field(props: { label: string; htmlFor: string; error?: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="field">
      <label htmlFor={props.htmlFor}>{props.label}</label>
      {props.children}
      {props.error ? (
        <span className="field-error" role="alert">
          {props.error}
        </span>
      ) : (
        props.hint && <span className="hint">{props.hint}</span>
      )}
    </div>
  )
}
