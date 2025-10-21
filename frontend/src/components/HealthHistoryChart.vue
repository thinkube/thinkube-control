<!-- src/components/HealthHistoryChart.vue -->
<template>
  <div class="health-chart">
    <div class="chart-container">
      <div 
        v-for="(item, index) in chartData" 
        :key="index"
        class="chart-bar"
        :class="`health-${item.status}`"
        :style="{ height: '100%', width: barWidth }"
        :title="`${item.status} - ${formatTime(item.checked_at)}`"
      />
    </div>
    <div class="chart-legend">
      <div class="legend-item">
        <div class="legend-dot health-healthy" />
        <span>{{ t('serviceDetails.healthy') }}</span>
      </div>
      <div class="legend-item">
        <div class="legend-dot health-unhealthy" />
        <span>{{ t('serviceDetails.unhealthy') }}</span>
      </div>
      <div class="legend-item">
        <div class="legend-dot health-unknown" />
        <span>{{ t('serviceDetails.unknown') }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  data: {
    type: Array,
    required: true
  }
})

const { t } = useI18n()

// Computed
const chartData = computed(() => {
  console.log('=== HealthHistoryChart Data Analysis ===')
  console.log('Total data points received:', props.data?.length)

  // Count statuses
  const statusCounts = {
    healthy: 0,
    unhealthy: 0,
    unknown: 0,
    other: 0
  }

  props.data?.forEach(item => {
    if (item.status === 'healthy') statusCounts.healthy++
    else if (item.status === 'unhealthy') statusCounts.unhealthy++
    else if (item.status === 'unknown') statusCounts.unknown++
    else statusCounts.other++
  })

  console.log('Status breakdown:', statusCounts)

  // Display all data points - 720 points for 24 hours (30 checks per hour)
  // Data comes newest-first from backend, so no need to reverse for left-to-right time flow
  const result = props.data.slice()
  console.log('Chart will display:', result.length, 'items (2-minute intervals)')
  console.log('=================================')
  return result
})

const barWidth = computed(() => {
  const count = chartData.value.length
  return count > 0 ? `${100 / count}%` : '0%'
})

// Methods
function formatTime(timestamp) {
  return new Date(timestamp).toLocaleString()
}
</script>

<style scoped>
.health-chart {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.chart-container {
  flex: 1;
  display: flex;
  align-items: flex-end;
  gap: 0;
  margin-bottom: 1rem;
}

.chart-bar {
  transition: opacity 0.2s;
  cursor: pointer;
}

.chart-bar:hover {
  opacity: 0.8;
}

.health-healthy {
  background-color: #10b981;
}

.health-unhealthy {
  background-color: #ef4444;
}

.health-unknown {
  background-color: #6b7280;
}

.health-disabled {
  background-color: #d1d5db;
}

.chart-legend {
  display: flex;
  gap: 1rem;
  justify-content: center;
  font-size: 0.75rem;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 0.25rem;
}

.legend-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
}
</style>