/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useCallback, useEffect, useState } from 'react';
import { Send } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { TkBadge, TkButton, TkLoadingButton } from 'thinkube-style/components/buttons-badges';
import { TkCard, TkCardContent, TkCardDescription, TkCardHeader, TkCardTitle } from 'thinkube-style/components/cards-data';
import { TkErrorAlert, TkInfoAlert } from 'thinkube-style/components/feedback';
import { TkTextarea } from 'thinkube-style/components/forms-inputs';
import { failure, forge, type ChatTarget, type ChatTurn, type ModelStatus, type ServedTarget } from '@/lib/forge';

const MOVING = ['loading', 'unloading'];
const POLL_MS = 5000;
// The models this card serves and stops itself; the fine-tuned one has its own card
type Managed = ServedTarget;

/** The same questions to the fine-tuned model, its base model and its GGUF export, each in its own conversation. */
export function ChatCompare({
  runId,
  tunedServed,
  ids,
}: {
  runId: string;
  tunedServed: boolean;
  ids: Partial<Record<ChatTarget, string>>;
}) {
  const { t } = useTranslation();
  const targets = (['tuned', 'base', 'gguf', 'nvfp4'] as ChatTarget[]).filter((target) => ids[target]);
  const managed = targets.filter((target): target is Managed => target !== 'tuned');
  const [status, setStatus] = useState<Partial<Record<Managed, ModelStatus>>>({});
  const [statusError, setStatusError] = useState<Partial<Record<Managed, string>>>({});
  const [changing, setChanging] = useState<Managed | null>(null);
  const [question, setQuestion] = useState('');
  const [turns, setTurns] = useState<Partial<Record<ChatTarget, ChatTurn[]>>>({});
  const [errors, setErrors] = useState<Partial<Record<ChatTarget, string | null>>>({});
  const [asking, setAsking] = useState(false);

  const read = useCallback(
    (target: Managed) => {
      forge
        .servedModel(runId, target)
        .then((m) => {
          setStatus((current) => ({ ...current, [target]: m }));
          setStatusError((current) => ({ ...current, [target]: undefined }));
        })
        .catch((e) => setStatusError((current) => ({ ...current, [target]: failure(e) })));
    },
    [runId],
  );

  const managedKey = managed.join(',');
  useEffect(() => {
    managedKey.split(',').filter(Boolean).forEach((target) => read(target as Managed));
  }, [managedKey, read]);

  const moving = managed.filter((target) => MOVING.includes(status[target]?.model.state ?? ''));
  const movingKey = moving.join(',');
  useEffect(() => {
    if (!movingKey) return;
    const timer = setInterval(() => movingKey.split(',').forEach((target) => read(target as Managed)), POLL_MS);
    return () => clearInterval(timer);
  }, [movingKey, read]);

  const isServed = (target: ChatTarget) =>
    target === 'tuned' ? tunedServed : status[target as Managed]?.model.state === 'available';
  const served = targets.filter(isServed);

  const change = async (target: Managed, load: boolean) => {
    setChanging(target);
    try {
      toast.info((load ? await forge.loadServed(runId, target) : await forge.unloadServed(runId, target)).message);
      read(target);
    } catch (e) {
      toast.error(failure(e));
    } finally {
      setChanging(null);
    }
  };

  const ask = async () => {
    const text = question.trim();
    if (!text || served.length === 0) return;
    setAsking(true);
    setQuestion('');
    const next: Partial<Record<ChatTarget, ChatTurn[]>> = { ...turns };
    for (const target of served) next[target] = [...(turns[target] ?? []), { role: 'user', content: text }];
    setTurns(next);
    await Promise.all(
      served.map(async (target) => {
        try {
          const answer = await forge.chat(runId, target, next[target] ?? []);
          setTurns((current) => ({
            ...current,
            [target]: [...(current[target] ?? []), { role: 'assistant', content: answer.content }],
          }));
          setErrors((current) => ({ ...current, [target]: null }));
        } catch (e) {
          setErrors((current) => ({ ...current, [target]: failure(e) }));
        }
      }),
    );
    setAsking(false);
  };

  const column = (target: ChatTarget) => {
    const state = target === 'tuned' ? undefined : status[target];
    const servedNow = isServed(target);
    return (
      <div key={target} className="space-y-2 min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{t(`chat.${target}`)}</span>
          <span className="text-xs text-muted-foreground break-all">{ids[target]}</span>
          {!servedNow && <TkBadge appearance="muted">{t('chat.notServed')}</TkBadge>}
        </div>
        {target !== 'tuned' && !servedNow && (
          <div className="space-y-2">
            {statusError[target] && <TkErrorAlert>{statusError[target]}</TkErrorAlert>}
            {state?.model.last_error && <TkErrorAlert>{state.model.last_error}</TkErrorAlert>}
            <TkLoadingButton
              size="sm"
              intent="secondary"
              loading={changing === target || moving.includes(target)}
              onClick={() => change(target, true)}
            >
              {moving.includes(target)
                ? t(`modelState.${state?.model.state}`, { defaultValue: state?.model.state })
                : t('chat.serve')}
            </TkLoadingButton>
          </div>
        )}
        {target !== 'tuned' && servedNow && (
          <TkButton size="sm" intent="ghost" disabled={changing === target} onClick={() => change(target, false)}>
            {t('chat.stopServing')}
          </TkButton>
        )}
        <div className="space-y-2">
          {(turns[target] ?? []).map((turn, i) => (
            <div
              key={i}
              className={`rounded-lg px-3 py-2 text-sm whitespace-pre-wrap break-words ${turn.role === 'user' ? 'bg-muted' : 'border'}`}
            >
              {turn.content}
            </div>
          ))}
          {errors[target] && <TkErrorAlert>{errors[target]}</TkErrorAlert>}
        </div>
      </div>
    );
  };

  return (
    <TkCard>
      <TkCardHeader>
        <TkCardTitle>{t('chat.title')}</TkCardTitle>
        <TkCardDescription>{t('chat.help')}</TkCardDescription>
      </TkCardHeader>
      <TkCardContent className="space-y-4">
        {served.length === 0 && <TkInfoAlert>{t('chat.loadFirst')}</TkInfoAlert>}
        <div className={`grid gap-4 ${targets.length > 2 ? 'lg:grid-cols-3' : 'md:grid-cols-2'}`}>{targets.map(column)}</div>
        <div className="flex items-end gap-2">
          <TkTextarea
            className="flex-1"
            rows={2}
            value={question}
            placeholder={t('chat.placeholder')}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                ask();
              }
            }}
          />
          <TkLoadingButton loading={asking} disabled={!question.trim() || served.length === 0} onClick={ask}>
            <Send className="h-4 w-4" />
            {t('chat.send')}
          </TkLoadingButton>
        </div>
      </TkCardContent>
    </TkCard>
  );
}
