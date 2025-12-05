<template>
  <v-chart
    ref="chartComponentRef"
    class="chart"
    :option="chartOption"
    autoresize
    @mousemove="handleChartMouseMove"
    @legendselectchanged="handleLegendChange"
  />
</template>

<script setup>
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import {
  TitleComponent,
  TooltipComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  DataZoomComponent,
  ToolboxComponent,
} from 'echarts/components'
import VChart from 'vue-echarts'
import { ref, computed, watch } from 'vue'

use([
  CanvasRenderer,
  LineChart,
  TitleComponent,
  TooltipComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  DataZoomComponent,
  ToolboxComponent,
])

// --- Props and Emits ---

const props = defineProps({
  chartData: {
    type: Array,
    required: true,
  },
  title: {
    type: String,
    default: '',
  },
  currentTime: {
    type: Number,
    required: true,
  },
  yAxisType: {
    type: String,
    default: 'value', // 'value' or 'category'
  },
})

const emit = defineEmits(['time-updated'])

// --- Refs ---
const chartComponentRef = ref(null)
const legendState = ref({})

// --- State Synchronization ---

watch(() => props.chartData, (newData) => {
  const newLegendState = {}
  if (newData) {
    newData.forEach(series => {
      newLegendState[series.name] = true
    })
  }
  legendState.value = newLegendState
}, { deep: true, immediate: true })

function handleLegendChange(params) {
  legendState.value = params.selected
}


// --- Chart Configuration ---

const colorMap = {
  truck: '#007BFF',
  screen: '#28A745',
  revolver: '#DC3545',
  screw: '#FFC107',
  pump: '#17A2B8',
  lifter: '#6F42C1',
  spade: '#FD7E14',
  clearance: '#20C997',
  mixer: '#6610F2',
  weight: '#E83E8C',
  default: '#6C757D',
}

const allCategoryValues = computed(() => {
  if (props.yAxisType !== 'category') return [];
  const allValues = new Set();
  props.chartData.forEach(series => {
    series.data.value.forEach(val => allValues.add(val));
  });
  return Array.from(allValues);
});



const chartOption = computed(() => {
  // 1. Create visible series from chart data
  const isWeightChart = props.title && props.title.toLowerCase().includes("weight")
  const dataSeries = props.chartData.map((seriesItem) => {
    const data = seriesItem.data.time.map((t, i) => [t, seriesItem.data.value[i]])
    return {
      name: seriesItem.name,
      data: data,
      type: 'line',
      step: props.yAxisType === 'category' ? 'start' : false,
      showSymbol: isWeightChart, // show points only for Weight chart
      animation: false,
      lineStyle: { width: 2 },
      itemStyle: { color: colorMap[seriesItem.name] || colorMap.default },
    }
  })

  // 2. Create a separate, invisible "phantom" series to host the markLine
  const markLineHostSeries = {
    name: '__MARKLINE_HOST__',
    type: 'line',
    silent: true,
    lineStyle: { opacity: 0 },
    showSymbol: false,
    markLine: {
      symbol: 'none',
      animation: false,
      lineStyle: {
        color: '#DC3545',
        type: 'solid',
        width: 2,
      },
      label: { show: false },
      data: [], // does not depend on currentTime!
    },
  }

  // Add dataZoom only for the Weight chart
  const option = {
    title: {
      text: props.title,
      left: 'center',
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross', animation: false, label: { backgroundColor: '#6a7985' } },
      formatter: (params) => {
        const filteredParams = params.filter(p => p.seriesName !== '__MARKLINE_HOST__');
        if (filteredParams.length === 0) return null;

        const time = `Time: ${filteredParams[0].axisValue.toFixed(3)}s<br/>`;
        const values = filteredParams.map((p) => `${p.marker} ${p.seriesName}: ${p.value[1] || 'N/A'}`).join('<br/>');
        return time + values;
      },
    },
    grid: { left: '3%', right: '4%', bottom: '15%', containLabel: true },
    xAxis: { type: 'value', name: 'Time (s)', axisLabel: { formatter: '{value} s' } },
    yAxis: {
      type: props.yAxisType,
      data: props.yAxisType === 'category' ? allCategoryValues.value : undefined,
    },
    legend: {
      data: dataSeries.map((s) => s.name),
      bottom: 5,
      type: 'scroll',
      selected: legendState.value,
    },
    series: [...dataSeries, markLineHostSeries],
  }
  if (isWeightChart) {
    option.toolbox = {
      feature: {
        dataZoom: {
          yAxisIndex: 'all',
          title: {
            zoom: 'Zoom Y',
            back: 'Reset zoom'
          }
        },
        restore: { title: 'Reset' }
      },
      right: 20
    }
    option.dataZoom = [
      {
        type: 'slider',
        yAxisIndex: 0,
        filterMode: 'none',
        show: true,
        width: 16,
        right: 0,
        top: 40,
        bottom: 60,
        handleSize: 20,
        backgroundColor: '#f2f2f2',
        fillerColor: '#e83e8c33',
        borderColor: '#e83e8c',
        showDetail: true,
        realtime: true
      },
      {
        type: 'inside',
        yAxisIndex: 0,
        filterMode: 'none',
        zoomOnMouseWheel: true,
        moveOnMouseMove: true,
        moveOnMouseWheel: true
      }
    ]
  }
  return option
})

// Update only markLine when currentTime changes, to avoid resetting dataZoom
import { nextTick } from 'vue'
watch(() => props.currentTime, (newTime) => {
  nextTick(() => {
    const chart = chartComponentRef.value && chartComponentRef.value.getEchartsInstance && chartComponentRef.value.getEchartsInstance()
    if (chart) {
      // Update only markLine for the required series, do not touch other options
      chart.setOption({
        series: chart.getOption().series.map(s =>
          s.name === '__MARKLINE_HOST__'
            ? { ...s, markLine: { ...s.markLine, data: [{ xAxis: newTime }] } }
            : {}
        )
      }, false, false)
    }
  })
})

function handleChartMouseMove(params) {
  if (params && params.value) {
    const time = params.value[0]
    emit('time-updated', time)
  }
}
</script>

<style scoped>
.chart {
  height: 350px;
  margin-top: 15px;
}
</style>
