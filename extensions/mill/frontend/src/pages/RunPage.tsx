/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { useCallback, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ArrowLeft, Square } from 'lucide-react';
import { TkButton } from 'thinkube-style/components/buttons-badges';
import { TkCard, TkCardContent, TkCardDescription, TkCardHeader, TkCardTitle } from 'thinkube-style/components/cards-data';
import { TkErrorAlert } from 'thinkube-style/components/feedback';
import { TkPageWrapper } from 'thinkube-style/components/utilities';
import { TkTable, TkTableBody, TkTableCell, TkTableHead, TkTableHeader, TkTableRow } from 'thinkube-style/components/tables';
import api from '@/lib/axios';
import { RunStateBadge } from '@/components/RunStateBadge';
import { StageBars } from '@/components/StageBars';
import { ItemTable } from '@/components/ItemTable';
import { duration, failureText, type Failure, type Port, type RunDetail, type Sample } from '@/lib/mill';

const POLL_MS = 2000;
const LOG_LINES = 200;

function Ports({ title, ports }: { title: string; ports: Record<string, Port> | undefined }) {
  const names = Object.keys(ports || {});
  if (!ports || !names.length) return null;
  return (
    <div>
      <div className="text-xs font-medium text-muted-foreground">{title}</div>
      {names.map((name) => (
        <div key={name} className="text-sm">
          <span className="font-medium">{name}</span> <span className="text-muted-foreground">({ports[name].type})</span>{' '}
          <code className="text-xs">{ports[name].path}</code>
        </div>
      ))}
    </div>
  );
}

/** One run: its progress, live samples, running totals, failures and log. */
export default function RunPage() {
  const { id } = useParams<{ id: string }>();
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [samples, setSamples] = useState<{ latest: Sample[]; spaced: Sample[] }>({ latest: [], spaced: [] });
  const [failures, setFailures] = useState<Failure[]>([]);
  const [log, setLog] = useState<string[]>([]);
  const [view, setView] = useState<'latest' | 'spaced'>('latest');
  const [error, setError] = useState<string | null>(null);
  const [stopping, setStopping] = useState(false);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get(`/runs/${id}`);
      const run = data as RunDetail;
      setDetail(run);
      const kept = await api.get(`/runs/${id}/samples`);
      setSamples(kept.data as { latest: Sample[]; spaced: Sample[] });
      if (run.status.failures > 0) {
        const failed = await api.get(`/runs/${id}/failures`);
        setFailures(failed.data.failures as Failure[]);
      }
      if (run.has_log) {
        const printed = await api.get(`/runs/${id}/log`, { params: { lines: LOG_LINES } });
        setLog(printed.data.lines as string[]);
      }
      setError(null);
      return run.status.state;
    } catch (err) {
      setError(failureText(err));
      return null;
    }
  }, [id]);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let gone = false;
    const follow = async () => {
      const state = await load();
      // A run that ended does not change any more
      if (!gone && (state === 'running' || state === null)) timer = setTimeout(follow, POLL_MS);
    };
    follow();
    return () => {
      gone = true;
      if (timer) clearTimeout(timer);
    };
  }, [load]);

  const stop = async () => {
    setStopping(true);
    try {
      await api.post(`/runs/${id}/stop`);
      await load();
    } catch (err) {
      setError(failureText(err));
    } finally {
      setStopping(false);
    }
  };

  if (!detail) {
    return (
      <TkPageWrapper>
        {error ? <TkErrorAlert title="The run could not be read">{error}</TkErrorAlert> : <p className="text-sm text-muted-foreground">Loading…</p>}
      </TkPageWrapper>
    );
  }

  const { run, status } = detail;
  const started = new Date(status.started_at).getTime();
  const until = status.ended_at ? new Date(status.ended_at).getTime() : Date.now();
  const shown = view === 'latest' ? [...samples.latest].reverse() : samples.spaced;
  const counters = Object.entries(status.counters);
  const measures = Object.entries(status.measures);

  return (
    <TkPageWrapper>
      <Link to="/" className="text-sm text-muted-foreground inline-flex items-center gap-1">
        <ArrowLeft className="w-4 h-4" />
        Runs
      </Link>

      {error && <TkErrorAlert title="Error">{error}</TkErrorAlert>}
      {status.error && <TkErrorAlert title={status.state === 'stopped' ? 'Stopped' : 'The run failed'}>{status.error}</TkErrorAlert>}

      <TkCard>
        <TkCardHeader className="flex flex-row items-start justify-between gap-4">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <TkCardTitle>{run.task}</TkCardTitle>
              <RunStateBadge state={status.state} />
            </div>
            <TkCardDescription>
              {run.description || run.file}
            </TkCardDescription>
          </div>
          {detail.can_stop && (
            <TkButton intent="secondary" onClick={stop} disabled={stopping} className="gap-2">
              <Square className="w-4 h-4" />
              Stop
            </TkButton>
          )}
        </TkCardHeader>
        <TkCardContent className="space-y-4">
          <StageBars stages={status.stages} failed={status.state === 'failed'} />
          <div className="grid grid-cols-[repeat(auto-fill,minmax(14rem,1fr))] gap-4 text-sm">
            <div>
              <div className="text-xs font-medium text-muted-foreground">Run</div>
              <code className="text-xs break-all">{run.id}</code>
            </div>
            <div>
              <div className="text-xs font-medium text-muted-foreground">File</div>
              {run.file}
              {run.limit != null && ` · first ${run.limit} items`}
            </div>
            <div>
              <div className="text-xs font-medium text-muted-foreground">Time</div>
              {duration((until - started) / 1000)} · started {new Date(status.started_at).toLocaleString()}
            </div>
            <div>
              <div className="text-xs font-medium text-muted-foreground">Runs on</div>
              {run.node || run.host || '-'}
            </div>
            <div>
              <div className="text-xs font-medium text-muted-foreground">Commit</div>
              {run.repository?.commit ? (
                <>
                  <code className="text-xs">{run.repository.commit.slice(0, 10)}</code>
                  {run.repository.dirty && ' with changes not committed'}
                </>
              ) : (
                'not in a repository'
              )}
            </div>
          </div>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(18rem,1fr))] gap-4">
            <Ports title="Inputs" ports={run.inputs} />
            <Ports title="Outputs" ports={run.outputs} />
            {run.settings && Object.keys(run.settings).length > 0 && (
              <div>
                <div className="text-xs font-medium text-muted-foreground">Settings</div>
                {Object.entries(run.settings).map(([name, value]) => (
                  <div key={name} className="text-sm">
                    <span className="font-medium">{name}</span> <code className="text-xs">{JSON.stringify(value)}</code>
                  </div>
                ))}
              </div>
            )}
          </div>
        </TkCardContent>
      </TkCard>

      {(counters.length > 0 || measures.length > 0) && (
        <TkCard>
          <TkCardHeader>
            <TkCardTitle>Running totals</TkCardTitle>
          </TkCardHeader>
          <TkCardContent>
            <TkTable>
              <TkTableHeader>
                <TkTableRow>
                  <TkTableHead>Name</TkTableHead>
                  <TkTableHead>Count</TkTableHead>
                  <TkTableHead>Mean</TkTableHead>
                  <TkTableHead>Least</TkTableHead>
                  <TkTableHead>Most</TkTableHead>
                </TkTableRow>
              </TkTableHeader>
              <TkTableBody>
                {counters.map(([name, value]) => (
                  <TkTableRow key={`c-${name}`}>
                    <TkTableCell>{name}</TkTableCell>
                    <TkTableCell>{value}</TkTableCell>
                    <TkTableCell />
                    <TkTableCell />
                    <TkTableCell />
                  </TkTableRow>
                ))}
                {measures.map(([name, m]) => (
                  <TkTableRow key={`m-${name}`}>
                    <TkTableCell>{name}</TkTableCell>
                    <TkTableCell>{m.count}</TkTableCell>
                    <TkTableCell>{(m.sum / m.count).toFixed(3)}</TkTableCell>
                    <TkTableCell>{m.min}</TkTableCell>
                    <TkTableCell>{m.max}</TkTableCell>
                  </TkTableRow>
                ))}
              </TkTableBody>
            </TkTable>
          </TkCardContent>
        </TkCard>
      )}

      {failures.length > 0 && (
        <TkCard>
          <TkCardHeader>
            <TkCardTitle>Failed items</TkCardTitle>
            <TkCardDescription>
              {status.failures} failed. The first {failures.length} are shown.
            </TkCardDescription>
          </TkCardHeader>
          <TkCardContent>
            <ItemTable
              items={failures.map((f) => ({ key: f.n, lead: { '#': String(f.n), error: f.error }, fields: f.fields }))}
            />
          </TkCardContent>
        </TkCard>
      )}

      <TkCard>
        <TkCardHeader className="flex flex-row items-center justify-between">
          <div>
            <TkCardTitle>Samples</TkCardTitle>
            <TkCardDescription>
              {status.samples} items sampled.{' '}
              {view === 'latest' ? 'The latest ones, newest first.' : 'Ones spaced across the run, in order.'}
            </TkCardDescription>
          </div>
          <div className="flex gap-2">
            <TkButton intent={view === 'latest' ? 'secondary' : 'ghost'} size="sm" onClick={() => setView('latest')}>
              Latest
            </TkButton>
            <TkButton intent={view === 'spaced' ? 'secondary' : 'ghost'} size="sm" onClick={() => setView('spaced')}>
              Across the run
            </TkButton>
          </div>
        </TkCardHeader>
        <TkCardContent>
          {!shown.length && <p className="text-sm text-muted-foreground">No sample yet.</p>}
          <ItemTable items={shown.map((sample) => ({ key: sample.n, lead: { '#': String(sample.n) }, fields: sample.fields }))} />
        </TkCardContent>
      </TkCard>

      <TkCard>
        <TkCardHeader>
          <TkCardTitle>Log</TkCardTitle>
          <TkCardDescription>
            {detail.has_log ? `The last ${LOG_LINES} lines the run printed.` : 'The run has printed nothing.'}
          </TkCardDescription>
        </TkCardHeader>
        {detail.has_log && (
          <TkCardContent>
            <pre className="text-xs whitespace-pre-wrap break-words rounded bg-muted p-3 max-h-96 overflow-auto">
              {log.join('\n')}
            </pre>
          </TkCardContent>
        )}
      </TkCard>
    </TkPageWrapper>
  );
}
