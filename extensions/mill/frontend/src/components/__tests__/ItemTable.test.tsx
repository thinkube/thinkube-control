/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ItemTable, itemColumns, type Item } from '../ItemTable';

const items: Item[] = [
  { key: 2, lead: { '#': '2' }, fields: { input: { id: 2, text: ' b ' }, output: { id: 2, text: 'b' }, score: 0.5 } },
  { key: 1, lead: { '#': '1' }, fields: { input: { id: 1, text: ' a ', lang: 'en' }, output: 'skipped', score: 1 } },
];

describe('itemColumns', () => {
  it('gives each key of an object field a column, and a plain field one column', () => {
    expect(itemColumns(items)).toEqual([
      { field: 'input', key: 'id' },
      { field: 'input', key: 'text' },
      { field: 'input', key: 'lang' },
      { field: 'output', key: null },
      { field: 'output', key: 'id' },
      { field: 'output', key: 'text' },
      { field: 'score', key: null },
    ]);
  });
});

describe('ItemTable', () => {
  it('draws one row per item, with the fields grouped under their names', () => {
    render(<ItemTable items={items} />);
    const rows = screen.getAllByRole('row');
    // two header rows, then the items
    expect(rows).toHaveLength(4);
    expect(screen.getByRole('columnheader', { name: 'input' }).getAttribute('colspan')).toBe('3');
    expect(screen.getByRole('columnheader', { name: 'lang' })).toBeTruthy();
    expect(rows[2].textContent).toContain('0.5');
    expect(rows[3].textContent).toContain('skipped');
    expect(rows[3].textContent).toContain('en');
  });

  it('draws one header row when no field is an object', () => {
    render(<ItemTable items={[{ key: 1, lead: { '#': '1', error: 'no text' }, fields: { id: 9, why: 'empty' } }]} />);
    expect(screen.getAllByRole('row')).toHaveLength(2);
    expect(screen.getByRole('columnheader', { name: 'why' })).toBeTruthy();
    expect(screen.getByText('no text')).toBeTruthy();
  });
});
