/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useEffect, useRef, useState } from 'react';
import { ListChecks, Loader2, CheckCircle2, XCircle, Clock, Ban } from 'lucide-react';
import {
  TkDialogRoot,
  TkDialogContent,
  TkDialogHeader,
  TkDialogTitle,
} from 'thinkube-style/components/modals-overlays';
import { TkButton } from 'thinkube-style/components/buttons-badges';
import api from '@/lib/axios';
import { useRunsStore, type RunEntry } from '../stores/useRunsStore';

interface RunProgress {
  current_step: string | null;
  steps_done: number;
  reason: string | null;
}

const LOG_LIMIT = 1000;

function useRunLog(runId: string | null, follow: boolean) {
  const [lines, setLines] = useState<string[]>([]);
  const [progress, setProgress] = useState<RunProgress | null>(null);

  useEffect(() => {
    setLines([]);
    setProgress(null);
    if (!runId) return;
    let stopped = false;
    const read = async () => {
      try {
        const [status, logs] = await Promise.all([
          api.get(`/templates/deployments/${runId}`),
          api.get(`/templates/deployments/${runId}/logs`, { params: { limit: LOG_LIMIT } }),
        ]);
        if (stopped) return;
        setProgress({
          current_step: status.data.current_step ?? null,
          steps_done: status.data.steps_done ?? 0,
          reason: status.data.reason ?? null,
        });
        setLines(logs.data.logs.map((l: any) => l.message));
      } catch (err) {
        console.error('Failed to read the run log:', err);
      }
    };
    read();
    if (!follow) return () => { stopped = true; };
    const timer = setInterval(read, 3000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [runId, follow]);

  return { lines, progress };
}

function LogView({ lines }: { lines: string[] }) {
  const ref = useRef<HTMLPreElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [lines.length]);
  return (
    <pre ref={ref} className="text-xs bg-muted rounded p-3 max-h-72 overflow-auto whitespace-pre-wrap">
      {lines.length ? lines.join('\n') : 'No output yet'}
    </pre>
  );
}

function StatusIcon({ status }: { status: string }) {
  if (status === 'success') return <CheckCircle2 className="h-4 w-4 text-green-600" />;
  if (status === 'cancelled') return <Ban className="h-4 w-4 text-muted-foreground" />;
  if (status === 'queued') return <Clock className="h-4 w-4 text-muted-foreground" />;
  if (status === 'pending' || status === 'running') return <Loader2 className="h-4 w-4 animate-spin text-primary" />;
  return <XCircle className="h-4 w-4 text-destructive" />;
}

function ActiveRun({ run }: { run: RunEntry }) {
  const { lines, progress } = useRunLog(run.id, true);
  return (
    <section className="space-y-2">
      <h4 className="text-sm font-semibold">In progress</h4>
      <div className="flex items-center gap-2">
        <StatusIcon status={run.status} />
        <span className="font-medium">{run.name}</span>
        <span className="text-xs text-muted-foreground">{run.kind}</span>
      </div>
      <p className="text-sm text-muted-foreground">
        {progress?.current_step ? `Step ${progress.steps_done}: ${progress.current_step}` : 'Starting...'}
      </p>
      <LogView lines={lines} />
    </section>
  );
}

function FinishedRun({ run }: { run: RunEntry }) {
  const [open, setOpen] = useState(false);
  const { lines, progress } = useRunLog(open ? run.id : null, false);
  return (
    <li className="space-y-2">
      <div className="flex items-center gap-2">
        <StatusIcon status={run.status} />
        <span className="font-medium">{run.name}</span>
        <span className="text-xs text-muted-foreground">{run.kind}</span>
        <span className="text-xs text-muted-foreground ml-auto">
          {run.completed_at ? new Date(run.completed_at).toLocaleString() : ''}
        </span>
        <TkButton intent="secondary" size="sm" onClick={() => setOpen(!open)}>
          {open ? 'Hide log' : 'View log'}
        </TkButton>
      </div>
      {open && (
        <>
          {run.status !== 'success' && (progress?.reason || run.output) && (
            <p className="text-sm text-destructive">{progress?.reason || run.output}</p>
          )}
          <LogView lines={lines} />
        </>
      )}
    </li>
  );
}

/**
 * Header button for the run queue, with the number of runs waiting or in
 * progress, and the dialog that shows the run in progress with its live log,
 * the queue and the latest results.
 */
export function RunsIndicator() {
  const { active, queued, recent, dialogOpen, fetchRuns, startPolling, stopPolling, openDialog, setDialogOpen, removeQueued } =
    useRunsStore();

  useEffect(() => {
    fetchRuns();
    startPolling();
    return () => stopPolling();
  }, []);

  const count = (active ? 1 : 0) + queued.length;

  const remove = async (run: RunEntry) => {
    try {
      await removeQueued(run.id);
    } catch (err: any) {
      alert(`Could not remove ${run.name} from the queue: ${err.response?.data?.detail ?? err.message}`);
    }
  };

  return (
    <>
      <button
        onClick={openDialog}
        className="flex items-center gap-2 px-3 py-1.5 rounded-md hover:bg-muted transition-colors"
        title="Deployment runs"
      >
        {active ? <Loader2 className="w-4 h-4 animate-spin" /> : <ListChecks className="w-4 h-4" />}
        {count > 0 && <span className="text-sm font-medium">{count}</span>}
      </button>

      <TkDialogRoot open={dialogOpen} onOpenChange={setDialogOpen}>
        <TkDialogContent className="max-w-3xl max-h-[90vh] overflow-y-auto">
          <TkDialogHeader>
            <TkDialogTitle>Deployment runs</TkDialogTitle>
          </TkDialogHeader>

          <div className="space-y-6">
            <p className="text-sm text-muted-foreground">
              Runs start one at a time. Others wait in the queue in the order they were submitted.
            </p>

            {active ? <ActiveRun key={active.id} run={active} /> : (
              <p className="text-sm">No run in progress.</p>
            )}

            {queued.length > 0 && (
              <section className="space-y-2">
                <h4 className="text-sm font-semibold">Queued</h4>
                <ul className="space-y-2">
                  {queued.map(run => (
                    <li key={run.id} className="flex items-center gap-2">
                      <span className="text-sm text-muted-foreground w-6">{run.queue_position}.</span>
                      <StatusIcon status={run.status} />
                      <span className="font-medium">{run.name}</span>
                      <span className="text-xs text-muted-foreground">{run.kind}</span>
                      <TkButton intent="secondary" size="sm" className="ml-auto" onClick={() => remove(run)}>
                        Remove
                      </TkButton>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {recent.length > 0 && (
              <section className="space-y-2">
                <h4 className="text-sm font-semibold">Recent</h4>
                <ul className="space-y-3">
                  {recent.map(run => <FinishedRun key={run.id} run={run} />)}
                </ul>
              </section>
            )}
          </div>
        </TkDialogContent>
      </TkDialogRoot>
    </>
  );
}
