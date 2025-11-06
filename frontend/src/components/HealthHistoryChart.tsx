import { TkHealthChart, HealthCheckData } from 'thinkube-style/components/data-viz'

interface HealthHistoryChartProps {
  data: HealthCheckData[]
}

/**
 * HealthHistoryChart - Displays service health check history
 *
 * Wrapper component around TkHealthChart for displaying service health status over time.
 * Data points represent health checks at 2-minute intervals (720 points for 24 hours).
 */
export function HealthHistoryChart({ data }: HealthHistoryChartProps) {
  return (
    <TkHealthChart
      data={data}
      height="200px"
      showLegend={true}
    />
  )
}

export default HealthHistoryChart
