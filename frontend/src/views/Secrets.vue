<template>
  <div class="container">
    <div class="prose prose-lg mb-6">
      <h1>API Secrets Management</h1>
      <p>Manage API keys and secrets that can be used by deployed applications. Secrets are encrypted and stored securely.</p>
    </div>

    <!-- Action Buttons -->
    <div class="flex justify-end gap-2 mb-4">
      <button class="btn btn-secondary" @click="exportToNotebooks" :disabled="exporting || secrets.length === 0">
        <span v-if="exporting" class="loading loading-spinner loading-sm"></span>
        <svg v-else xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-6 h-6">
          <path stroke-linecap="round" stroke-linejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 12 12 16.5m0 0L7.5 12m4.5 4.5V3" />
        </svg>
        Export to Notebooks
      </button>
      <button class="btn btn-primary" @click="openCreateDialog">
        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-6 h-6">
          <path stroke-linecap="round" stroke-linejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
        </svg>
        Add Secret
      </button>
    </div>
    <!-- Secrets Table -->
    <div class="card bg-base-100 shadow-xl">
      <div class="card-body">
        <div v-if="loading" class="flex justify-center">
          <span class="loading loading-spinner loading-lg"></span>
        </div>
        
        <div v-else-if="secrets.length === 0" class="text-center py-8">
          <p class="text-base-content/60">No secrets found. Create your first secret to get started.</p>
        </div>
        
        <div v-else class="overflow-x-auto">
          <table class="table table-zebra">
            <thead>
              <tr>
                <th>Name</th>
                <th>Description</th>
                <th>Created</th>
                <th>Used By</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="secret in secrets" :key="secret.id">
                <td class="font-mono">{{ secret.name }}</td>
                <td>{{ secret.description || '-' }}</td>
                <td>{{ formatDate(secret.created_at) }}</td>
                <td>
                  <div v-if="secret.used_by_apps.length > 0" class="flex flex-wrap gap-1">
                    <span 
                      v-for="app in secret.used_by_apps" 
                      :key="app"
                      class="badge badge-primary badge-sm"
                    >
                      {{ app }}
                    </span>
                  </div>
                  <span v-else class="text-base-content/60">Not in use</span>
                </td>
                <td>
                  <div class="flex gap-2">
                    <button 
                      class="btn btn-ghost btn-sm"
                      @click="openEditDialog(secret)"
                      title="Edit"
                    >
                      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-4 h-4">
                        <path stroke-linecap="round" stroke-linejoin="round" d="m16.862 4.487 1.687-1.688a1.875 1.875 0 1 1 2.652 2.652L6.832 19.82a4.5 4.5 0 0 1-1.897 1.13l-2.685.8.8-2.685a4.5 4.5 0 0 1 1.13-1.897L16.863 4.487Zm0 0L19.5 7.125" />
                      </svg>
                    </button>
                    <button 
                      class="btn btn-ghost btn-sm"
                      @click="confirmDelete(secret)"
                      :disabled="secret.used_by_apps.length > 0"
                      title="Delete"
                    >
                      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-4 h-4">
                        <path stroke-linecap="round" stroke-linejoin="round" d="m14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0" />
                      </svg>
                    </button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- Create/Edit Modal -->
    <dialog id="secret_modal" class="modal" :open="dialog">
      <div class="modal-box">
        <h3 class="font-bold text-lg mb-4">
          {{ editingSecret ? 'Edit Secret' : 'Create Secret' }}
        </h3>
        
        <form @submit.prevent="saveSecret">
          <div class="form-control w-full mb-4">
            <label class="label">
              <span class="label-text">Secret Name</span>
            </label>
            <input
              v-model="secretForm.name"
              type="text"
              placeholder="e.g., HUGGINGFACE_TOKEN"
              class="input input-bordered w-full"
              :disabled="editingSecret"
              required
              pattern="^[A-Z][A-Z0-9_]*$"
              title="Must be UPPERCASE with underscores only"
            />
            <label class="label">
              <span class="label-text-alt">Use UPPERCASE_WITH_UNDERSCORES format</span>
            </label>
          </div>
          
          <div class="form-control w-full mb-4">
            <label class="label">
              <span class="label-text">Description (Optional)</span>
            </label>
            <textarea
              v-model="secretForm.description"
              class="textarea textarea-bordered"
              placeholder="What is this secret used for?"
              rows="2"
            ></textarea>
          </div>
          
          <div class="form-control w-full mb-6">
            <label class="label">
              <span class="label-text">
                {{ editingSecret ? 'New Value (leave empty to keep current)' : 'Secret Value' }}
              </span>
            </label>
            <div class="join w-full">
              <input
                v-model="secretForm.value"
                :type="showValue ? 'text' : 'password'"
                class="input input-bordered join-item w-full"
                :required="!editingSecret"
              />
              <button
                type="button"
                class="btn btn-square join-item"
                @click="showValue = !showValue"
              >
                <svg v-if="showValue" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-5 h-5">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M2.036 12.322a1.012 1.012 0 0 1 0-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178Z" />
                  <path stroke-linecap="round" stroke-linejoin="round" d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
                </svg>
                <svg v-else xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-5 h-5">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M3.98 8.223A10.477 10.477 0 0 0 1.934 12C3.226 16.338 7.244 19.5 12 19.5c.993 0 1.953-.138 2.863-.395M6.228 6.228A10.451 10.451 0 0 1 12 4.5c4.756 0 8.773 3.162 10.065 7.498a10.522 10.522 0 0 1-4.293 5.774M6.228 6.228 3 3m3.228 3.228 3.65 3.65m7.894 7.894L21 21m-3.228-3.228-3.65-3.65m0 0a3 3 0 1 0-4.243-4.243m4.242 4.242L9.88 9.88" />
                </svg>
              </button>
            </div>
          </div>
          
          <div class="modal-footer">
            <button type="button" class="btn" @click="closeDialog">Cancel</button>
            <button type="submit" class="btn btn-primary">
              {{ editingSecret ? 'Update' : 'Create' }}
            </button>
          </div>
        </form>
      </div>
      <div class="modal-backdrop" @click="closeDialog"></div>
    </dialog>

    <!-- Delete Confirmation Modal -->
    <dialog id="delete_modal" class="modal" :open="deleteDialog">
      <div class="modal-box">
        <h3 class="font-bold text-lg">Confirm Delete</h3>
        <p class="py-4">
          Are you sure you want to delete the secret "{{ secretToDelete?.name }}"?
          This action cannot be undone.
        </p>
        <div class="modal-footer">
          <button class="btn" @click="deleteDialog = false">Cancel</button>
          <button class="btn btn-error" @click="deleteSecret">Delete</button>
        </div>
      </div>
      <div class="modal-backdrop" @click="deleteDialog = false"></div>
    </dialog>
  </div>
</template>

<script>
import { ref, onMounted } from 'vue'
import { api } from '@/services/api'

export default {
  name: 'Secrets',
  setup() {
    const secrets = ref([])
    const loading = ref(false)
    const exporting = ref(false)
    const dialog = ref(false)
    const deleteDialog = ref(false)
    const editingSecret = ref(null)
    const secretToDelete = ref(null)
    const showValue = ref(false)

    const secretForm = ref({
      name: '',
      description: '',
      value: ''
    })
    
    const fetchSecrets = async () => {
      loading.value = true
      try {
        const response = await api.get('/secrets/')
        secrets.value = response.data
      } catch (error) {
        console.error('Failed to fetch secrets:', error)
      } finally {
        loading.value = false
      }
    }
    
    const openCreateDialog = () => {
      editingSecret.value = null
      secretForm.value = {
        name: '',
        description: '',
        value: ''
      }
      showValue.value = false
      dialog.value = true
    }
    
    const openEditDialog = (secret) => {
      editingSecret.value = secret
      secretForm.value = {
        name: secret.name,
        description: secret.description || '',
        value: ''
      }
      showValue.value = false
      dialog.value = true
    }
    
    const closeDialog = () => {
      dialog.value = false
      editingSecret.value = null
      secretForm.value = {
        name: '',
        description: '',
        value: ''
      }
      showValue.value = false
    }
    
    const saveSecret = async () => {
      try {
        if (editingSecret.value) {
          // Update existing secret
          const updateData = {
            description: secretForm.value.description
          }
          if (secretForm.value.value) {
            updateData.value = secretForm.value.value
          }
          await api.put(`/secrets/${editingSecret.value.id}`, updateData)
        } else {
          // Create new secret
          await api.post('/secrets/', secretForm.value)
        }
        closeDialog()
        fetchSecrets()
      } catch (error) {
        console.error('Failed to save secret:', error)
        alert(error.response?.data?.detail || 'Failed to save secret')
      }
    }
    
    const confirmDelete = (secret) => {
      secretToDelete.value = secret
      deleteDialog.value = true
    }
    
    const deleteSecret = async () => {
      try {
        await api.delete(`/secrets/${secretToDelete.value.id}`)
        deleteDialog.value = false
        secretToDelete.value = null
        fetchSecrets()
      } catch (error) {
        console.error('Failed to delete secret:', error)
        alert(error.response?.data?.detail || 'Failed to delete secret')
      }
    }
    
    const formatDate = (dateString) => {
      if (!dateString) return ''
      return new Date(dateString).toLocaleString()
    }

    const exportToNotebooks = async () => {
      exporting.value = true
      try {
        const response = await api.post('/secrets/export-to-notebooks')
        alert(`✅ ${response.data.message}\n\nSecrets available at: ${response.data.path}`)
      } catch (error) {
        console.error('Failed to export secrets:', error)
        alert(error.response?.data?.detail || 'Failed to export secrets to notebooks')
      } finally {
        exporting.value = false
      }
    }

    onMounted(() => {
      fetchSecrets()
    })

    return {
      secrets,
      loading,
      exporting,
      dialog,
      deleteDialog,
      editingSecret,
      secretToDelete,
      showValue,
      secretForm,
      fetchSecrets,
      openCreateDialog,
      openEditDialog,
      closeDialog,
      saveSecret,
      confirmDelete,
      deleteSecret,
      formatDate,
      exportToNotebooks
    }
  }
}
</script>