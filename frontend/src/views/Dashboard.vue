<!-- src/views/Dashboard.vue -->
<template>
  <div class="container pt-2">
    <div
      class="flex justify-between items-center mb-4"
    >
      <!-- Compact Mode Toggle -->
      <div class="flex items-center gap-2">
        <label class="label cursor-pointer gap-2">
          <span class="label-text text-sm">{{ t('dashboard.compactMode') }}</span>
          <input
            type="checkbox"
            class="toggle toggle-sm"
            v-model="compactMode"
            @change="saveCompactMode"
          />
        </label>
      </div>

      <button
        class="btn btn-xs btn-ghost"
        :disabled="syncing"
        @click="handleSync"
      >
        <ArrowPathIcon
          class="size-4"
          :class="{ 'animate-spin': syncing }"
        />
        {{ t('dashboard.syncServices') }}
      </button>
    </div>
    
    <div
      v-if="servicesStore.loading"
      class="hero"
    >
      <div class="hero-content">
        <span class="loading loading-spinner loading-lg" />
      </div>
    </div>
    
    <div
      v-else-if="servicesStore.error"
      class="alert alert-error alert-soft"
    >
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
          d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z"
        />
      </svg>
      <span>{{ servicesStore.error }}</span>
    </div>
    
    <div v-else>
      <!-- Tab Navigation -->
      <div class="tabs tabs-boxed mb-4">
        <button 
          class="tab"
          :class="{ 'tab-active': activeTab === 'favorites' }"
          @click="activeTab = 'favorites'"
        >
          <StarIcon class="size-4 mr-1" />
          {{ t('dashboard.tabs.favorites') }}
        </button>
        <button 
          class="tab"
          :class="{ 'tab-active': activeTab === 'all' }"
          @click="activeTab = 'all'"
        >
          <Squares2X2Icon class="size-4 mr-1" />
          {{ t('dashboard.tabs.all') }}
        </button>
      </div>

      <!-- Favorites Tab -->
      <div v-if="activeTab === 'favorites'">
        <div
          v-if="servicesStore.favoriteServicesComputed.length === 0"
          class="alert alert-info"
        >
          <StarIcon class="size-6" />
          <span>{{ t('dashboard.noFavorites') }}</span>
        </div>
        
        <draggable
          v-else
          v-model="favoritesList"
          class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4"
          item-key="id"
          @end="onDragEnd"
          :animation="200"
          handle=".drag-handle"
        >
          <template #item="{ element }">
            <div class="flex">
              <FavoriteServiceCard
                :service="element"
                :compact="compactMode"
                @toggle-favorite="handleToggleFavorite"
                @show-details="showServiceDetails"
                class="w-full"
              />
            </div>
          </template>
        </draggable>
      </div>

      <!-- All Services Tab -->
      <div v-if="activeTab === 'all'">
        <!-- Category Filter -->
        <div class="mb-4">
          <div class="join">
            <button 
              class="btn btn-sm join-item" 
              :class="{ 'btn-primary': !servicesStore.categoryFilter }" 
              @click="servicesStore.setCategoryFilter(null)"
            >
              {{ t('dashboard.categories.all') }}
            </button>
          
            <button 
              v-for="category in servicesStore.categories" 
              :key="category"
              class="btn btn-sm join-item" 
              :class="{ 'btn-primary': servicesStore.categoryFilter === category }"
              @click="servicesStore.setCategoryFilter(category)"
            >
              {{ t(`dashboard.categories.${category}`) }}
            </button>
          </div>
        </div>
      
        <div
          v-if="servicesStore.filteredServices.length === 0"
          class="alert alert-info"
        >
          <InformationCircleIcon class="size-6" />
          <span>{{ t('dashboard.noServices') }}</span>
        </div>
      
        <!-- All Services Grid -->
        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          <div v-for="service in filteredServicesWithUrls" :key="service.id" class="flex">
            <ServiceCard
              :service="service"
              :compact="compactMode"
              @toggle="handleToggleService"
              @restart="handleRestartService"
              @show-details="showServiceDetails"
              @toggle-favorite="handleToggleFavorite"
              class="w-full"
            />
          </div>
        </div>
      </div>
    </div>
    
    <!-- Service Details Modal -->
    <ServiceDetailsModal 
      v-if="selectedService"
      :service="selectedService"
      @close="selectedService = null"
    />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { InformationCircleIcon, ArrowPathIcon, StarIcon, Squares2X2Icon } from '@heroicons/vue/24/outline'
import { useServicesStore } from '@/stores/services'
import ServiceCard from '@/components/ServiceCard.vue'
import FavoriteServiceCard from '@/components/FavoriteServiceCard.vue'
import ServiceDetailsModal from '@/components/ServiceDetailsModal.vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import draggable from 'vuedraggable'

const { t } = useI18n()
const servicesStore = useServicesStore()

const syncing = ref(false)
const selectedService = ref(null)
const activeTab = ref('favorites') // Start with favorites tab
const compactMode = ref(false)

// Reactive favorites list for drag and drop
const favoritesList = ref([])

// Save compact mode preference
const saveCompactMode = () => {
  localStorage.setItem('dashboardCompactMode', compactMode.value.toString())
}

// Watch for changes in favorites from store
watch(
  () => servicesStore.favoriteServicesComputed,
  (newFavorites) => {
    favoritesList.value = [...newFavorites]
    
    // Auto-switch to All Services tab if favorites becomes empty
    if (newFavorites.length === 0 && activeTab.value === 'favorites') {
      activeTab.value = 'all'
    }
  },
  { immediate: true, deep: true }
)

// Computed properties for filtered services with URLs
const filteredServicesWithUrls = computed(() => 
  servicesStore.filteredServices.filter(s => s.url)
)

// Methods
async function handleSync() {
  syncing.value = true
  try {
    await servicesStore.syncServices()
    ElMessage.success(t('dashboard.syncSuccess'))
  } catch (error) {
    ElMessage.error(t('dashboard.syncError'))
  } finally {
    syncing.value = false
  }
}

async function handleToggleService(service, enabled) {
  try {
    const action = enabled ? 'enable' : 'disable'
    const message = enabled 
      ? t('dashboard.confirmEnable', { name: service.display_name })
      : t('dashboard.confirmDisable', { name: service.display_name })
    
    await ElMessageBox.confirm(message, t('dashboard.confirm'), {
      confirmButtonText: t('common.confirm'),
      cancelButtonText: t('common.cancel'),
      type: 'warning'
    })
    
    await servicesStore.toggleService(service.id, enabled)
    ElMessage.success(t(`dashboard.${action}Success`, { name: service.display_name }))
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error(error.response?.data?.detail || t('dashboard.toggleError'))
    }
  }
}

async function handleRestartService(service) {
  try {
    await ElMessageBox.confirm(
      t('dashboard.confirmRestart', { name: service.display_name }),
      t('dashboard.confirm'),
      {
        confirmButtonText: t('common.confirm'),
        cancelButtonText: t('common.cancel'),
        type: 'warning'
      }
    )
    
    await servicesStore.restartService(service.id)
    ElMessage.success(t('dashboard.restartSuccess', { name: service.display_name }))
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error(error.response?.data?.detail || t('dashboard.restartError'))
    }
  }
}

function showServiceDetails(service) {
  selectedService.value = service
  servicesStore.fetchServiceDetails(service.id)
  servicesStore.fetchHealthHistory(service.id)
}

async function handleToggleFavorite(service) {
  try {
    // Store the current state before toggling
    const wasFavorite = service.is_favorite
    await servicesStore.toggleFavorite(service)
    // Use the opposite of the previous state for the message
    const action = wasFavorite ? 'removed from' : 'added to'
    ElMessage.success(t('dashboard.favoriteSuccess', { name: service.display_name, action }))
  } catch (error) {
    ElMessage.error(t('dashboard.favoriteError'))
  }
}

async function onDragEnd() {
  try {
    // Get the new order of service IDs
    const serviceIds = favoritesList.value.map(service => service.id)
    await servicesStore.reorderFavorites(serviceIds)
  } catch (error) {
    ElMessage.error(t('dashboard.reorderError'))
    // Refresh to restore original order
    await servicesStore.fetchServices()
  }
}

onMounted(async () => {
  // Load compact mode preference
  const savedCompactMode = localStorage.getItem('dashboardCompactMode')
  if (savedCompactMode !== null) {
    compactMode.value = savedCompactMode === 'true'
  }

  await servicesStore.fetchServices()
})
</script>