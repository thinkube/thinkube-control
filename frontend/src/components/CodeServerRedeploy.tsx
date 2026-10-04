/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState } from 'react'
import { Hammer } from 'lucide-react'
import { toast } from 'sonner'
import { TkButton } from 'thinkube-style/components/buttons-badges'
import { TkTooltip, TkControlledConfirmDialog } from 'thinkube-style/components/modals-overlays'
import api from '@/lib/axios'
import { useRunsStore } from '../stores/useRunsStore'

const REDEPLOY_RUN = 'redeploy-code-server'

/**
 * Queues a rebuild of the code-server image and a redeploy of code-server.
 *
 * The run takes place in thinkube-control, so it continues while the IDE
 * restarts. The runs dialog shows its place in the queue, its log and its
 * outcome.
 */
export function CodeServerRedeploy() {
  const { openDialog, isInQueue, fetchRuns } = useRunsStore()
  const [confirming, setConfirming] = useState(false)
  const [starting, setStarting] = useState(false)

  const alreadyQueued = isInQueue(REDEPLOY_RUN)

  const queueRedeploy = async () => {
    setConfirming(false)
    setStarting(true)
    try {
      const { data } = await api.post('/code-server/redeploy')
      toast.success(
        data.queue_position && data.queue_position > 1
          ? `code-server redeploy queued at position ${data.queue_position}`
          : 'code-server redeploy queued, starts next'
      )
      await fetchRuns()
      openDialog()
    } catch (err: any) {
      toast.error(`Failed to queue the code-server redeploy: ${err.response?.data?.detail ?? err.message}`)
    } finally {
      setStarting(false)
    }
  }

  return (
    <>
      <TkTooltip content={alreadyQueued ? 'A code-server redeploy is already queued or running' : 'Rebuild the code-server image and redeploy code-server. The workspace repositories are not touched.'}>
        <TkButton
          intent="secondary"
          onClick={() => setConfirming(true)}
          disabled={starting || alreadyQueued}
        >
          <Hammer className="h-4 w-4 mr-2" />
          Redeploy
        </TkButton>
      </TkTooltip>

      <TkControlledConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title="Redeploy code-server"
        description="This rebuilds the code-server image and redeploys code-server. The IDE restarts and open sessions in it are interrupted. The workspace repositories are not touched. The redeploy waits in the queue if another run is in progress."
        confirmText="Redeploy"
        cancelText="Cancel"
        onConfirm={queueRedeploy}
        variant="destructive"
      />
    </>
  )
}
