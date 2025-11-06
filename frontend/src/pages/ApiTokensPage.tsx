import { useState, useEffect } from 'react'
import { TkButton } from 'thinkube-style/components/buttons-badges'
import { TkCard, TkCardHeader, TkCardTitle, TkCardContent, TkCardFooter } from 'thinkube-style/components/cards-data'
import { TkInput, TkLabel } from 'thinkube-style/components/forms-inputs'
import { TkSuccessAlert, TkWarningAlert, TkInfoAlert, TkCodeBlock } from 'thinkube-style/components/feedback'
import { TkPageWrapper } from 'thinkube-style/components/utilities'
import {
  TkTable as Table,
  TkTableBody as TableBody,
  TkTableCell as TableCell,
  TkTableHead as TableHead,
  TkTableHeader as TableHeader,
  TkTableRow as TableRow,
} from 'thinkube-style/components/tables'
import { Loader2 } from 'lucide-react'
import { useTokensStore } from '@/stores/useTokensStore'
import { toast } from 'sonner'

interface NewToken {
  name: string
  expires_in_days: number | null
}

interface CreatedToken {
  token: string
  name: string
  expires_at?: string
}

interface RevealedToken {
  token: string
  name: string
  message: string
}

export default function ApiTokensPage() {
  const { tokens, loading, fetchTokens, createToken, revokeToken, revealToken } = useTokensStore()
  const [newToken, setNewToken] = useState<NewToken>({ name: '', expires_in_days: null })
  const [createdToken, setCreatedToken] = useState<CreatedToken | null>(null)
  const [revealedToken, setRevealedToken] = useState<RevealedToken | null>(null)

  useEffect(() => {
    fetchTokens()
  }, [fetchTokens])

  const formatDate = (dateString: string) => {
    return new Date(dateString).toLocaleString()
  }

  const handleCreateToken = async () => {
    if (!newToken.name || newToken.name.trim() === '') {
      toast.error('Token name is required')
      return
    }

    try {
      const tokenData: any = { name: newToken.name, scopes: [] }
      if (newToken.expires_in_days) {
        // Calculate expiration date
        const expiresAt = new Date()
        expiresAt.setDate(expiresAt.getDate() + newToken.expires_in_days)
        tokenData.expires_at = expiresAt.toISOString()
      }

      const result = await createToken(tokenData)
      setCreatedToken(result)
      setNewToken({ name: '', expires_in_days: null })
      toast.success('Token created successfully')
    } catch (error) {
      console.error('Failed to create token:', error)
      toast.error('Failed to create token')
    }
  }

  const handleRevokeToken = async (tokenId: string) => {
    if (!confirm('Are you sure you want to revoke this token? This action cannot be undone.')) {
      return
    }

    try {
      await revokeToken(tokenId)
      toast.success('Token revoked successfully')
    } catch (error) {
      console.error('Failed to revoke token:', error)
      toast.error('Failed to revoke token')
    }
  }

  const handleShowToken = async (tokenId: string) => {
    try {
      const response = await revealToken(tokenId)
      setRevealedToken(response)
    } catch (error) {
      console.error('Failed to reveal token:', error)
      toast.error('Failed to reveal token')
    }
  }

  const handleCopyToken = (token: string) => {
    navigator.clipboard.writeText(token)
    toast.success('Token copied to clipboard')
  }

  return (
    <TkPageWrapper title="API Tokens">
      <div className="space-y-6">
        {/* Create Token Form */}
        <TkCard>
          <TkCardHeader>
            <TkCardTitle>Create New Token</TkCardTitle>
          </TkCardHeader>
          <TkCardContent className="space-y-4">
            <div className="space-y-2">
              <TkLabel htmlFor="token-name">Token Name</TkLabel>
              <TkInput
                id="token-name"
                type="text"
                placeholder="Enter token name"
                value={newToken.name}
                onChange={(e) => setNewToken({ ...newToken, name: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <TkLabel htmlFor="token-expires">Expires in (days)</TkLabel>
              <TkInput
                id="token-expires"
                type="number"
                placeholder="Leave empty for no expiration"
                value={newToken.expires_in_days || ''}
                onChange={(e) =>
                  setNewToken({
                    ...newToken,
                    expires_in_days: e.target.value ? parseInt(e.target.value) : null,
                  })
                }
              />
            </div>
          </TkCardContent>
          <TkCardFooter className="flex justify-end">
            <TkButton disabled={!newToken.name} onClick={handleCreateToken}>
              Create Token
            </TkButton>
          </TkCardFooter>
        </TkCard>

        {/* New Token Display */}
        {createdToken && (
          <TkSuccessAlert title="Token Created Successfully">
            <div className="space-y-3">
              <p className="text-sm">
                Please copy this token now. For security reasons, you won't be able to see it again.
              </p>
              <TkCodeBlock maxHeight="h-auto">
                <code>{createdToken.token}</code>
              </TkCodeBlock>
              <TkButton variant="outline" size="sm" onClick={() => handleCopyToken(createdToken.token)}>
                Copy to Clipboard
              </TkButton>
            </div>
          </TkSuccessAlert>
        )}

        {/* Revealed Token Display */}
        {revealedToken && (
          <TkWarningAlert title={revealedToken.name}>
            <div className="space-y-3">
              <p className="text-sm">{revealedToken.message}</p>
              <TkCodeBlock maxHeight="h-auto">
                <code>{revealedToken.token}</code>
              </TkCodeBlock>
              <div className="flex gap-2">
                <TkButton variant="outline" size="sm" onClick={() => handleCopyToken(revealedToken.token)}>
                  Copy Token
                </TkButton>
                <TkButton variant="outline" size="sm" onClick={() => setRevealedToken(null)}>
                  Close
                </TkButton>
              </div>
            </div>
          </TkWarningAlert>
        )}

        {/* Token List */}
        <TkCard>
          <TkCardHeader>
            <TkCardTitle>Your Tokens</TkCardTitle>
          </TkCardHeader>
          <TkCardContent>
            {loading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="h-8 w-8 animate-spin text-primary" />
              </div>
            ) : tokens.length === 0 ? (
              <TkInfoAlert>No tokens found. Create your first token above.</TkInfoAlert>
            ) : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Name</TableHead>
                      <TableHead>Created</TableHead>
                      <TableHead>Expires</TableHead>
                      <TableHead>Last Used</TableHead>
                      <TableHead>Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {tokens.map((token) => (
                      <TableRow key={token.id} data-testid="token-card">
                        <TableCell>{token.name}</TableCell>
                        <TableCell>{formatDate(token.created_at)}</TableCell>
                        <TableCell>
                          {token.expires_at ? formatDate(token.expires_at) : 'Never'}
                        </TableCell>
                        <TableCell>
                          {token.last_used ? formatDate(token.last_used) : 'Never'}
                        </TableCell>
                        <TableCell>
                          <div className="flex gap-2">
                            {(token.name === 'CI/CD Monitoring' || token.name === 'MCP Default') && (
                              <TkButton
                                variant="default"
                                size="sm"
                                onClick={() => handleShowToken(token.id)}
                              >
                                Show
                              </TkButton>
                            )}
                            <TkButton
                              variant="destructive"
                              size="sm"
                              disabled={!token.is_active}
                              onClick={() => handleRevokeToken(token.id)}
                            >
                              {token.is_active ? 'Revoke' : 'Revoked'}
                            </TkButton>
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </TkCardContent>
        </TkCard>

        {/* Usage Instructions */}
        <TkCard>
          <TkCardHeader>
            <TkCardTitle>How to Use API Tokens</TkCardTitle>
          </TkCardHeader>
          <TkCardContent className="space-y-6">
            <div>
              <h3 className="text-lg font-semibold mb-2">VS Code Extension</h3>
              <p className="text-sm text-muted-foreground mb-3">
                Configure your Thinkube CI/CD VS Code extension with your API token:
              </p>
              <TkCodeBlock maxHeight="h-auto">
                <code>{`{
  "thinkube-cicd.apiToken": "tk_your_token_here"
}`}</code>
              </TkCodeBlock>
            </div>

            <div>
              <h3 className="text-lg font-semibold mb-2">Command Line</h3>
              <p className="text-sm text-muted-foreground mb-3">
                Use your token in API requests:
              </p>
              <TkCodeBlock maxHeight="h-auto">
                <code>{`curl -H "Authorization: Bearer tk_your_token_here" \\
  https://control.thinkube.com/api/v1/cicd/pipelines`}</code>
              </TkCodeBlock>
            </div>

            <div>
              <h3 className="text-lg font-semibold mb-2">MCP Integration</h3>
              <p className="text-sm text-muted-foreground mb-3">
                Set your token as an environment variable for MCP:
              </p>
              <TkCodeBlock maxHeight="h-auto">
                <code>{`export THINKUBE_API_TOKEN="tk_your_token_here"`}</code>
              </TkCodeBlock>
            </div>
          </TkCardContent>
        </TkCard>
      </div>
    </TkPageWrapper>
  )
}
