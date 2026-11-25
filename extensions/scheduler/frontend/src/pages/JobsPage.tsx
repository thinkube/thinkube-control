/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { useCallback, useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { TkButton } from 'thinkube-style/components/buttons-badges';
import { TkCard, TkCardContent, TkCardDescription, TkCardHeader, TkCardTitle } from 'thinkube-style/components/cards-data';
import { TkErrorAlert } from 'thinkube-style/components/feedback';
import { TkPageWrapper } from 'thinkube-style/components/utilities';
import { TkTable, TkTableBody, TkTableCell, TkTableHead, TkTableHeader, TkTableRow } from 'thinkube-style/components/tables';
import api from '@/lib/axios';
import { failureText, gpuNeed, type Job } from '@/lib/scheduler';

const POLL_MS = 5000;

/** The jobs: what each one asks for, where it is, and what it waits for. */
export default function JobsPage() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [workerError, setWorkerError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get('/jobs');
      setJobs(data.jobs as Job[]);
      setWorkerError((data.worker?.error as string | null | undefined) || null);
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

  const stop = async (job: Job) => {
    try {
      await api.post(`/jobs/${job.id}/stop`);
      await load();
    } catch (err) {
      setError(failureText(err));
    }
  };

  return (
    <TkPageWrapper>
      {error && <TkErrorAlert title="The jobs could not be read">{error}</TkErrorAlert>}
      {workerError && <TkErrorAlert title="The scheduler could not follow the jobs">{workerError}</TkErrorAlert>}
      <TkCard>
        <TkCardHeader className="flex flex-row items-center justify-between">
          <div>
            <TkCardTitle>Jobs</TkCardTitle>
            <TkCardDescription>Every job given to the scheduler, the newest first.</TkCardDescription>
          </div>
          <TkButton intent="ghost" onClick={load} className="gap-2">
            <RefreshCw className="w-4 h-4" />
            Refresh
          </TkButton>
        </TkCardHeader>
        <TkCardContent>
          {loaded && !jobs.length && !error ? (
            <p className="text-sm text-muted-foreground py-6">No job yet.</p>
          ) : (
            <TkTable>
              <TkTableHeader>
                <TkTableRow>
                  <TkTableHead>Job</TkTableHead>
                  <TkTableHead>State</TkTableHead>
                  <TkTableHead>Node</TkTableHead>
                  <TkTableHead>Asks for</TkTableHead>
                  <TkTableHead>Priority</TkTableHead>
                  <TkTableHead></TkTableHead>
                </TkTableRow>
              </TkTableHeader>
              <TkTableBody>
                {jobs.map((job) => (
                  <TkTableRow key={job.id} data-testid={`job-${job.id}`}>
                    <TkTableCell>
                      <div className="font-medium">{job.name}</div>
                      <div className="text-xs text-muted-foreground">{job.owner}</div>
                    </TkTableCell>
                    <TkTableCell>
                      <div>{job.status}</div>
                      {job.waiting && <div className="text-xs text-muted-foreground">{job.waiting}</div>}
                      {job.error && <div className="text-xs text-destructive">{job.error}</div>}
                    </TkTableCell>
                    <TkTableCell>
                      <div>{job.node}</div>
                      {job.gpu_ids && job.gpu_ids.length > 0 && (
                        <div className="font-mono text-xs text-muted-foreground">{job.gpu_ids.join(', ')}</div>
                      )}
                    </TkTableCell>
                    <TkTableCell className="text-sm">
                      {gpuNeed(job)}; {job.memory_gb} GiB, {job.cpu_cores} cores
                    </TkTableCell>
                    <TkTableCell>{job.priority}</TkTableCell>
                    <TkTableCell>
                      {(job.status === 'waiting' || job.status === 'running') && (
                        <TkButton intent="ghost" onClick={() => stop(job)}>
                          Stop
                        </TkButton>
                      )}
                    </TkTableCell>
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
