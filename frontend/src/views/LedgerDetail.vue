<template>
  <section class="page" data-testid="ledger-detail">
    <header class="page-head">
      <div>
        <h2>回补对账详情</h2>
        <p class="page-desc">
          从对账记录回到原行：确认所选更正覆盖了哪些实际动作，未纳入的事项仍能发起下一次更正。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" data-testid="ledger-refresh" @click="reload">刷新</button>
      </div>
    </header>

    <p v-if="errorMessage" class="error-text" data-testid="ledger-error">{{ errorMessage }}</p>

    <label v-if="history.length > 1" class="filter-item">
      <span>选择对账记录</span>
      <select v-model="selectedLedgerNo" data-testid="select-ledger" @change="loadCoverage">
        <option v-for="item in history" :key="item['对账编号']" :value="item['对账编号']">
          {{ item['对账编号'] }}（{{ item['回补动作'] }}）
        </option>
      </select>
    </label>

    <template v-if="coverage">
      <div class="stat-row">
        <article class="stat-card">
          <span class="stat-label">关联批次 / 原行</span>
          <strong class="stat-value">{{ coverage['关联批次'] || '—' }}/{{ coverage['原始行号'] || '—' }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">已覆盖动作</span>
          <strong class="stat-value">{{ coverage.covered_count }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">未纳入可再发起</span>
          <strong class="stat-value">{{ uncoveredCount }}</strong>
        </article>
      </div>

      <h3>本对账覆盖的实际动作</h3>
      <table class="data-table" data-testid="covered-table">
        <thead><tr><th>对账编号</th><th>动作</th><th>原行</th><th>回补结果</th><th>对账时间</th></tr></thead>
        <tbody>
          <tr v-for="item in coverage.covered_actions" :key="item['对账编号']">
            <td>{{ item['对账编号'] }}</td>
            <td>{{ item['回补动作'] }}</td>
            <td>{{ item['原始行号'] }}</td>
            <td>{{ item['回补结果'] }}</td>
            <td>{{ item['对账时间'] || '—' }}</td>
          </tr>
        </tbody>
      </table>

      <h3>同原行未纳入的事项</h3>
      <table class="data-table" data-testid="uncovered-table">
        <thead><tr><th>对账编号</th><th>动作</th><th>对账状态</th><th>可发起下一次更正</th></tr></thead>
        <tbody>
          <tr v-for="item in coverage.uncovered_actions" :key="`un-${item['对账编号']}`">
            <td>{{ item['对账编号'] }}</td>
            <td>{{ item['回补动作'] }}</td>
            <td>{{ item['对账状态'] }}</td>
            <td>{{ item['可发起更正'] ? '是' : '否' }}</td>
          </tr>
          <tr v-if="!coverage.uncovered_actions.length">
            <td colspan="4" class="empty-state">同原行没有未纳入的对账事项</td>
          </tr>
        </tbody>
      </table>
    </template>

    <template v-if="entry">
      <h3>从原行发起日报更正</h3>
      <p class="page-desc">
        原行 {{ entry.row_no }}，渠道 {{ entry.channel || '—' }}。更正必须勾选至少一条对账记录。
      </p>

      <label class="filter-item">
        <span>受影响的结算读数</span>
        <select v-model="selectedReading" data-testid="select-reading">
          <option
            v-for="item in entry.reading_candidates"
            :key="item['指标读数编号']"
            :value="item['指标读数编号']"
            :disabled="item.selectable === false"
          >
            {{ item['指标读数编号'] }}（{{ item['隔离行分子'] }}/{{ item['隔离行分母'] }}，{{ item['发布状态'] }}）
          </option>
        </select>
      </label>

      <h4>选择要纳入的更正事项</h4>
      <ul class="ledger-list" data-testid="ledger-options">
        <li v-for="item in entry.ledger_rows" :key="item['对账编号']">
          <label>
            <input v-model="selectedForCorrection" type="checkbox" :value="item['对账编号']" />
            {{ item['对账编号'] }} · 行 {{ item['原始行号'] }} · {{ item['回补动作'] }}
            · 当时隔离 {{ item['隔离行分子'] }}/{{ item['隔离行分母'] }}
          </label>
        </li>
        <li v-if="!entry.ledger_rows.length" class="empty-state">该原行暂无可纳入的对账记录</li>
      </ul>

      <div class="page-actions">
        <input v-model="correctionNo" placeholder="更正编号，如 RC-009" data-testid="correction-no" />
        <button class="btn primary" type="button" data-testid="submit-correction" @click="submit">
          提交日报更正
        </button>
      </div>
      <p v-if="notice" class="notice-text" data-testid="ledger-notice">{{ notice }}</p>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  loadCorrectionEntry,
  loadLedgerCoverage,
  submitCorrection,
  type CorrectionEntry,
  type CoverageAction,
  type CoverageDetail,
} from '@/api/backend-service'

const props = defineProps<{ entryNo: string }>()
const coverage = ref<CoverageDetail | null>(null)
const entry = ref<CorrectionEntry | null>(null)
const history = ref<CoverageAction[]>([])
const selectedLedgerNo = ref('')
const selectedReading = ref('')
const selectedForCorrection = ref<string[]>([])
const correctionNo = ref('')
const errorMessage = ref('')
const notice = ref('')

const uncoveredCount = computed(
  () => coverage.value?.uncovered_actions.filter((item) => item['可发起更正']).length ?? 0,
)

async function loadCoverage() {
  if (!selectedLedgerNo.value) {
    coverage.value = null
    return
  }
  try {
    coverage.value = await loadLedgerCoverage(selectedLedgerNo.value)
  } catch (error) {
    coverage.value = null
    errorMessage.value = error instanceof Error ? error.message : '对账覆盖范围读取失败'
  }
}

async function reload() {
  errorMessage.value = ''
  try {
    entry.value = await loadCorrectionEntry(props.entryNo)
  } catch {
    entry.value = null
  }
  history.value = entry.value?.ledger_history ?? []
  if (!selectedLedgerNo.value && history.value.length) {
    selectedLedgerNo.value = history.value[0]['对账编号']
  }
  if (!selectedReading.value && entry.value?.reading_candidates.length) {
    const selectable = entry.value.reading_candidates.find((item) => item.selectable !== false)
    selectedReading.value = selectable?.['指标读数编号'] ?? ''
  }
  await loadCoverage()
}

async function submit() {
  errorMessage.value = ''
  notice.value = ''
  if (!entry.value) return
  if (!selectedReading.value) {
    errorMessage.value = '请选择受影响的结算读数'
    return
  }
  if (!selectedForCorrection.value.length) {
    errorMessage.value = '请选择至少一条对账记录，更正必须复核到原行'
    return
  }
  try {
    const result = await submitCorrection({
      correction_no: correctionNo.value.trim() || `RC-${Date.now()}`,
      reading_no: selectedReading.value,
      ledger_nos: selectedForCorrection.value,
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
