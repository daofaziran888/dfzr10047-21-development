<template>
  <section class="page" data-testid="reading-detail">
    <header class="page-head">
      <div>
        <h2>指标读数详情</h2>
        <p class="page-desc">
          读数不只有百分比：同时给出真实隔离分子分母、有效隔离行号与批次凭据，并可从此处发起日报更正。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" data-testid="reading-refresh" @click="reload">刷新</button>
      </div>
    </header>

    <p v-if="errorMessage" class="error-text" data-testid="reading-error">{{ errorMessage }}</p>

    <template v-if="detail">
      <div class="stat-row">
        <article class="stat-card">
          <span class="stat-label">指标读数编号</span>
          <strong class="stat-value">{{ reading['指标读数编号'] }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">隔离（分子/分母）</span>
          <strong class="stat-value" data-testid="reading-basis">
            {{ reading['隔离行分子'] ?? '—' }}/{{ reading['隔离行分母'] ?? '—' }}
          </strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">隔离率</span>
          <strong class="stat-value">{{ reading['隔离率'] ?? '—' }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">发布状态</span>
          <strong class="stat-value">{{ reading['发布状态'] || '未发布' }}</strong>
        </article>
      </div>

      <dl class="reading-facts">
        <div><dt>来源渠道</dt><dd>{{ detail.channel || '—' }}</dd></div>
        <div><dt>统计窗口</dt><dd>{{ reading['统计窗口'] || '—' }}</dd></div>
        <div><dt>实际隔离行号</dt><dd data-testid="reading-rows">{{ reading['实际隔离行号'] || '无' }}</dd></div>
        <div><dt>批次凭据</dt><dd data-testid="reading-evidence">{{ reading['批次凭据'] || '缺失' }}</dd></div>
        <div><dt>解析吞吐行数</dt><dd>{{ reading['解析吞吐行数'] ?? '—' }}</dd></div>
        <div><dt>平均解析耗时</dt><dd>{{ reading['平均解析耗时'] ?? '—' }}</dd></div>
      </dl>

      <h3>已发布的日报版本</h3>
      <table class="data-table" data-testid="reading-history">
        <thead>
          <tr><th>读数编号</th><th>版本</th><th>隔离</th><th>实际行号</th><th>批次凭据</th><th>发布状态</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in detail.history" :key="String(item['指标读数编号'])">
            <td>{{ item['指标读数编号'] }}</td>
            <td>{{ item['发布版本'] || '—' }}</td>
            <td>{{ item['隔离行分子'] }}/{{ item['隔离行分母'] }}</td>
            <td>{{ item['实际隔离行号'] || '无' }}</td>
            <td>{{ item['批次凭据'] || '—' }}</td>
            <td>{{ item['发布状态'] || '未发布' }}</td>
          </tr>
        </tbody>
      </table>

      <h3>该渠道的回补对账</h3>
      <table class="data-table">
        <thead><tr><th>对账编号</th><th>原行</th><th>动作</th><th>对账状态</th><th>关联更正</th></tr></thead>
        <tbody>
          <tr v-for="item in detail.ledger_rows" :key="item['对账编号']">
            <td>{{ item['对账编号'] }}</td>
            <td>{{ item['原始行号'] }}</td>
            <td>{{ item['回补动作'] }}</td>
            <td>{{ item['对账状态'] }}</td>
            <td>{{ item['关联更正编号'] || '—' }}</td>
          </tr>
          <tr v-if="!detail.ledger_rows.length">
            <td colspan="5" class="empty-state">该渠道暂无回补对账记录</td>
          </tr>
        </tbody>
      </table>

      <h3>发起日报更正</h3>
      <p class="page-desc">勾选要纳入本次更正的回补对账记录，未纳入的事项仍可发起下一次更正。</p>
      <ul class="ledger-list" data-testid="reading-ledger-options">
        <li v-for="item in detail.available_ledger" :key="item['对账编号']">
          <label>
            <input v-model="selectedLedger" type="checkbox" :value="item['对账编号']" />
            {{ item['对账编号'] }} · 行 {{ item['原始行号'] }} · {{ item['回补动作'] }}
            · 当时隔离 {{ item['隔离行分子'] }}/{{ item['隔离行分母'] }}
          </label>
        </li>
        <li v-if="!detail.available_ledger.length" class="empty-state">该渠道暂无可纳入的对账记录</li>
      </ul>
      <div class="page-actions">
        <input v-model="correctionNo" placeholder="更正编号，如 RC-009" data-testid="reading-correction-no" />
        <button class="btn primary" type="button" data-testid="reading-submit" @click="submit">
          提交日报更正
        </button>
      </div>
      <p v-if="notice" class="notice-text" data-testid="reading-notice">{{ notice }}</p>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  loadReadingDetail,
  submitCorrection,
  type ReadingDetail,
} from '@/api/backend-service'

const props = defineProps<{ readingNo: string }>()
const detail = ref<ReadingDetail | null>(null)
const selectedLedger = ref<string[]>([])
const correctionNo = ref('')
const errorMessage = ref('')
const notice = ref('')

const reading = computed<Record<string, unknown>>(
  () => (detail.value?.reading ?? {}) as Record<string, unknown>,
)

async function reload() {
  errorMessage.value = ''
  try {
    detail.value = await loadReadingDetail(props.readingNo)
  } catch (error) {
    detail.value = null
    errorMessage.value = error instanceof Error ? error.message : '读数详情读取失败'
  }
}

async function submit() {
  errorMessage.value = ''
  notice.value = ''
  if (!selectedLedger.value.length) {
    errorMessage.value = '请选择至少一条对账记录，更正必须复核到原行'
    return
  }
  try {
    const result = await submitCorrection({
      correction_no: correctionNo.value.trim() || `RC-${Date.now()}`,
      reading_no: props.readingNo,
      ledger_nos: selectedLedger.value,
      operator: '值班运营',
      submit_time: new Date().toISOString(),
    })
    if (!result.ok) throw new Error(result.message)
    notice.value = result.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '提交更正失败'
  }
}

onMounted(reload)
</script>
