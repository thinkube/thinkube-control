/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Nvfp4Card } from '../Nvfp4Card';
import type { GpuNode, Run } from '@/lib/forge';

const node = (name: string, cc: string, nvfp4: boolean): GpuNode => ({
  name, gpu_count: 1, total_memory_gb: 120, ai_remaining_gb: 60, loaded_models: [], compute_capability: cc, nvfp4,
});
const run = { id: 'r1', node: 'tkspark', nvfp4: null } as unknown as Run;

describe('Nvfp4Card', () => {
  it('says no node computes NVFP4 when none is Blackwell', () => {
    render(<Nvfp4Card run={run} nodes={[node('tkgpu', '8.6', false)]} onChange={() => {}} />);
    expect(screen.getByText(/needs compute capability 10.0 or higher/)).toBeInTheDocument();
  });

  it('offers a Blackwell node and 512 calibration rows', () => {
    render(<Nvfp4Card run={run} nodes={[node('tkspark', '12.1', true), node('tkgpu', '8.6', false)]} onChange={() => {}} />);
    expect(screen.getByLabelText('Calibration rows')).toHaveValue(512);
    expect(screen.getByText('tkspark · 12.1')).toBeInTheDocument();
  });

  it('chooses a Blackwell node once the nodes arrive after it is drawn', () => {
    const { rerender } = render(<Nvfp4Card run={run} nodes={[]} onChange={() => {}} />);
    rerender(<Nvfp4Card run={run} nodes={[node('tkspark', '12.1', true)]} onChange={() => {}} />);
    expect(screen.getByRole('button', { name: /Export to NVFP4/ })).toBeEnabled();
  });

  it('shows what a failed export raised', () => {
    const failed = {
      ...run,
      nvfp4: { model_id: 'forge/x-nvfp4', calibration_rows: 512, node: 'tkspark', job_id: 'j', status: 'error',
               error: 'RuntimeError: Thinkube Control did not publish forge/x-nvfp4: 422' },
    } as unknown as Run;
    render(<Nvfp4Card run={failed} nodes={[node('tkspark', '12.1', true)]} onChange={() => {}} />);
    expect(screen.getByText(/did not publish forge\/x-nvfp4: 422/)).toBeInTheDocument();
  });
});
