import { useState } from 'react'
import { api } from '../api/client'
import type { VirtualMachine, VmAction } from '../api/types'

interface Props {
  vms: VirtualMachine[] | null
  error: string | null
  onChanged: () => void
}

export function VmTable({ vms, error, onChanged }: Props) {
  const [busy, setBusy] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  async function run(vm: VirtualMachine, action: VmAction) {
    setBusy(vm.id)
    setActionError(null)
    try {
      const result = await api.vmAction(vm.id, action)
      if (!result.completed) setActionError(`${action} ${vm.name}: still in progress in CloudStack, the state will update`)
    } catch (e) {
      setActionError(`${action} ${vm.name}: ${e instanceof Error ? e.message : 'failed'}`)
    } finally {
      setBusy(null)
      onChanged()
    }
  }

  if (error && !vms) return <p className="error-box">Could not load VMs: {error}</p>
  if (!vms) return <p className="muted">Loading VMs...</p>
  if (vms.length === 0) return <p className="muted">No VMs in this account.</p>

  return (
    <>
      {actionError && <p className="error-box">{actionError}</p>}
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>State</th>
            <th>Zone</th>
            <th>Offering</th>
            <th>IP</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {vms.map((vm) => {
            const running = vm.state === 'Running'
            const stopped = vm.state === 'Stopped'
            const disabled = busy === vm.id
            return (
              <tr key={vm.id}>
                <td>{vm.name}</td>
                <td>
                  <span className={`badge vm-${vm.state.toLowerCase()}`}>{vm.state}</span>
                </td>
                <td>{vm.zone_name ?? '-'}</td>
                <td>{vm.service_offering_name ?? '-'}</td>
                <td>{vm.ip_address ?? '-'}</td>
                <td className="row-actions">
                  <button disabled={disabled || !stopped} onClick={() => void run(vm, 'start')}>
                    Start
                  </button>
                  <button disabled={disabled || !running} onClick={() => void run(vm, 'stop')}>
                    Stop
                  </button>
                  <button disabled={disabled || !running} onClick={() => void run(vm, 'reboot')}>
                    Reboot
                  </button>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </>
  )
}
