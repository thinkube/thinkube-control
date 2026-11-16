/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { TkTable, TkTableBody, TkTableCell, TkTableHead, TkTableHeader, TkTableRow } from 'thinkube-style/components/tables';
import { cellText } from '@/lib/mill';

export interface Item {
  key: number;
  /** Columns shown before the item's fields, such as its number or its error. */
  lead: Record<string, string>;
  fields: Record<string, unknown>;
}

interface Column {
  field: string;
  /** The key inside the field, when the field is an object; null for the field itself. */
  key: string | null;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** The columns of a set of items: each field, or each key of a field that is an object, in the order first met. */
export function itemColumns(items: Item[]): Column[] {
  const fields: string[] = [];
  const keys: Record<string, string[]> = {};
  const plain: Record<string, boolean> = {};
  for (const item of items) {
    for (const [field, value] of Object.entries(item.fields)) {
      if (!fields.includes(field)) fields.push(field);
      if (isObject(value)) {
        keys[field] = keys[field] || [];
        for (const key of Object.keys(value)) if (!keys[field].includes(key)) keys[field].push(key);
      } else {
        plain[field] = true;
      }
    }
  }
  const columns: Column[] = [];
  for (const field of fields) {
    // A field that is an object in some items and a plain value in others keeps a column for the plain value
    if (plain[field] || !keys[field]) columns.push({ field, key: null });
    for (const key of keys[field] || []) columns.push({ field, key });
  }
  return columns;
}

function value(item: Item, column: Column): unknown {
  const v = item.fields[column.field];
  if (column.key === null) return isObject(v) ? undefined : v;
  return isObject(v) ? v[column.key] : undefined;
}

/** Items as a table: one row each, one column per field, and per key of a field that is an object. */
export function ItemTable({ items }: { items: Item[] }) {
  if (!items.length) return null;
  const lead = Object.keys(items[0].lead);
  const columns = itemColumns(items);
  const grouped = columns.some((c) => c.key !== null);
  const spans: { field: string; span: number }[] = [];
  for (const c of columns) {
    const last = spans[spans.length - 1];
    if (last && last.field === c.field) last.span += 1;
    else spans.push({ field: c.field, span: 1 });
  }
  return (
    <div className="overflow-x-auto">
      <TkTable>
        <TkTableHeader>
          {grouped && (
            <TkTableRow>
              {lead.map((name) => (
                <TkTableHead key={`g-${name}`} />
              ))}
              {spans.map((s, i) => (
                <TkTableHead key={`g-${i}`} colSpan={s.span} className="text-center border-l">
                  {s.field}
                </TkTableHead>
              ))}
            </TkTableRow>
          )}
          <TkTableRow>
            {lead.map((name) => (
              <TkTableHead key={`l-${name}`}>{name}</TkTableHead>
            ))}
            {columns.map((c, i) => (
              <TkTableHead key={`c-${i}`} className={i === 0 || columns[i - 1].field !== c.field ? 'border-l' : ''}>
                {c.key === null ? (grouped ? '' : c.field) : c.key}
              </TkTableHead>
            ))}
          </TkTableRow>
        </TkTableHeader>
        <TkTableBody>
          {items.map((item) => (
            <TkTableRow key={item.key}>
              {lead.map((name) => (
                <TkTableCell key={`l-${name}`} className="text-xs text-muted-foreground align-top whitespace-nowrap">
                  {item.lead[name]}
                </TkTableCell>
              ))}
              {columns.map((c, i) => (
                <TkTableCell
                  key={`c-${i}`}
                  className={`text-sm align-top ${i === 0 || columns[i - 1].field !== c.field ? 'border-l' : ''}`}
                >
                  <div className="max-w-[32rem] max-h-40 overflow-auto whitespace-pre-wrap break-words">
                    {cellText(value(item, c))}
                  </div>
                </TkTableCell>
              ))}
            </TkTableRow>
          ))}
        </TkTableBody>
      </TkTable>
    </div>
  );
}
