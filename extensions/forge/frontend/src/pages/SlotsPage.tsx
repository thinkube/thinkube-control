/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useCallback, useEffect, useState } from 'react';
import { Pencil, Plug, Plus, Trash2, Unplug } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { TkBadge, TkButton, TkLoadingButton } from 'thinkube-style/components/buttons-badges';
import { TkCard, TkCardContent, TkCardDescription, TkCardHeader, TkCardTitle } from 'thinkube-style/components/cards-data';
import { TkErrorAlert, TkInfoAlert, TkLoader, TkWarningAlert } from 'thinkube-style/components/feedback';
import {
  TkCheckbox,
  TkInput,
  TkLabel,
  TkSelect,
  TkSelectContent,
  TkSelectItem,
  TkSelectTrigger,
  TkSelectValue,
} from 'thinkube-style/components/forms-inputs';
import { TkControlledConfirmDialog } from 'thinkube-style/components/modals-overlays';
import { TkTable, TkTableBody, TkTableCell, TkTableHead, TkTableHeader, TkTableRow } from 'thinkube-style/components/tables';
import { TkPageWrapper } from 'thinkube-style/components/utilities';
import { failure, forge, type GpuNode, type NewSlot, type Slot, type WorkerStatus } from '@/lib/forge';

const DAYS = [0, 1, 2, 3, 4, 5, 6];
const EMPTY: NewSlot = { node: '', days: [0, 1, 2, 3, 4], start: '23:00', end: '07:00', timezone: 'Europe/Madrid' };

function windowText(start: string, end: string): string {
  const options: Intl.DateTimeFormatOptions = { weekday: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' };
  return `${new Date(start).toLocaleString(undefined, options)} – ${new Date(end).toLocaleString(undefined, options)}`;
}

function WorkerCard({ status, onChange }: { status: WorkerStatus | null; onChange: (s: WorkerStatus) => void }) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(false);

  const change = async (connect: boolean) => {
    setBusy(true);
    try {
      onChange(connect ? await forge.connectWorker() : await forge.disconnectWorker());
    } catch (e) {
      toast.error(failure(e));
    } finally {
      setBusy(false);
      setConfirm(false);
    }
  };

  return (
    <TkCard>
      <TkCardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <TkCardTitle>{t('slots.worker')}</TkCardTitle>
          {status && (
            <TkBadge status={status.connected ? 'healthy' : 'warning'}>
              {status.connected ? t('slots.connected') : t('slots.notConnected')}
            </TkBadge>
          )}
        </div>
        <TkCardDescription>{t('slots.workerHelp')}</TkCardDescription>
      </TkCardHeader>
      <TkCardContent className="space-y-3">
        {!status ? (
          <TkLoader />
        ) : status.connected ? (
          <>
            <p className="text-sm">
              {t('slots.connectedAs', { user: status.username, when: status.connected_at ? new Date(status.connected_at).toLocaleString() : '' })}
            </p>
            {status.last_tick && (
              <p className="text-xs text-muted-foreground">{t('slots.lastTick', { when: new Date(status.last_tick).toLocaleString() })}</p>
            )}
            {status.error && <TkWarningAlert>{status.error}</TkWarningAlert>}
            <TkButton intent="secondary" onClick={() => setConfirm(true)}>
              <Unplug className="h-4 w-4" />
              {t('slots.disconnect')}
            </TkButton>
          </>
        ) : (
          <TkLoadingButton loading={busy} onClick={() => change(true)}>
            <Plug className="h-4 w-4" />
            {t('slots.connect')}
          </TkLoadingButton>
        )}
      </TkCardContent>
      <TkControlledConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title={t('slots.disconnect')}
        description={t('slots.disconnectConfirm')}
        confirmText={t('slots.disconnect')}
        cancelText={t('common.close')}
        variant="destructive"
        onConfirm={() => change(false)}
      />
    </TkCard>
  );
}

export default function SlotsPage() {
  const { t } = useTranslation();
  const [slots, setSlots] = useState<Slot[] | null>(null);
  const [nodes, setNodes] = useState<GpuNode[]>([]);
  const [worker, setWorker] = useState<WorkerStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState<NewSlot>(EMPTY);
  const [editing, setEditing] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<Slot | null>(null);

  const load = useCallback(() => {
    Promise.all([forge.slots(), forge.worker()])
      .then(([s, w]) => {
        setSlots(s);
        setWorker(w);
        setError(null);
      })
      .catch((e) => setError(failure(e)));
  }, []);

  useEffect(() => {
    load();
    forge.nodes().then(setNodes).catch((e) => setError(failure(e)));
  }, [load]);

  const toggleDay = (day: number, on: boolean) =>
    setForm({ ...form, days: on ? [...form.days, day].sort() : form.days.filter((d) => d !== day) });

  const save = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      if (editing) await forge.updateSlot(editing, form);
      else await forge.createSlot(form);
      setForm({ ...EMPTY, node: form.node });
      setEditing(null);
      load();
    } catch (e) {
      setSaveError(failure(e));
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (!deleting) return;
    try {
      await forge.deleteSlot(deleting.id);
      load();
    } catch (e) {
      toast.error(failure(e));
    } finally {
      setDeleting(null);
    }
  };

  const dayNames = (days: number[]) => days.map((d) => t(`slots.day.${d}`)).join(', ');

  return (
    <TkPageWrapper>
      <div>
        <h1 className="text-2xl font-bold">{t('slots.title')}</h1>
        <p className="text-muted-foreground mt-1">{t('slots.subtitle')}</p>
      </div>

      {error && <TkErrorAlert title={t('slots.loadFailed')}>{error}</TkErrorAlert>}

      <WorkerCard status={worker} onChange={setWorker} />

      <TkCard>
        <TkCardHeader>
          <TkCardTitle>{t('slots.listTitle')}</TkCardTitle>
          <TkCardDescription>{t('slots.listHelp')}</TkCardDescription>
        </TkCardHeader>
        <TkCardContent className="space-y-3">
          {slots === null ? (
            <TkLoader />
          ) : slots.length === 0 ? (
            <TkInfoAlert>{t('slots.none')}</TkInfoAlert>
          ) : (
            <div className="overflow-x-auto">
              <TkTable>
                <TkTableHeader>
                  <TkTableRow>
                    <TkTableHead>{t('slots.node')}</TkTableHead>
                    <TkTableHead>{t('slots.days')}</TkTableHead>
                    <TkTableHead>{t('slots.hours')}</TkTableHead>
                    <TkTableHead>{t('slots.next')}</TkTableHead>
                    <TkTableHead>
                      <span className="sr-only">{t('queue.actions')}</span>
                    </TkTableHead>
                  </TkTableRow>
                </TkTableHeader>
                <TkTableBody>
                  {slots.map((s) => (
                    <TkTableRow key={s.id}>
                      <TkTableCell className="font-medium">{s.node}</TkTableCell>
                      <TkTableCell className="text-sm">{dayNames(s.days)}</TkTableCell>
                      <TkTableCell className="text-sm">
                        {s.start} – {s.end}
                        <div className="text-xs text-muted-foreground">{s.timezone}</div>
                      </TkTableCell>
                      <TkTableCell className="text-sm">
                        {s.next_window ? windowText(s.next_window.start, s.next_window.end) : '–'}
                        {s.overlaps.length > 0 && (
                          <div className="text-xs text-warning">
                            {t('slots.overlaps', { nodes: [...new Set(s.overlaps.map((o) => o.node))].join(', ') })}
                          </div>
                        )}
                      </TkTableCell>
                      <TkTableCell>
                        <div className="flex justify-end gap-1">
                          <TkButton
                            intent="ghost"
                            size="icon"
                            aria-label={t('slots.edit', { node: s.node })}
                            onClick={() => {
                              setEditing(s.id);
                              setForm({ node: s.node, days: s.days, start: s.start, end: s.end, timezone: s.timezone });
                            }}
                          >
                            <Pencil className="h-4 w-4" />
                          </TkButton>
                          <TkButton intent="ghost" size="icon" aria-label={t('slots.delete', { node: s.node })} onClick={() => setDeleting(s)}>
                            <Trash2 className="h-4 w-4" />
                          </TkButton>
                        </div>
                      </TkTableCell>
                    </TkTableRow>
                  ))}
                </TkTableBody>
              </TkTable>
            </div>
          )}
          <p className="text-xs text-muted-foreground">{t('slots.overlapRule')}</p>
        </TkCardContent>
      </TkCard>

      <TkCard>
        <TkCardHeader>
          <TkCardTitle>{editing ? t('slots.editTitle') : t('slots.addTitle')}</TkCardTitle>
          <TkCardDescription>{t('slots.addHelp')}</TkCardDescription>
        </TkCardHeader>
        <TkCardContent className="grid gap-4 md:grid-cols-2">
          <div className="space-y-1">
            <TkLabel>{t('slots.node')}</TkLabel>
            <TkSelect value={form.node} onValueChange={(node) => setForm({ ...form, node })}>
              <TkSelectTrigger>
                <TkSelectValue placeholder={t('newRun.pickNode')} />
              </TkSelectTrigger>
              <TkSelectContent>
                {nodes.map((n) => (
                  <TkSelectItem key={n.name} value={n.name}>
                    {n.name}
                  </TkSelectItem>
                ))}
              </TkSelectContent>
            </TkSelect>
          </div>
          <div className="space-y-1">
            <TkLabel htmlFor="slot-tz">{t('slots.timezone')}</TkLabel>
            <TkInput id="slot-tz" value={form.timezone} onChange={(e) => setForm({ ...form, timezone: e.target.value })} />
          </div>
          <div className="space-y-2 md:col-span-2">
            <TkLabel>{t('slots.days')}</TkLabel>
            <div className="flex flex-wrap gap-4">
              {DAYS.map((d) => (
                <label key={d} className="flex items-center gap-2 text-sm">
                  <TkCheckbox checked={form.days.includes(d)} onCheckedChange={(on) => toggleDay(d, on === true)} />
                  {t(`slots.day.${d}`)}
                </label>
              ))}
            </div>
          </div>
          <div className="space-y-1">
            <TkLabel htmlFor="slot-start">{t('slots.start')}</TkLabel>
            <TkInput id="slot-start" type="time" value={form.start} onChange={(e) => setForm({ ...form, start: e.target.value })} />
          </div>
          <div className="space-y-1">
            <TkLabel htmlFor="slot-end">{t('slots.end')}</TkLabel>
            <TkInput id="slot-end" type="time" value={form.end} onChange={(e) => setForm({ ...form, end: e.target.value })} />
            <p className="text-xs text-muted-foreground">{t('slots.endHelp')}</p>
          </div>
          {saveError && (
            <div className="md:col-span-2">
              <TkErrorAlert>{saveError}</TkErrorAlert>
            </div>
          )}
          <div className="flex gap-2 md:col-span-2">
            <TkLoadingButton loading={saving} disabled={!form.node || form.days.length === 0} onClick={save}>
              <Plus className="h-4 w-4" />
              {editing ? t('common.save') : t('slots.add')}
            </TkLoadingButton>
            {editing && (
              <TkButton
                intent="ghost"
                onClick={() => {
                  setEditing(null);
                  setForm(EMPTY);
                }}
              >
                {t('common.cancel')}
              </TkButton>
            )}
          </div>
        </TkCardContent>
      </TkCard>

      <TkControlledConfirmDialog
        open={deleting !== null}
        onOpenChange={(open) => !open && setDeleting(null)}
        title={t('slots.deleteTitle')}
        description={t('slots.deleteConfirm')}
        confirmText={t('common.delete')}
        cancelText={t('common.close')}
        variant="destructive"
        onConfirm={remove}
      />
    </TkPageWrapper>
  );
}
