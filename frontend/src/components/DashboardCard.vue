<!-- src/components/DashboardCard.vue -->
<template>
  <div class="card">
    <div class="card-body">
      <h2 class="card-title">
        {{ dashboard.name }}
        <div :class="getCategoryBadgeClass(dashboard.category)">
          {{ t(`dashboard.categories.${dashboard.category}`) }}
        </div>
      </h2>
      
      <p>{{ dashboard.description }}</p>
      
      <div class="card-actions justify-end">
        <a
          :href="dashboard.url"
          target="_blank"
          rel="noopener noreferrer"
          class="btn btn-primary"
        >
          {{ t('dashboard.open') }}
          <ArrowTopRightOnSquareIcon class="icon-sm" />
        </a>
      </div>
    </div>
  </div>
</template>

<script setup>
import { useI18n } from 'vue-i18n'
import { ArrowTopRightOnSquareIcon } from '@heroicons/vue/20/solid'

const { t } = useI18n()

const props = defineProps({
  dashboard: {
    type: Object,
    required: true
  }
})

const getCategoryBadgeClass = (category) => {
  const badgeMap = {
    storage: 'badge badge-warning',
    monitoring: 'badge badge-success',
    devops: 'badge badge-info',
    ai: 'badge badge-secondary',
    database: 'badge badge-primary',
    development: 'badge badge-accent',
    documentation: 'badge badge-ghost',
    security: 'badge badge-error',
    infrastructure: 'badge badge-neutral'
  }
  return badgeMap[category] || 'badge'
}
</script>