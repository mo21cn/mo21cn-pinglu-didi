// 港口选择弹层（底部滑出 · 滚动列表）
// 背景：wx.showActionSheet 的 itemList 上限 6 项，13 个港口用它必然失败且无兜底。
// 用法：
//   <port-picker visible="{{ppVisible}}" title="{{ppTitle}}" current="{{ppCurrent}}"
//                tip="{{ppTip}}" bind:select="onPortPicked" bind:close="onPortPickerClose" />
// ports 不传时使用 utils/ports.js 的全量 13 港；传 ports 可复用为筛选器（含「全部」项）。
const { PORTS } = require('../../utils/ports')

Component({
  properties: {
    visible: { type: Boolean, value: false },
    title: { type: String, value: '选择港口' },
    tip: { type: String, value: '' },
    current: { type: String, value: '' },
    ports: { type: Array, value: [] }
  },

  data: {
    list: PORTS
  },

  observers: {
    ports(ports) {
      if (ports && ports.length) this.setData({ list: ports })
    }
  },

  methods: {
    onPick(e) {
      const key = e.currentTarget.dataset.key
      const item = this.data.list.find((x) => x.key === key)
      if (!item) return
      this.triggerEvent('select', { key: item.key, label: item.label })
    },

    onMaskTap() {
      this.triggerEvent('close')
    },

    onCancelTap() {
      this.triggerEvent('close')
    },

    // 吞掉面板内的滚动/点击穿透（mask 上绑定 catchtouchmove，面板内 catchtap 阻止冒泡关闭）
    noop() {}
  }
})
