<template>
  <v-chart
    class="chart"
    :option="chartOption"
    autoresize
    @updateAxisPointer="handleAxisPointer"
  />
</template>

<script setup>
import { computed } from 'vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { LineChart, ScatterChart } from 'echarts/charts'
import {
  TitleComponent,
  TooltipComponent,
  GridComponent,
  LegendComponent,
  DataZoomComponent,
  ToolboxComponent
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

use([
  CanvasRenderer,
  LineChart,
  ScatterChart,
  TitleComponent,
  TooltipComponent,
  GridComponent,
  LegendComponent,
  DataZoomComponent,
  ToolboxComponent
])

const props = defineProps({
  weightData: { type: Object, required: true },
  extraPoints: { type: Array, default: () => [] },
  currentTime: { type: Number, required: true },
  title: { type: String, default: 'Weight' }
})

const emit = defineEmits(['time-updated'])

function handleAxisPointer(event) {
  const axisInfo = event?.axesInfo?.[0]
  if (typeof axisInfo?.value === 'number') {
    emit('time-updated', axisInfo.value)
  }
}

const chartOption = computed(() => ({
  title: { text: props.title, left: 'center' },
  tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
  xAxis: { type: 'value', name: 'Time (s)' },
  yAxis: { type: 'value', name: 'Weight', scale: true },
  grid: { left: '5%', right: '5%', bottom: '23%' },
  legend: { data: ['Weight', 'Extra Points'], bottom: 40 },

  series: [
    {
      name: 'Weight',
      type: 'line',
      data: props.weightData.time.map((t, i) => [t, props.weightData.value[i]]),
      symbolSize: 1,
      lineStyle: { width: 2 },
      itemStyle: { color: '#E83E8C' }
    },
    {
      name: 'Extra Points',
      type: 'scatter',
      data: props.extraPoints.map(p => [p.time, p.value, p.name]),
      symbolSize: 10,
      itemStyle: { color: 'red' },
      label: {
        show: true,
        formatter: function(params) {
          return params.data[2] || "";
        },
        position: "top"
      }
    },
    // {
    //   type: 'line',
    //   silent: true,
    //   lineStyle: { opacity: 0 },
    //   markLine: {
    //     symbol: 'none',
    //     label: { show: false },
    //     data: [{ xAxis: props.currentTime }]
    //   }
    // }
  ],

  toolbox: { feature: { dataZoom: { yAxisIndex: 'all' }, restore: {} } },
  dataZoom: [
    {
      type: 'slider',
      xAxisIndex: 0,
      filterMode: 'none'
    },
    {
      type: 'inside',
      xAxisIndex: 0,
      filterMode: 'none'
    },
    {
      type: 'slider',
      yAxisIndex: 0,
      filterMode: 'none'
    },
    {
      type: 'inside',
      yAxisIndex: 0,
      filterMode: 'none'
    },
  ]

}))
</script>

<style scoped>
.chart { height: 350px; margin-top: 15px; }
</style>
