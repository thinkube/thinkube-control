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
import { failureText, gib, KIND_TEXT, type SchedulerNode } from '@/lib/scheduler';

const POLL_MS = 5000;

/** The nodes: each one's kind and memory, and its GPUs by id with what each holds now. */
export default function NodesPage() {
  const [nodes, setNodes] = useState<SchedulerNode[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get('/nodes');
      setNodes(data.nodes as SchedulerNode[]);
      setError(null);
    } catch (err) {
      setError(failureText(err));
    }
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

  return (
    <TkPageWrapper>
      {error && <TkErrorAlert title="The nodes could not be read">{error}</TkErrorAlert>}
      <div className="flex justify-end">
        <TkButton intent="ghost" onClick={load} className="gap-2">
          <RefreshCw className="w-4 h-4" />
          Refresh
        </TkButton>
      </div>
      {nodes.map((node) => (
        <TkCard key={node.name} data-testid={`node-${node.name}`}>
          <TkCardHeader>
            <TkCardTitle>{node.name}</TkCardTitle>
            <TkCardDescription>
              {node.kind && KIND_TEXT[node.kind]}
              {node.memory_total_bytes !== undefined && node.memory_available_bytes !== undefined &&
                `. Memory: ${gib(node.memory_available_bytes)} available of ${gib(node.memory_total_bytes)}.`}
            </TkCardDescription>
          </TkCardHeader>
          <TkCardContent>
            {node.error && <TkErrorAlert title="This node could not be read">{node.error}</TkErrorAlert>}
            {node.gpus && node.gpus.length > 0 && (
              <TkTable>
                <TkTableHeader>
                  <TkTableRow>
                    <TkTableHead>GPU</TkTableHead>
                    <TkTableHead>Id</TkTableHead>
                    <TkTableHead>Memory in use</TkTableHead>
                    <TkTableHead>Busy</TkTableHead>
                    <TkTableHead>Temperature</TkTableHead>
                    <TkTableHead>Power</TkTableHead>
                  </TkTableRow>
                </TkTableHeader>
                <TkTableBody>
                  {node.gpus.map((gpu) => (
                    <TkTableRow key={gpu.id}>
                      <TkTableCell>
                        {gpu.index}: {gpu.name}
                      </TkTableCell>
                      <TkTableCell className="font-mono text-xs">{gpu.id}</TkTableCell>
                      <TkTableCell>
                        {gpu.memory_total_bytes !== undefined && gpu.memory_used_bytes !== undefined
                          ? `${gib(gpu.memory_used_bytes)} of ${gib(gpu.memory_total_bytes)}`
                          : "the node's memory"}
                      </TkTableCell>
                      <TkTableCell>{gpu.utilization.toFixed(0)}%</TkTableCell>
                      <TkTableCell>{gpu.temperature.toFixed(0)} °C</TkTableCell>
                      <TkTableCell>{gpu.power.toFixed(0)} W</TkTableCell>
                    </TkTableRow>
                  ))}
                </TkTableBody>
              </TkTable>
            )}
          </TkCardContent>
        </TkCard>
      ))}
    </TkPageWrapper>
  );
}
