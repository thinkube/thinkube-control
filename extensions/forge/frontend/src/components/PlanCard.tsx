/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState } from 'react';
import { RotateCcw } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { TkBadge, TkButton, TkLoadingButton } from 'thinkube-style/components/buttons-badges';
import { TkCard, TkCardContent, TkCardDescription, TkCardHeader, TkCardTitle } from 'thinkube-style/components/cards-data';
import { TkInput } from 'thinkube-style/components/forms-inputs';
import { TkTable, TkTableBody, TkTableCell, TkTableHead, TkTableHeader, TkTableRow } from 'thinkube-style/components/tables';
import { decisionField, duration, parseDecision, SETTABLE_DECISIONS, type DecisionValue, type Plan, type Run } from '@/lib/forge';

interface PlanCardProps {
  plan: Plan;
  /** The run, for the memory its training job asks for. */
  run?: Run;
  title?: string;
  /** With onReplan, a person may set a decision and ask for the plan again. */
  onReplan?: (decisions: Record<string, DecisionValue>) => Promise<void>;
  chosen?: Record<string, DecisionValue>;
}

/** Meep²'s plan: each decision with its reason, and whether it is auto mode's or the person's. */
export function PlanCard({ plan, run, title, onReplan, chosen = {} }: PlanCardProps) {
  const { t } = useTranslation();
  const [edits, setEdits] = useState<Record<string, string>>(
    Object.fromEntries(Object.entries(chosen).map(([k, v]) => [k, String(v)])),
  );
  const [planning, setPlanning] = useState(false);

  const replan = async () => {
    if (!onReplan) return;
    const decisions: Record<string, DecisionValue> = {};
    for (const [field, text] of Object.entries(edits)) {
      if (text.trim() === '') continue;
      const like = plan.decisions.find((d) => decisionField(d.name) === field)?.auto_value ?? null;
      decisions[field] = parseDecision(text, typeof like === 'object' && like !== null ? 0 : like);
    }
    setPlanning(true);
    try {
      await onReplan(decisions);
    } finally {
      setPlanning(false);
    }
  };

  return (
    <TkCard>
      <TkCardHeader>
        <TkCardTitle>{title ?? t('plan.title')}</TkCardTitle>
        <TkCardDescription>
          {t('plan.device', { device: plan.device, free: plan.free_bytes ? (plan.free_bytes / 2 ** 30).toFixed(0) : '?' })}
          {plan.estimated_seconds != null && ` · ${t('plan.estimate', { time: duration(plan.estimated_seconds) })}`}
          {plan.seconds_per_step != null && ` · ${t('plan.perStep', { seconds: plan.seconds_per_step.toFixed(2) })}`}
        </TkCardDescription>
      </TkCardHeader>
      <TkCardContent className="space-y-4">
        {run && (
          <p className="text-sm">
            {run.memory_gb != null
              ? t('plan.memoryYours', { gb: run.memory_gb })
              : run.training_memory_gb != null
                ? t('plan.memoryMeep', { gb: run.training_memory_gb, reason: run.memory_reason })
                : t('plan.memoryUnsized', { reason: run.memory_reason })}
            {run.cpu_cores != null && ` ${t('plan.coresYours', { cores: run.cpu_cores })}`}
          </p>
        )}
        <div className="overflow-x-auto">
          <TkTable>
            <TkTableHeader>
              <TkTableRow>
                <TkTableHead>{t('plan.decision')}</TkTableHead>
                <TkTableHead>{t('plan.choice')}</TkTableHead>
                <TkTableHead>{t('plan.reason')}</TkTableHead>
                {onReplan && <TkTableHead>{t('plan.yours')}</TkTableHead>}
              </TkTableRow>
            </TkTableHeader>
            <TkTableBody>
              {plan.decisions.map((d) => (
                <TkTableRow key={d.name}>
                  <TkTableCell className="font-medium whitespace-nowrap">{d.label}</TkTableCell>
                  <TkTableCell className="text-sm">
                    {d.text}
                    {d.by === 'user' && (
                      <TkBadge appearance="outlined" className="ml-2">
                        {t('plan.byYou')}
                      </TkBadge>
                    )}
                  </TkTableCell>
                  <TkTableCell className="text-sm text-muted-foreground">
                    {d.by === 'user' ? t('plan.autoWould', { text: d.auto_text }) : d.reason}
                  </TkTableCell>
                  {onReplan && (
                    <TkTableCell className="min-w-32">
                      {SETTABLE_DECISIONS.includes(decisionField(d.name)) && (
                        <TkInput
                          aria-label={t('plan.setDecision', { name: d.label })}
                          value={edits[decisionField(d.name)] ?? ''}
                          placeholder={d.name === 'evaluation' ? t('plan.everyAuto') : t('plan.auto')}
                          onChange={(e) => setEdits({ ...edits, [decisionField(d.name)]: e.target.value })}
                        />
                      )}
                    </TkTableCell>
                  )}
                </TkTableRow>
              ))}
            </TkTableBody>
          </TkTable>
        </div>
        {onReplan && (
          <div className="flex flex-wrap gap-2">
            <TkLoadingButton intent="secondary" loading={planning} onClick={replan}>
              {t('plan.replan')}
            </TkLoadingButton>
            {Object.values(edits).some((v) => v.trim() !== '') && (
              <TkButton intent="ghost" onClick={() => setEdits({})}>
                <RotateCcw className="h-4 w-4" />
                {t('plan.allAuto')}
              </TkButton>
            )}
          </div>
        )}
      </TkCardContent>
    </TkCard>
  );
}
