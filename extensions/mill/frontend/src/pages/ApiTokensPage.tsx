/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { TkButton } from 'thinkube-style/components/buttons-badges';
import {
  TkCard,
  TkCardHeader,
  TkCardTitle,
  TkCardContent,
  TkCardFooter,
} from 'thinkube-style/components/cards-data';
import { TkInput, TkLabel } from 'thinkube-style/components/forms-inputs';
import { TkSuccessAlert, TkInfoAlert, TkCodeBlock } from 'thinkube-style/components/feedback';
import { TkPageWrapper } from 'thinkube-style/components/utilities';
import { TkControlledConfirmDialog } from 'thinkube-style/components/modals-overlays';
import {
  TkTable,
  TkTableBody,
  TkTableCell,
  TkTableHead,
  TkTableHeader,
  TkTableRow,
} from 'thinkube-style/components/tables';
import { useTokensStore, type APIToken, type CreatedToken } from '@/stores/useTokensStore';

export default function ApiTokensPage() {
  const { t, i18n } = useTranslation();
  const { tokens, loading, fetchTokens, createToken, revokeToken } = useTokensStore();
  const [name, setName] = useState('');
  const [expiresInDays, setExpiresInDays] = useState('');
  const [creating, setCreating] = useState(false);
  const [createdToken, setCreatedToken] = useState<CreatedToken | null>(null);
  const [tokenToRevoke, setTokenToRevoke] = useState<APIToken | null>(null);

  const apiBaseUrl = `${window.location.origin}/api`;

  useEffect(() => {
    fetchTokens().catch(() => toast.error(t('apiTokens.errors.loadFailed')));
  }, [fetchTokens, t]);

  const formatDate = (value: string) => new Date(value).toLocaleString(i18n.resolvedLanguage);

  const handleCreate = async () => {
    if (!name.trim()) {
      toast.error(t('apiTokens.errors.nameMissing'));
      return;
    }

    setCreating(true);
    try {
      const created = await createToken({
        name: name.trim(),
        expires_in_days: expiresInDays ? parseInt(expiresInDays, 10) : null,
      });
      setCreatedToken(created);
      setName('');
      setExpiresInDays('');
      toast.success(t('apiTokens.success.created'));
    } catch {
      toast.error(t('apiTokens.errors.createFailed'));
    } finally {
      setCreating(false);
    }
  };

  const confirmRevoke = async () => {
    if (!tokenToRevoke) return;

    try {
      await revokeToken(tokenToRevoke.id);
      toast.success(t('apiTokens.success.revoked'));
    } catch {
      toast.error(t('apiTokens.errors.revokeFailed'));
    } finally {
      setTokenToRevoke(null);
    }
  };

  const copyToken = async (token: string) => {
    await navigator.clipboard.writeText(token);
    toast.success(t('apiTokens.tokenCreated.copied'));
  };

  return (
    <TkPageWrapper>
      <div>
        <h1 className="text-2xl font-bold">{t('apiTokens.title')}</h1>
        <p className="text-muted-foreground mt-1">{t('apiTokens.subtitle')}</p>
      </div>

      <TkCard>
        <TkCardHeader>
          <TkCardTitle>{t('apiTokens.createToken.title')}</TkCardTitle>
        </TkCardHeader>
        <TkCardContent className="space-y-4">
          <div className="space-y-2">
            <TkLabel htmlFor="token-name">{t('apiTokens.createToken.nameLabel')}</TkLabel>
            <TkInput
              id="token-name"
              value={name}
              placeholder={t('apiTokens.createToken.namePlaceholder')}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <TkLabel htmlFor="token-expires">{t('apiTokens.createToken.expiresLabel')}</TkLabel>
            <TkInput
              id="token-expires"
              type="number"
              min={1}
              value={expiresInDays}
              placeholder={t('apiTokens.createToken.expiresPlaceholder')}
              onChange={(e) => setExpiresInDays(e.target.value)}
            />
          </div>
        </TkCardContent>
        <TkCardFooter className="justify-end">
          <TkButton disabled={!name.trim() || creating} onClick={handleCreate}>
            {creating && <Loader2 className="h-4 w-4 animate-spin" />}
            {t('apiTokens.createToken.createButton')}
          </TkButton>
        </TkCardFooter>
      </TkCard>

      {createdToken && (
        <TkSuccessAlert title={t('apiTokens.tokenCreated.title')}>
          <div className="space-y-3">
            <p className="text-sm">{t('apiTokens.tokenCreated.message')}</p>
            <TkCodeBlock maxHeight="h-auto">
              <code>{createdToken.token}</code>
            </TkCodeBlock>
            <div className="flex gap-2">
              <TkButton intent="secondary" size="sm" onClick={() => copyToken(createdToken.token)}>
                {t('apiTokens.tokenCreated.copyButton')}
              </TkButton>
              <TkButton intent="secondary" size="sm" onClick={() => setCreatedToken(null)}>
                {t('common.close')}
              </TkButton>
            </div>
          </div>
        </TkSuccessAlert>
      )}

      <TkCard>
        <TkCardHeader>
          <TkCardTitle>{t('apiTokens.tokenList.title')}</TkCardTitle>
        </TkCardHeader>
        <TkCardContent>
          {loading && tokens.length === 0 ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-8 w-8 animate-spin text-primary" />
            </div>
          ) : tokens.length === 0 ? (
            <TkInfoAlert>{t('apiTokens.tokenList.noTokens')}</TkInfoAlert>
          ) : (
            <div className="overflow-x-auto">
              <TkTable>
                <TkTableHeader>
                  <TkTableRow>
                    <TkTableHead>{t('apiTokens.tokenList.table.name')}</TkTableHead>
                    <TkTableHead>{t('apiTokens.tokenList.table.created')}</TkTableHead>
                    <TkTableHead>{t('apiTokens.tokenList.table.expires')}</TkTableHead>
                    <TkTableHead>{t('apiTokens.tokenList.table.lastUsed')}</TkTableHead>
                    <TkTableHead>{t('apiTokens.tokenList.table.actions')}</TkTableHead>
                  </TkTableRow>
                </TkTableHeader>
                <TkTableBody>
                  {tokens.map((token) => (
                    <TkTableRow key={token.id} data-testid="token-row">
                      <TkTableCell>{token.name}</TkTableCell>
                      <TkTableCell>{formatDate(token.created_at)}</TkTableCell>
                      <TkTableCell>
                        {token.expires_at
                          ? formatDate(token.expires_at)
                          : t('apiTokens.tokenList.table.never')}
                      </TkTableCell>
                      <TkTableCell>
                        {token.last_used
                          ? formatDate(token.last_used)
                          : t('apiTokens.tokenList.table.never')}
                      </TkTableCell>
                      <TkTableCell>
                        <TkButton
                          intent="danger"
                          size="sm"
                          disabled={!token.is_active}
                          onClick={() => setTokenToRevoke(token)}
                        >
                          {token.is_active
                            ? t('apiTokens.tokenList.table.revoke')
                            : t('apiTokens.tokenList.table.revoked')}
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

      <TkCard>
        <TkCardHeader>
          <TkCardTitle>{t('apiTokens.usage.title')}</TkCardTitle>
        </TkCardHeader>
        <TkCardContent className="space-y-6">
          <div>
            <h3 className="text-lg font-semibold mb-2">{t('apiTokens.usage.cli.title')}</h3>
            <p className="text-sm text-muted-foreground mb-3">
              {t('apiTokens.usage.cli.description')}
            </p>
            <TkCodeBlock maxHeight="h-auto">
              <code>{`curl -H "Authorization: Bearer YOUR_TOKEN" ${apiBaseUrl}/v1/tasks`}</code>
            </TkCodeBlock>
          </div>

          <div>
            <h3 className="text-lg font-semibold mb-2">{t('apiTokens.usage.env.title')}</h3>
            <p className="text-sm text-muted-foreground mb-3">
              {t('apiTokens.usage.env.description')}
            </p>
            <TkCodeBlock maxHeight="h-auto">
              <code>export API_TOKEN=&quot;YOUR_TOKEN&quot;</code>
            </TkCodeBlock>
          </div>
        </TkCardContent>
      </TkCard>

      <TkControlledConfirmDialog
        open={!!tokenToRevoke}
        onOpenChange={(open) => !open && setTokenToRevoke(null)}
        title={t('apiTokens.tokenList.confirmRevokeTitle')}
        description={t('apiTokens.tokenList.confirmRevoke')}
        confirmText={t('apiTokens.tokenList.table.revoke')}
        cancelText={t('common.cancel')}
        variant="destructive"
        onConfirm={confirmRevoke}
      />
    </TkPageWrapper>
  );
}
