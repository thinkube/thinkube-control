<template>
  <div class="container">
    <div class="prose prose-lg">
      <h1>{{ t('apiTokens.title') }}</h1>
    </div>
    
    <!-- Create Token Form -->
    <div class="card">
      <div class="card-body">
        <h2 class="card-title">
          {{ t('apiTokens.createToken.title') }}
        </h2>
        <fieldset class="fieldset">
          <label
            class="label"
            for="token-name"
          >{{ t('apiTokens.createToken.nameLabel') }}</label>
          <input 
            id="token-name"
            v-model="newToken.name" 
            type="text" 
            :placeholder="t('apiTokens.createToken.namePlaceholder')" 
            class="input"
          >
        </fieldset>
        <fieldset class="fieldset">
          <label
            class="label"
            for="token-expires"
          >{{ t('apiTokens.createToken.expiresLabel') }}</label>
          <input 
            id="token-expires"
            v-model.number="newToken.expires_in_days" 
            type="number" 
            :placeholder="t('apiTokens.createToken.expiresPlaceholder')" 
            class="input"
          >
        </fieldset>
        <div class="card-actions justify-end">
          <button 
            class="btn btn-primary" 
            :disabled="!newToken.name"
            @click="createToken"
          >
            {{ t('apiTokens.createToken.createButton') }}
          </button>
        </div>
      </div>
    </div>

    <!-- New Token Display -->
    <div
      v-if="createdToken"
      class="alert alert-success shadow-lg"
    >
      <div>
        <svg
          xmlns="http://www.w3.org/2000/svg"
          class="h-6 w-6 shrink-0 stroke-current"
          fill="none"
          viewBox="0 0 24 24"
        >
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            stroke-width="2"
            d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"
          />
        </svg>
        <div>
          <h3 class="font-bold">
            {{ t('apiTokens.tokenCreated.title') }}
          </h3>
          <p class="text-sm">
            {{ t('apiTokens.tokenCreated.message') }}
          </p>
          <div class="mockup-code overflow-x-auto max-w-full">
            <pre class="whitespace-pre-wrap break-all"><code>{{ createdToken.token }}</code></pre>
          </div>
          <button
            class="btn btn-sm btn-ghost"
            title="Copy to clipboard"
            @click="copyToken"
          >
            {{ t('apiTokens.tokenCreated.copyButton') }}
          </button>
        </div>
      </div>
    </div>

    <!-- Token List -->
    <div class="card">
      <div class="card-body">
        <h2 class="card-title">
          {{ t('apiTokens.tokenList.title') }}
        </h2>
        <div
          v-if="tokenStore.loading"
          class="hero"
        >
          <div class="hero-content">
            <span class="loading loading-spinner loading-lg" />
          </div>
        </div>
        <div
          v-else-if="tokenStore.tokens.length === 0"
          class="alert alert-info alert-soft"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            fill="none"
            viewBox="0 0 24 24"
            class="h-6 w-6 shrink-0 stroke-current"
          >
            <path
              stroke-linecap="round"
              stroke-linejoin="round"
              stroke-width="2"
              d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
            />
          </svg>
          <span>{{ t('apiTokens.tokenList.noTokens') }}</span>
        </div>
        <div
          v-else
          class="overflow-x-auto"
        >
          <table class="table">
            <thead>
              <tr>
                <th>{{ t('apiTokens.tokenList.table.name') }}</th>
                <th>{{ t('apiTokens.tokenList.table.created') }}</th>
                <th>{{ t('apiTokens.tokenList.table.expires') }}</th>
                <th>{{ t('apiTokens.tokenList.table.lastUsed') }}</th>
                <th>{{ t('apiTokens.tokenList.table.actions') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="token in tokenStore.tokens"
                :key="token.id"
                data-testid="token-card"
              >
                <td>{{ token.name }}</td>
                <td>{{ formatDate(token.created_at) }}</td>
                <td>{{ token.expires_at ? formatDate(token.expires_at) : t('apiTokens.tokenList.table.never') }}</td>
                <td>{{ token.last_used ? formatDate(token.last_used) : t('apiTokens.tokenList.table.never') }}</td>
                <td>
                  <div class="flex gap-2">
                    <!-- Show token button for system tokens -->
                    <button
                      v-if="token.name === 'CI/CD Monitoring' || token.name === 'MCP Default'"
                      class="btn btn-sm btn-primary"
                      @click="showToken(token.id)"
                    >
                      {{ t('apiTokens.tokenList.table.show') || 'Show' }}
                    </button>
                    <button
                      class="btn btn-sm btn-error"
                      :disabled="!token.is_active"
                      @click="revokeToken(token.id)"
                    >
                      {{ token.is_active ? t('apiTokens.tokenList.table.revoke') : t('apiTokens.tokenList.table.revoked') }}
                    </button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- Revealed Token Modal -->
    <div
      v-if="revealedToken"
      class="alert alert-warning shadow-lg"
    >
      <div>
        <svg
          xmlns="http://www.w3.org/2000/svg"
          class="h-6 w-6 shrink-0 stroke-current"
          fill="none"
          viewBox="0 0 24 24"
        >
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            stroke-width="2"
            d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
          />
        </svg>
        <div>
          <h3 class="font-bold">
            {{ revealedToken.name }}
          </h3>
          <p class="text-sm">
            {{ revealedToken.message }}
          </p>
          <div class="mockup-code overflow-x-auto max-w-full">
            <pre class="whitespace-pre-wrap break-all"><code>{{ revealedToken.token }}</code></pre>
          </div>
          <div class="flex gap-2">
            <button
              class="btn btn-sm btn-ghost"
              title="Copy to clipboard"
              @click="copyRevealedToken"
            >
              Copy Token
            </button>
            <button
              class="btn btn-sm btn-ghost"
              @click="revealedToken = null"
            >
              Close
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Usage Instructions -->
    <div class="card">
      <div class="card-body">
        <h2 class="card-title">
          {{ t('apiTokens.usage.title') }}
        </h2>
        <div class="prose">
          <div class="divider" />
          <h3 class="text-lg font-semibold">
            {{ t('apiTokens.usage.vscode.title') }}
          </h3>
          <p>{{ t('apiTokens.usage.vscode.description') }}</p>
          <pre><code>{
  "thinkube-cicd.apiToken": "tk_your_token_here"
}</code></pre>

          <div class="divider" />
          <h3 class="text-lg font-semibold">
            {{ t('apiTokens.usage.cli.title') }}
          </h3>
          <p>{{ t('apiTokens.usage.cli.description') }}</p>
          <pre><code>curl -H "Authorization: Bearer tk_your_token_here" \
  https://control.thinkube.com/api/v1/cicd/pipelines</code></pre>

          <div class="divider" />
          <h3 class="text-lg font-semibold">
            {{ t('apiTokens.usage.mcp.title') }}
          </h3>
          <p>{{ t('apiTokens.usage.mcp.description') }}</p>
          <pre><code>export THINKUBE_API_TOKEN="tk_your_token_here"</code></pre>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useTokenStore } from '@/stores/tokens'

const { t } = useI18n()
const tokenStore = useTokenStore()

const newToken = ref({
  name: '',
  expires_in_days: null,
  scopes: []
})
const createdToken = ref(null)
const revealedToken = ref(null)

const formatDate = (dateString) => {
  return new Date(dateString).toLocaleString()
}

const createToken = async () => {
  // Validate token name
  if (!newToken.value.name || newToken.value.name.trim() === '') {
    alert(t('apiTokens.errors.nameMissing'))
    return
  }
  
  try {
    createdToken.value = await tokenStore.createToken(newToken.value)
    newToken.value = { name: '', expires_in_days: null, scopes: [] }
  } catch (error) {
    console.error('Failed to create token:', error)
    alert(t('apiTokens.errors.createFailed'))
  }
}

const revokeToken = async (tokenId) => {
  if (!confirm(t('apiTokens.tokenList.confirmRevoke'))) {
    return
  }
  
  try {
    await tokenStore.revokeToken(tokenId)
  } catch (error) {
    console.error('Failed to revoke token:', error)
    alert(t('apiTokens.errors.revokeFailed'))
  }
}

const copyToken = () => {
  navigator.clipboard.writeText(createdToken.value.token)
  alert(t('apiTokens.tokenCreated.copied'))
}

const showToken = async (tokenId) => {
  try {
    const response = await tokenStore.revealToken(tokenId)
    revealedToken.value = response
  } catch (error) {
    console.error('Failed to reveal token:', error)
    alert(t('apiTokens.errors.revealFailed') || 'Failed to reveal token')
  }
}

const copyRevealedToken = () => {
  navigator.clipboard.writeText(revealedToken.value.token)
  alert(t('apiTokens.tokenCreated.copied') || 'Token copied to clipboard')
}

onMounted(async () => {
  await tokenStore.fetchTokens()
})
</script>

<!-- 🤖 Generated with Claude -->