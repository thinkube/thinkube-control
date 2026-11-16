/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { RefreshCw } from 'lucide-react';
import { TkButton } from 'thinkube-style/components/buttons-badges';
import { TkCard, TkCardContent, TkCardDescription, TkCardHeader, TkCardTitle } from 'thinkube-style/components/cards-data';
import { TkErrorAlert } from 'thinkube-style/components/feedback';
import { TkPageWrapper } from 'thinkube-style/components/utilities';
import { TkTable, TkTableBody, TkTableCell, TkTableHead, TkTableHeader, TkTableRow } from 'thinkube-style/components/tables';
import api from '@/lib/axios';
import { RunStateBadge } from '@/components/RunStateBadge';
import { StageBars } from '@/components/StageBars';
import { failureText, type RunSummary } from '@/lib/mill';

const POLL_MS = 3000;

/** The control center: every run of a Mill task, wherever it executes, with its progress. */
export default function RunsPage() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get('/runs');
      setRuns(data.runs as RunSummary[]);
      setTotal(data.total as number);
      setProblem((data.problem as string | null | undefined) || null);
      setError(null);
    } catch (err) {
      setError(failureText(err));
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

  return (
    <TkPageWrapper>
      {error && <TkErrorAlert title="The runs could not be read">{error}</TkErrorAlert>}
      {problem && (
        <TkErrorAlert title="The runs are not being received">
          {problem}. The runs below are as they were when they were last received.
        </TkErrorAlert>
      )}
      <TkCard>
        <TkCardHeader className="flex flex-row items-center justify-between">
          <div>
            <TkCardTitle>Runs</TkCardTitle>
            <TkCardDescription>
              Every run of a task of the tasks repository, the newest first.
              {total > runs.length && ` Showing ${runs.length} of ${total}.`}
            </TkCardDescription>
          </div>
          <TkButton intent="ghost" onClick={load} className="gap-2">
            <RefreshCw className="w-4 h-4" />
            Refresh
          </TkButton>
        </TkCardHeader>
        <TkCardContent>
          {loaded && !runs.length && !error ? (
            <p className="text-sm text-muted-foreground py-6">
              No run yet. A run appears here when a task is started: by an agent, or with the command
              mill run, in the IDE or in any pod.
            </p>
          ) : (
            <TkTable>
              <TkTableHeader>
                <TkTableRow>
                  <TkTableHead>Task</TkTableHead>
                  <TkTableHead>State</TkTableHead>
                  <TkTableHead>Progress</TkTableHead>
                  <TkTableHead>Started</TkTableHead>
                  <TkTableHead>Runs on</TkTableHead>
                </TkTableRow>
              </TkTableHeader>
              <TkTableBody>
                {runs.map((run) => (
                  <TkTableRow key={run.id}>
                    <TkTableCell>
                      <Link className="font-medium underline" to={`/runs/${run.id}`}>
                        {run.task}
                      </Link>
                      <div className="text-xs text-muted-foreground">
                        {run.file}
                        {run.limit !== null && ` · first ${run.limit} items`}
                      </div>
                    </TkTableCell>
                    <TkTableCell>
                      <RunStateBadge state={run.state} />
                      {run.failures > 0 && (
                        <div className="text-xs text-muted-foreground mt-1">{run.failures} failed items</div>
                      )}
                    </TkTableCell>
                    <TkTableCell>
                      <StageBars stages={run.stages} failed={run.state === 'failed'} />
                    </TkTableCell>
                    <TkTableCell className="text-sm">{new Date(run.started_at).toLocaleString()}</TkTableCell>
                    <TkTableCell className="text-sm">{run.node || run.host || '-'}</TkTableCell>
                  </TkTableRow>
                ))}
              </TkTableBody>
            </TkTable>
          )}
        </TkCardContent>
      </TkCard>
    </TkPageWrapper>
  );
}
