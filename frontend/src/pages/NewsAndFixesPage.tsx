/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useEffect } from 'react'
import { toast } from 'sonner'
import { TkButton, TkBadge } from 'thinkube-style/components/buttons-badges'
import { TkCard, TkCardHeader, TkCardTitle, TkCardContent } from 'thinkube-style/components/cards-data'
import { TkErrorAlert, TkInfoAlert } from 'thinkube-style/components/feedback'
import { TkPageWrapper } from 'thinkube-style/components/utilities'
import { useFixesStore, type FixEntry, type NewsEntry } from '../stores/useFixesStore'
import { useRunsStore } from '../stores/useRunsStore'

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : 'never')

function FixCard({ fix, onApply, queued }: { fix: FixEntry; onApply: () => void; queued: boolean }) {
  return (
    <TkCard>
      <TkCardHeader className="flex flex-row items-start justify-between gap-4">
        <div className="space-y-1">
          <TkCardTitle>{fix.title}</TkCardTitle>
          <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
            {fix.severity === 'security'
              ? <TkBadge status="unhealthy">security</TkBadge>
              : <TkBadge status="warning">bug</TkBadge>}
            <span>{fix.component}</span>
            <span>{fix.installed_version ? `v${fix.installed_version}` : 'not installed'} → v{fix.fixed_in}</span>
            <span>{fix.date}</span>
          </div>
        </div>
        {fix.status === 'available' && (
          <TkButton onClick={onApply} disabled={queued}>
            {queued ? 'Queued' : 'Apply'}
          </TkButton>
        )}
        {fix.status === 'applied' && <TkBadge status="healthy">applied</TkBadge>}
      </TkCardHeader>
      <TkCardContent className="space-y-3">
        <p className="whitespace-pre-line text-sm">{fix.body}</p>
        {fix.status === 'apply_from_ide' && fix.ide_command && (
          <TkInfoAlert>
            This fix restarts Thinkube Control, so it cannot apply it itself. Run this in a Thinkube IDE terminal:
            <pre className="mt-2 overflow-x-auto font-mono text-xs">{fix.ide_command}</pre>
          </TkInfoAlert>
        )}
      </TkCardContent>
    </TkCard>
  )
}

function NewsCard({ news, onRead }: { news: NewsEntry; onRead: () => void }) {
  return (
    <TkCard>
      <TkCardHeader className="flex flex-row items-start justify-between gap-4">
        <div className="space-y-1">
          <TkCardTitle>{news.title}</TkCardTitle>
          <div className="text-sm text-muted-foreground">{news.date}</div>
        </div>
        {news.read
          ? <TkBadge appearance="muted">read</TkBadge>
          : <TkButton intent="secondary" size="sm" onClick={onRead}>Mark read</TkButton>}
      </TkCardHeader>
      <TkCardContent>
        <p className="whitespace-pre-line text-sm">{news.body}</p>
      </TkCardContent>
    </TkCard>
  )
}

export default function NewsAndFixesPage() {
  const { feed, error, checking, fetchFeed, checkNow, applyFix, markRead } = useFixesStore()
  const { active, queued, recent, openDialog, isInQueue } = useRunsStore()

  const handleApply = async (fix: FixEntry) => {
    try {
      const response = await applyFix(fix.id)
      toast.success(
        response.queue_position && response.queue_position > 1
          ? `Fix ${fix.title}: queued at position ${response.queue_position}`
          : `Fix ${fix.title}: queued, starts next`
      )
      openDialog()
    } catch (err: any) {
      toast.error(`Failed to queue the fix ${fix.title}: ${err.response?.data?.detail ?? err.message}`)
    }
  }

  // A fix run ending changes the installed versions, and so the statuses.
  const queueState = `${active?.id ?? ''}:${active?.status ?? ''}:${queued.map(r => r.id).join(',')}:${recent[0]?.id ?? ''}`
  useEffect(() => {
    fetchFeed()
  }, [queueState])

  const fixes = (feed?.fixes ?? []).filter(f => f.status !== 'not_installed')

  return (
    <TkPageWrapper description="News about this Thinkube release, and fixes for the components installed on this cluster.">
      <div className="space-y-8"> {/* @allowed-inline */}
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="text-sm text-muted-foreground">
            Last check: {when(feed?.last_checked ?? null)}
            {feed?.feed_commit && ` · thinkube-fixes ${feed.feed_commit.slice(0, 12)}`}
          </div>
          <TkButton intent="secondary" onClick={checkNow} disabled={checking}>
            {checking ? 'Checking…' : 'Check now'}
          </TkButton>
        </div>

        {error && <TkErrorAlert>{error}</TkErrorAlert>}
        {feed?.last_error && (
          <TkErrorAlert>
            The last check failed at {when(feed.last_checked)}: {feed.last_error}
            {feed.feed_fetched && ` What is shown was read at ${when(feed.feed_fetched)}.`}
          </TkErrorAlert>
        )}

        <div>
          <h2 className="text-2xl font-bold mb-4">Fixes</h2>
          {fixes.length === 0
            ? <p className="text-sm text-muted-foreground">No fixes for the components on this cluster.</p>
            : (
              <div className="space-y-4">
                {fixes.map(fix => (
                  <FixCard
                    key={fix.id}
                    fix={fix}
                    queued={isInQueue(`fix-${fix.id}`)}
                    onApply={() => handleApply(fix)}
                  />
                ))}
              </div>
            )}
        </div>

        <div>
          <h2 className="text-2xl font-bold mb-4">News</h2>
          {(feed?.news ?? []).length === 0
            ? <p className="text-sm text-muted-foreground">No news.</p>
            : (
              <div className="space-y-4">
                {feed!.news.map(news => (
                  <NewsCard key={news.id} news={news} onRead={() => markRead(news.id)} />
                ))}
              </div>
            )}
        </div>
      </div>
    </TkPageWrapper>
  )
}
