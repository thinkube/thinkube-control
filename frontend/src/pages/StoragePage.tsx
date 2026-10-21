/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState, useEffect, useCallback, type ChangeEvent } from 'react';
import { TkCard, TkCardContent, TkCardDescription, TkCardHeader, TkCardTitle, TkStatCard } from 'thinkube-style/components/cards-data';
import { TkButton, TkBadge } from 'thinkube-style/components/buttons-badges';
import { TkErrorAlert, TkSuccessAlert } from 'thinkube-style/components/feedback';
import { TkInput, TkLabel } from 'thinkube-style/components/forms-inputs';
import {
  TkDialogRoot,
  TkDialogContent,
  TkDialogHeader,
  TkDialogTitle,
  TkDialogFooter,
} from 'thinkube-style/components/modals-overlays';
import { TkPageWrapper } from 'thinkube-style/components/utilities';
import {
  TkTable,
  TkTableBody,
  TkTableCell,
  TkTableHead,
  TkTableHeader,
  TkTableRow,
} from 'thinkube-style/components/tables';
import { HardDrive, Database, FolderOpen, Folder, File, Loader2, Trash2, Pencil, RefreshCw, ChevronRight } from 'lucide-react';
import api from '@/lib/axios';

interface SharedFolder {
  name: string;
  path: string;
  bytes: number;
  dirs: number;
  files: number;
  quota_bytes: number | null;
  claims: string[];
  volumes: string[];
  quota_at_next_mount_bytes: number | null;
}

interface FileSystem {
  name: string;
  used_bytes: number;
  trash_days: number;
  folders: SharedFolder[];
}

interface BackingDisk {
  server: string;
  node: string;
  dir: string;
  total_bytes: number;
  used_bytes: number;
  free_bytes: number;
}

interface Entry {
  name: string;
  path: string;
  is_dir: boolean;
  bytes: number;
  dirs: number;
  files: number;
}

interface NodeDisk {
  source: string;
  fstype: string;
  mount: string;
  total_bytes: number;
  used_bytes: number;
  free_bytes: number;
}

interface NodeStorage {
  node: string;
  error?: string;
  disks?: NodeDisk[];
  df_error?: string | null;
  training?: {
    path: string;
    exists: boolean;
    bytes?: number | null;
    entries?: { path: string; bytes: number }[];
    error?: string | null;
  };
}

interface Claim {
  namespace: string;
  name: string;
  storage_class: string | null;
  volume: string | null;
  phase: string;
  requested_bytes: number | null;
  capacity_bytes: number | null;
  used_bytes: number | null;
}

const GIB = 1024 ** 3;

function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return '-';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB'];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit++;
  }
  return `${value.toFixed(unit < 3 ? 0 : 1)} ${units[unit]}`;
}

function errorText(err: any): string {
  return err?.response?.data?.detail || err?.message || String(err);
}

function UsageBar({ used, total }: { used: number; total: number | null }) {
  if (!total) return <span className="text-xs text-muted-foreground">no limit</span>;
  const pct = Math.min(100, (used / total) * 100);
  const color = pct >= 95 ? 'bg-red-500' : pct >= 80 ? 'bg-amber-500' : 'bg-primary';
  return (
    <div className="min-w-[8rem]">
      <div className="h-2 w-full rounded bg-muted overflow-hidden">
        <div className={`h-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <div className="text-xs text-muted-foreground mt-1">
        {formatBytes(used)} of {formatBytes(total)} ({pct.toFixed(0)}%)
      </div>
    </div>
  );
}

function folderLabel(folder: SharedFolder): string {
  return folder.claims.length ? folder.claims.join(', ') : folder.name;
}

export default function StoragePage() {
  const [filesystems, setFilesystems] = useState<FileSystem[]>([]);
  const [disks, setDisks] = useState<BackingDisk[]>([]);
  const [nodes, setNodes] = useState<NodeStorage[]>([]);
  const [claims, setClaims] = useState<Claim[]>([]);
  const [claimErrors, setClaimErrors] = useState<string[]>([]);
  const [loading, setLoading] = useState({ shared: true, nodes: true, claims: true });
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [browse, setBrowse] = useState<{ filesystem: string; path: string } | null>(null);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [browseLoading, setBrowseLoading] = useState(false);

  const [quotaTarget, setQuotaTarget] = useState<{ filesystem: string; folder: SharedFolder } | null>(null);
  const [quotaGib, setQuotaGib] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<{ filesystem: string; entry: Entry } | null>(null);
  const [working, setWorking] = useState(false);

  const loadShared = useCallback(async () => {
    setLoading(l => ({ ...l, shared: true }));
    try {
      const { data } = await api.get('/storage', { timeout: 120000 });
      setFilesystems(data.filesystems);
      setDisks(data.disks);
    } catch (err) {
      setError(`Shared folders: ${errorText(err)}`);
    } finally {
      setLoading(l => ({ ...l, shared: false }));
    }
  }, []);

  const loadNodes = useCallback(async () => {
    setLoading(l => ({ ...l, nodes: true }));
    try {
      const { data } = await api.get('/storage/nodes', { timeout: 150000 });
      setNodes(data.nodes);
    } catch (err) {
      setError(`Node disks: ${errorText(err)}`);
    } finally {
      setLoading(l => ({ ...l, nodes: false }));
    }
  }, []);

  const loadClaims = useCallback(async () => {
    setLoading(l => ({ ...l, claims: true }));
    try {
      const { data } = await api.get('/storage/claims', { timeout: 60000 });
      setClaims(data.claims);
      setClaimErrors(data.errors);
    } catch (err) {
      setError(`Volume claims: ${errorText(err)}`);
    } finally {
      setLoading(l => ({ ...l, claims: false }));
    }
  }, []);

  const loadAll = useCallback(() => {
    setError(null);
    loadShared();
    loadNodes();
    loadClaims();
  }, [loadShared, loadNodes, loadClaims]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const openFolder = useCallback(async (filesystem: string, path: string) => {
    setBrowse({ filesystem, path });
    setBrowseLoading(true);
    try {
      const { data } = await api.get('/storage/folder', { params: { filesystem, path }, timeout: 120000 });
      setEntries(data.entries);
    } catch (err) {
      setEntries([]);
      setError(`${filesystem}:/${path}: ${errorText(err)}`);
    } finally {
      setBrowseLoading(false);
    }
  }, []);

  const saveQuota = async () => {
    if (!quotaTarget) return;
    setWorking(true);
    try {
      const { data } = await api.post('/storage/quota', {
        filesystem: quotaTarget.filesystem,
        folder: quotaTarget.folder.name,
        size_gib: Number(quotaGib),
      }, { timeout: 120000 });
      setNotice(`Quota of ${folderLabel(quotaTarget.folder)} is now ${formatBytes(data.quota_bytes)}.`);
      setQuotaTarget(null);
      loadShared();
    } catch (err) {
      setError(`Quota: ${errorText(err)}`);
    } finally {
      setWorking(false);
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    setWorking(true);
    try {
      const { data } = await api.post('/storage/delete', {
        filesystem: deleteTarget.filesystem,
        path: deleteTarget.entry.path,
      }, { timeout: 620000 });
      setNotice(`Deleted ${deleteTarget.entry.path} (${formatBytes(data.freed_bytes)}). The folder's quota is free now; ` +
        `the disk under it after ${data.trash_days} day(s) in the trash.`);
      setDeleteTarget(null);
      if (browse) openFolder(browse.filesystem, browse.path);
      loadShared();
    } catch (err) {
      setError(`Delete: ${errorText(err)}`);
    } finally {
      setWorking(false);
    }
  };

  const diskTotal = disks.reduce((s, d) => s + d.total_bytes, 0);
  const diskFree = disks.reduce((s, d) => s + d.free_bytes, 0);
  const quotaSum = filesystems.flatMap(f => f.folders).reduce((s, f) => s + (f.quota_bytes || 0), 0);
  const browseParts = browse ? browse.path.split('/').filter(Boolean) : [];
  const browseFs = browse ? filesystems.find(f => f.name === browse.filesystem) : undefined;
  const browseTop = browseFs?.folders.find(f => f.name === browseParts[0]);

  return (
    <TkPageWrapper>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(12rem,1fr))] gap-4">
        {filesystems.map(fs => (
          <TkStatCard
            key={fs.name}
            title={fs.name}
            value={formatBytes(fs.used_bytes)}
            description={`${fs.folders.length} folders`}
            icon={Database}
            variant="primary"
          />
        ))}
        <TkStatCard
          title="Disk under shared folders"
          value={disks.length ? formatBytes(diskFree) : '-'}
          description={disks.length ? `free of ${formatBytes(diskTotal)} (SeaweedFS)` : 'loading'}
          icon={HardDrive}
          variant="primary"
        />
        <TkStatCard
          title="All quotas together"
          value={formatBytes(quotaSum)}
          description={quotaSum > diskTotal && diskTotal ? 'more than the disk holds' : 'within the disk'}
          icon={HardDrive}
          variant="primary"
        />
      </div>

      {error && (
        <TkErrorAlert title="Error">
          <div className="flex items-start justify-between gap-4">
            <span>{error}</span>
            <TkButton intent="ghost" size="sm" onClick={() => setError(null)}>Dismiss</TkButton>
          </div>
        </TkErrorAlert>
      )}
      {notice && (
        <TkSuccessAlert title="Done">
          <div className="flex items-start justify-between gap-4">
            <span>{notice}</span>
            <TkButton intent="ghost" size="sm" onClick={() => setNotice(null)}>Dismiss</TkButton>
          </div>
        </TkSuccessAlert>
      )}

      {/* Shared folders */}
      <TkCard>
        <TkCardHeader className="flex flex-row items-center justify-between">
          <div>
            <TkCardTitle>Shared folders</TkCardTitle>
            <TkCardDescription>
              JuiceFS folders behind the volumes. Each quota is a limit on the folder; all of them share the disk under them.
            </TkCardDescription>
          </div>
          <TkButton intent="ghost" onClick={loadAll} className="gap-2">
            <RefreshCw className="w-4 h-4" />
            Refresh
          </TkButton>
        </TkCardHeader>
        <TkCardContent className="space-y-6">
          {loading.shared && !filesystems.length ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="w-6 h-6 animate-spin" />
              <span className="ml-2">Measuring shared folders...</span>
            </div>
          ) : filesystems.map(fs => (
            <div key={fs.name} className="space-y-2">
              <div className="flex items-center gap-2">
                <h3 className="font-semibold">{fs.name}</h3>
                <TkBadge appearance="outlined">deleted files kept {fs.trash_days} day(s) in trash</TkBadge>
              </div>
              <div className="overflow-x-auto rounded-lg border">
                <TkTable>
                  <TkTableHeader>
                    <TkTableRow>
                      <TkTableHead className="font-semibold">Folder</TkTableHead>
                      <TkTableHead className="font-semibold">Size</TkTableHead>
                      <TkTableHead className="font-semibold">Quota</TkTableHead>
                      <TkTableHead className="font-semibold">Files</TkTableHead>
                      <TkTableHead className="font-semibold">Actions</TkTableHead>
                    </TkTableRow>
                  </TkTableHeader>
                  <TkTableBody>
                    {fs.folders.map(folder => (
                      <TkTableRow key={folder.name}>
                        <TkTableCell>
                          <div className="font-medium">{folderLabel(folder)}</div>
                          <div className="text-xs text-muted-foreground font-mono">{folder.name}</div>
                          {folder.quota_at_next_mount_bytes !== null && folder.quota_bytes !== null &&
                            folder.quota_at_next_mount_bytes !== folder.quota_bytes && (
                            <div className="text-xs text-amber-600">
                              Its volume says {formatBytes(folder.quota_at_next_mount_bytes)}: the next mount sets the quota to that.
                            </div>
                          )}
                        </TkTableCell>
                        <TkTableCell>{formatBytes(folder.bytes)}</TkTableCell>
                        <TkTableCell><UsageBar used={folder.bytes} total={folder.quota_bytes} /></TkTableCell>
                        <TkTableCell className="text-xs text-muted-foreground">
                          {folder.files} files, {folder.dirs} folders
                        </TkTableCell>
                        <TkTableCell>
                          <div className="flex gap-1">
                            <TkButton intent="ghost" size="sm" className="gap-1"
                              onClick={() => openFolder(fs.name, folder.name)}>
                              <FolderOpen className="w-4 h-4" />
                              Open
                            </TkButton>
                            {folder.quota_bytes !== null && (
                              <TkButton intent="ghost" size="sm" className="gap-1"
                                onClick={() => {
                                  setQuotaTarget({ filesystem: fs.name, folder });
                                  setQuotaGib(String(Math.round(folder.quota_bytes! / GIB)));
                                }}>
                                <Pencil className="w-4 h-4" />
                                Quota
                              </TkButton>
                            )}
                          </div>
                        </TkTableCell>
                      </TkTableRow>
                    ))}
                  </TkTableBody>
                </TkTable>
              </div>
            </div>
          ))}

          {disks.length > 0 && (
            <div className="text-sm text-muted-foreground">
              Disk under them:{' '}
              {disks.map(d => `${d.node} ${d.dir}: ${formatBytes(d.free_bytes)} free of ${formatBytes(d.total_bytes)}`).join('; ')}
            </div>
          )}
        </TkCardContent>
      </TkCard>

      {/* Folder browser */}
      {browse && (
        <TkCard>
          <TkCardHeader className="flex flex-row items-center justify-between">
            <div>
              <TkCardTitle>
                <div className="flex flex-wrap items-center gap-1 text-base">
                  <button className="hover:underline" onClick={() => openFolder(browse.filesystem, browseParts[0])}>
                    {browseTop ? folderLabel(browseTop) : browseParts[0]}
                  </button>
                  {browseParts.slice(1).map((part, i) => (
                    <span key={i} className="flex items-center gap-1">
                      <ChevronRight className="w-4 h-4" />
                      <button className="hover:underline"
                        onClick={() => openFolder(browse.filesystem, browseParts.slice(0, i + 2).join('/'))}>
                        {part}
                      </button>
                    </span>
                  ))}
                </div>
              </TkCardTitle>
              <TkCardDescription>{browse.filesystem}:/{browse.path}</TkCardDescription>
            </div>
            <TkButton intent="ghost" onClick={() => setBrowse(null)}>Close</TkButton>
          </TkCardHeader>
          <TkCardContent>
            {browseLoading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="w-6 h-6 animate-spin" />
              </div>
            ) : entries.length === 0 ? (
              <p className="text-sm text-muted-foreground">This folder is empty.</p>
            ) : (
              <div className="overflow-x-auto rounded-lg border">
                <TkTable>
                  <TkTableHeader>
                    <TkTableRow>
                      <TkTableHead className="font-semibold">Name</TkTableHead>
                      <TkTableHead className="font-semibold">Size</TkTableHead>
                      <TkTableHead className="font-semibold">Files</TkTableHead>
                      <TkTableHead className="font-semibold">Actions</TkTableHead>
                    </TkTableRow>
                  </TkTableHeader>
                  <TkTableBody>
                    {entries.map(entry => (
                      <TkTableRow key={entry.path}>
                        <TkTableCell>
                          {entry.is_dir ? (
                            <button className="flex items-center gap-2 hover:underline"
                              onClick={() => openFolder(browse.filesystem, entry.path)}>
                              <Folder className="w-4 h-4" />
                              {entry.name}
                            </button>
                          ) : (
                            <span className="flex items-center gap-2">
                              <File className="w-4 h-4" />
                              {entry.name}
                            </span>
                          )}
                        </TkTableCell>
                        <TkTableCell>{formatBytes(entry.bytes)}</TkTableCell>
                        <TkTableCell className="text-xs text-muted-foreground">
                          {entry.is_dir ? `${entry.files} files, ${entry.dirs} folders` : '-'}
                        </TkTableCell>
                        <TkTableCell>
                          <TkButton intent="ghost" size="sm" className="text-destructive hover:text-destructive"
                            onClick={() => setDeleteTarget({ filesystem: browse.filesystem, entry })}>
                            <Trash2 className="w-4 h-4" />
                          </TkButton>
                        </TkTableCell>
                      </TkTableRow>
                    ))}
                  </TkTableBody>
                </TkTable>
              </div>
            )}
          </TkCardContent>
        </TkCard>
      )}

      {/* Node disks */}
      <TkCard>
        <TkCardHeader>
          <TkCardTitle>Node disks</TkCardTitle>
          <TkCardDescription>
            Each node's own disks, and its training folder ({nodes.find(n => n.training)?.training?.path || '/var/lib/thinkube-training'}), where training jobs keep checkpoints.
          </TkCardDescription>
        </TkCardHeader>
        <TkCardContent>
          {loading.nodes && !nodes.length ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="w-6 h-6 animate-spin" />
              <span className="ml-2">Reading node disks...</span>
            </div>
          ) : (
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {nodes.map(node => (
                <div key={node.node} className="rounded-lg border p-4 space-y-3">
                  <div className="font-semibold flex items-center gap-2">
                    <HardDrive className="w-4 h-4" />
                    {node.node}
                  </div>
                  {node.error ? (
                    <p className="text-sm text-destructive">{node.error}</p>
                  ) : (
                    <>
                      {node.disks?.map(d => (
                        <div key={d.source}>
                          <div className="text-xs font-mono">{d.mount} <span className="text-muted-foreground">({d.source})</span></div>
                          <UsageBar used={d.used_bytes} total={d.total_bytes} />
                        </div>
                      ))}
                      {node.df_error && <p className="text-xs text-amber-600">df: {node.df_error}</p>}
                      {node.training?.exists ? (
                        <div>
                          <div className="text-sm font-medium">Training folder: {formatBytes(node.training.bytes)}</div>
                          <ul className="text-xs space-y-0.5 mt-1">
                            {node.training.entries?.map(e => (
                              <li key={e.path} className="flex justify-between gap-2"
                                style={{ paddingLeft: `${(e.path.split('/').length - 1) * 1}rem` }}>
                                <span className="font-mono truncate">{e.path.split('/').pop()}</span>
                                <span className="text-muted-foreground whitespace-nowrap">{formatBytes(e.bytes)}</span>
                              </li>
                            ))}
                          </ul>
                          {node.training.error && <p className="text-xs text-amber-600">du: {node.training.error}</p>}
                        </div>
                      ) : (
                        <div className="text-xs text-muted-foreground">No training folder.</div>
                      )}
                    </>
                  )}
                </div>
              ))}
            </div>
          )}
        </TkCardContent>
      </TkCard>

      {/* Volume claims */}
      <TkCard>
        <TkCardHeader>
          <TkCardTitle>Volume claims</TkCardTitle>
          <TkCardDescription>
            What each app holds. Used space comes from the kubelet, which measures a claim only while a pod mounts it.
          </TkCardDescription>
        </TkCardHeader>
        <TkCardContent>
          {claimErrors.map(e => <p key={e} className="text-xs text-amber-600 mb-2">{e}</p>)}
          {loading.claims && !claims.length ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="w-6 h-6 animate-spin" />
            </div>
          ) : (
            <div className="overflow-x-auto rounded-lg border">
              <TkTable>
                <TkTableHeader>
                  <TkTableRow>
                    <TkTableHead className="font-semibold">Namespace</TkTableHead>
                    <TkTableHead className="font-semibold">Claim</TkTableHead>
                    <TkTableHead className="font-semibold">Storage class</TkTableHead>
                    <TkTableHead className="font-semibold">Size</TkTableHead>
                    <TkTableHead className="font-semibold">Used</TkTableHead>
                  </TkTableRow>
                </TkTableHeader>
                <TkTableBody>
                  {claims.map(c => (
                    <TkTableRow key={`${c.namespace}/${c.name}`}>
                      <TkTableCell>{c.namespace}</TkTableCell>
                      <TkTableCell className="font-medium">
                        {c.name}
                        {c.phase !== 'Bound' && <TkBadge appearance="outlined" className="ml-2">{c.phase}</TkBadge>}
                      </TkTableCell>
                      <TkTableCell className="text-xs">{c.storage_class || 'static'}</TkTableCell>
                      <TkTableCell>{formatBytes(c.capacity_bytes ?? c.requested_bytes)}</TkTableCell>
                      <TkTableCell>
                        {c.used_bytes === null ? (
                          <span className="text-xs text-muted-foreground">not mounted</span>
                        ) : formatBytes(c.used_bytes)}
                      </TkTableCell>
                    </TkTableRow>
                  ))}
                </TkTableBody>
              </TkTable>
            </div>
          )}
        </TkCardContent>
      </TkCard>

      {/* Quota dialog */}
      <TkDialogRoot open={!!quotaTarget} onOpenChange={(open) => !open && setQuotaTarget(null)}>
        <TkDialogContent>
          <TkDialogHeader>
            <TkDialogTitle>Change quota</TkDialogTitle>
          </TkDialogHeader>
          {quotaTarget && (
            <div className="space-y-3 text-sm">
              <p>
                <strong>{folderLabel(quotaTarget.folder)}</strong> holds {formatBytes(quotaTarget.folder.bytes)}.
                The quota is {formatBytes(quotaTarget.folder.quota_bytes)}. The disk under the shared folders has {formatBytes(diskFree)} free.
              </p>
              <div className="space-y-1">
                <TkLabel htmlFor="quota-gib">New quota (GiB)</TkLabel>
                <TkInput id="quota-gib" type="number" min={1} value={quotaGib}
                  onChange={(e: ChangeEvent<HTMLInputElement>) => setQuotaGib(e.target.value)} />
              </div>
              <p className="text-muted-foreground">
                Takes effect at once. The volume's capacity is changed too, so the next mount keeps it.
              </p>
            </div>
          )}
          <TkDialogFooter>
            <TkButton intent="ghost" onClick={() => setQuotaTarget(null)}>Cancel</TkButton>
            <TkButton onClick={saveQuota} disabled={working || !(Number(quotaGib) >= 1)} className="gap-2">
              {working && <Loader2 className="w-4 h-4 animate-spin" />}
              Set quota
            </TkButton>
          </TkDialogFooter>
        </TkDialogContent>
      </TkDialogRoot>

      {/* Delete dialog */}
      <TkDialogRoot open={!!deleteTarget} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <TkDialogContent>
          <TkDialogHeader>
            <TkDialogTitle>Delete {deleteTarget?.entry.is_dir ? 'folder' : 'file'}</TkDialogTitle>
          </TkDialogHeader>
          {deleteTarget && (
            <div className="space-y-2 text-sm">
              <p>
                Delete <strong className="font-mono break-all">{deleteTarget.entry.path}</strong> ({formatBytes(deleteTarget.entry.bytes)}
                {deleteTarget.entry.is_dir && `, ${deleteTarget.entry.files} files`})?
              </p>
              <p className="text-muted-foreground">
                This cannot be undone from here. The folder's quota is free at once; the disk space after the
                trash days of {deleteTarget.filesystem}.
              </p>
            </div>
          )}
          <TkDialogFooter>
            <TkButton intent="ghost" onClick={() => setDeleteTarget(null)}>Cancel</TkButton>
            <TkButton intent="danger" onClick={confirmDelete} disabled={working} className="gap-2">
              {working ? <Loader2 className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
              Delete
            </TkButton>
          </TkDialogFooter>
        </TkDialogContent>
      </TkDialogRoot>
    </TkPageWrapper>
  );
}
