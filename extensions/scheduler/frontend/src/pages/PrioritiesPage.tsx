/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { useCallback, useEffect, useState } from 'react';
import { TkCard, TkCardContent, TkCardDescription, TkCardHeader, TkCardTitle } from 'thinkube-style/components/cards-data';
import { TkErrorAlert } from 'thinkube-style/components/feedback';
import { TkPageWrapper } from 'thinkube-style/components/utilities';
import { TkTable, TkTableBody, TkTableCell, TkTableHead, TkTableHeader, TkTableRow } from 'thinkube-style/components/tables';
import api from '@/lib/axios';
import { failureText, PRIORITY_TEXT, type KindPriority } from '@/lib/scheduler';

/** The priority of each kind of work on the nodes, which a person sets. */
export default function PrioritiesPage() {
  const [kinds, setKinds] = useState<KindPriority[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get('/priorities');
      setKinds(data as KindPriority[]);
      setError(null);
    } catch (err) {
      setError(failureText(err));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const change = async (kind: string, priority: number) => {
    try {
      const { data } = await api.put('/priorities', { [kind]: priority });
      setKinds(data as KindPriority[]);
      setError(null);
    } catch (err) {
      setError(failureText(err));
    }
  };

  return (
    <TkPageWrapper>
      {error && <TkErrorAlert title="The priorities could not be read or changed">{error}</TkErrorAlert>}
      <TkCard>
        <TkCardHeader>
          <TkCardTitle>Priorities</TkCardTitle>
          <TkCardDescription>
            Work of a higher priority takes the GPUs it needs from work of a lower one; work of the same priority
            waits its turn. Each part of the platform reads its kind's priority when it gives the Scheduler work, so a
            change holds for the work given from now on.
          </TkCardDescription>
        </TkCardHeader>
        <TkCardContent>
          <div className="overflow-x-auto">
            <TkTable>
              <TkTableHeader>
                <TkTableRow>
                  <TkTableHead>Kind of work</TkTableHead>
                  <TkTableHead>Priority</TkTableHead>
                  <TkTableHead>Default</TkTableHead>
                  <TkTableHead>When work of a higher priority needs its GPUs</TkTableHead>
                </TkTableRow>
              </TkTableHeader>
              <TkTableBody>
                {kinds.map((k) => (
                  <TkTableRow key={k.kind} data-testid={`priority-${k.kind}`}>
                    <TkTableCell className="font-medium">{k.label}</TkTableCell>
                    <TkTableCell>
                      <select
                        aria-label={`Priority of ${k.label}`}
                        className="rounded-md border bg-background px-2 py-1 text-sm"
                        value={k.priority}
                        onChange={(e) => change(k.kind, Number(e.target.value))}
                      >
                        {[1, 2, 3, 4, 5].map((p) => (
                          <option key={p} value={p}>
                            {PRIORITY_TEXT[p]}
                          </option>
                        ))}
                      </select>
                    </TkTableCell>
                    <TkTableCell className="text-sm">{PRIORITY_TEXT[k.default]}</TkTableCell>
                    <TkTableCell className="text-sm text-muted-foreground">{k.gives_up}</TkTableCell>
                  </TkTableRow>
                ))}
              </TkTableBody>
            </TkTable>
          </div>
        </TkCardContent>
      </TkCard>
    </TkPageWrapper>
  );
}
