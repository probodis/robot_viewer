import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import RobotViewer from '../src/views/RobotViewer.vue'
import api from '../src/services/api'

vi.mock('../src/services/api', () => ({
  default: {
    getOrderData: vi.fn(),
    getOrderLogs: vi.fn(),
    getBackendVersion: vi.fn(),
    getMachineLogs: vi.fn(),
    getOrderLogWindow: vi.fn(),
    getScannerArchive: vi.fn(),
  },
}))

const MACHINE_ID = 'cb-3-0007'
const ORDER_ID = '1794011078.328'
const DEFAULT_KEY = 'orders/2026-11-07_orders.txt.gz'
const VIDEO_URL = 'https://video.example.invalid/order.mp4'
const wrappers = []

function deferred() {
  let resolve
  let reject
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function orderResponse(uid = ORDER_ID) {
  const node = {
    velocity: { time: [0, 1], value: [0, 1] },
    position: { time: [0, 1], value: [0, 1] },
    state: { time: [0, 1], value: ['idle', 'running'] },
  }
  return {
    data: {
      order_id: uid,
      start_time: Number(uid),
      end_time: Number(uid) + 16.85,
      video_path: VIDEO_URL,
      video_status: 'referenced',
      telemetry_status: 'available',
      motors: { truck: node, screen: { ...node, weight: { time: [0, 1], value: [100, 110] } } },
      extra_weight_points: [],
      logs: {},
      diagnostics_status: 'on_demand',
    },
  }
}

function logsResponse(text, key = DEFAULT_KEY) {
  return {
    data: {
      logs: { [key]: { path: key, text: btoa(text), status: 'available', truncated: false } },
      status: 'available',
      truncated: false,
      selected_files: 1,
      loaded_files: 1,
    },
  }
}

function buttonMatching(wrapper, pattern) {
  const result = wrapper.findAll('button').find((button) => pattern.test(button.text()))
  if (!result)
    throw new Error(
      `No button matching ${pattern}; visible buttons: ${wrapper
        .findAll('button')
        .map((b) => b.text())
        .join(', ')}`,
    )
  return result
}

async function mountViewer() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/robot_viewer', name: 'robot-viewer', component: RobotViewer },
      { path: '/logfile', name: 'logfile-view', component: { template: '<div />' } },
    ],
  })
  await router.push({ name: 'robot-viewer', query: { machine_id: MACHINE_ID, order_id: ORDER_ID } })
  await router.isReady()
  const wrapper = mount(RobotViewer, {
    global: {
      plugins: [router],
      stubs: {
        TelemetryChart: {
          props: ['title'],
          template: '<div data-testid="telemetry-chart">{{ title }}</div>',
        },
        WeightChart: { template: '<div data-testid="weight-chart">Weight</div>' },
      },
    },
  })
  wrappers.push(wrapper)
  await flushPromises()
  return { wrapper, router }
}

beforeEach(() => {
  vi.resetAllMocks()
  api.getOrderData.mockImplementation((_machine, uid) => Promise.resolve(orderResponse(uid)))
  api.getBackendVersion.mockResolvedValue({ data: { version: '0.8.1' } })
  api.getMachineLogs.mockResolvedValue({
    data: [{ log_key: DEFAULT_KEY, filename: '2026-11-07_orders.txt.gz', size_bytes: 100 }],
  })
})

afterEach(() => {
  for (const wrapper of wrappers.splice(0)) wrapper.unmount()
})

describe('independent order media and automatic diagnostics', () => {
  it('renders video and telemetry while automatic logs are still pending', async () => {
    const logs = deferred()
    api.getOrderLogs.mockReturnValue(logs.promise)
    const { wrapper } = await mountViewer()

    expect(api.getOrderLogs).toHaveBeenCalledTimes(1)
    expect(wrapper.get('video').attributes('src')).toBe(VIDEO_URL)
    expect(wrapper.findAll('[data-testid="telemetry-chart"]')).toHaveLength(3)
    expect(wrapper.find('[data-testid="weight-chart"]').exists()).toBe(true)
    expect(wrapper.text()).toMatch(/loading.*logs/i)
    expect(api.getMachineLogs).not.toHaveBeenCalled()
    expect(api.getScannerArchive).not.toHaveBeenCalled()
  })

  it('keeps media and charts after log failure and retries only diagnostics', async () => {
    const logs = deferred()
    api.getOrderLogs
      .mockReturnValueOnce(logs.promise)
      .mockResolvedValueOnce(logsResponse('recovered log'))
    const { wrapper } = await mountViewer()
    logs.reject({ response: { status: 503 } })
    await flushPromises()

    expect(wrapper.get('video').attributes('src')).toBe(VIDEO_URL)
    expect(wrapper.findAll('[data-testid="telemetry-chart"]')).toHaveLength(3)
    await buttonMatching(wrapper, /retry.*logs|retry automatic/i).trigger('click')
    await flushPromises()
    expect(api.getOrderData).toHaveBeenCalledTimes(1)
    expect(api.getOrderLogs).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('recovered log')
  })

  it('preserves a manually selected file when a late automatic response arrives', async () => {
    const logs = deferred()
    api.getOrderLogs.mockReturnValue(logs.promise)
    api.getOrderLogWindow.mockResolvedValue({
      data: {
        log_key: DEFAULT_KEY,
        text: btoa('manual selected window'),
        status: 'available',
        truncated: false,
      },
    })
    const { wrapper } = await mountViewer()
    await buttonMatching(wrapper, /browse machine logs/i).trigger('click')
    await flushPromises()
    await wrapper.get('select').setValue(DEFAULT_KEY)
    await buttonMatching(wrapper, /load order window/i).trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('manual selected window')

    logs.resolve(logsResponse('automatic window must not overwrite manual'))
    await flushPromises()
    expect(wrapper.text()).toContain('manual selected window')
    expect(wrapper.text()).not.toContain('automatic window must not overwrite manual')
    expect(api.getOrderData).toHaveBeenCalledTimes(1)
  })

  it('activates a manually chosen file and preserves that tab on automatic refresh', async () => {
    const manualKey = 'machine/2026-11-06_machine.txt.gz'
    api.getOrderLogs.mockResolvedValue(logsResponse('automatic details'))
    api.getMachineLogs.mockResolvedValue({
      data: [{ log_key: manualKey, filename: '2026-11-06_machine.txt.gz', size_bytes: 100 }],
    })
    api.getOrderLogWindow.mockResolvedValue({
      data: {
        log_key: manualKey,
        text: btoa('manual active details'),
        status: 'available',
        truncated: false,
      },
    })
    const { wrapper } = await mountViewer()
    expect(wrapper.text()).toContain('automatic details')
    await buttonMatching(wrapper, /browse machine logs/i).trigger('click')
    await flushPromises()
    await wrapper.get('select').setValue(manualKey)
    await buttonMatching(wrapper, /load order window/i).trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="tab"][aria-selected="true"]').text()).toBe(manualKey)
    expect(wrapper.text()).toContain('manual active details')
    await buttonMatching(wrapper, /reload logs/i).trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="tab"][aria-selected="true"]').text()).toBe(manualKey)
    expect(wrapper.text()).toContain('manual active details')
    expect(api.getOrderData).toHaveBeenCalledTimes(1)
  })

  it('does not attach a stale automatic log response to the next order', async () => {
    const oldLogs = deferred()
    const newLogs = deferred()
    const nextUid = '1794267966.115'
    api.getOrderLogs.mockImplementation((_machine, uid) =>
      uid === ORDER_ID ? oldLogs.promise : newLogs.promise,
    )
    const { wrapper, router } = await mountViewer()
    const oldSignal = api.getOrderLogs.mock.calls[0][2].signal
    await router.push({
      name: 'robot-viewer',
      query: { machine_id: MACHINE_ID, order_id: nextUid },
    })
    await flushPromises()
    expect(oldSignal.aborted).toBe(true)
    newLogs.resolve(logsResponse('current order details', 'orders/2026-11-09_orders.txt.gz'))
    await flushPromises()
    oldLogs.resolve(logsResponse('stale order details'))
    await flushPromises()

    expect(wrapper.text()).toContain(`Order Video: ${nextUid}`)
    expect(wrapper.text()).toContain('current order details')
    expect(wrapper.text()).not.toContain('stale order details')
  })
})
