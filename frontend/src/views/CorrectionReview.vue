<template>
  <section class="page" data-testid="correction-review">
    <header class="page-head">
      <div>
        <h2>日报更正审核台</h2>
        <p class="page-desc">
          已发布的日报、正在审的候选与稍后重新隔离的事项分区展示；确认更正前按有效隔离批次复核口径。
        </p>
      </div>
      <div class="page-actions">
        <label class="filter-item">
          <span>来源渠道</span>
          <input v-model="channel" placeholder="留空看全部" data-testid="board-channel" @change="reload" />
        </label>
        <button class="btn" type="button" data-testid="board-refresh" @click="reload">刷新</button>
      </div>
    </header>

    <p v-if="errorMessage" class="error-text" data-testid="board-error">{{ errorMessage }}</p>
    <p v-if="notice" class="notice-text" data-testid="board-notice">{{ notice }}</p>

    <section class="board-section" data-testid="section-published">
      <h3>已发布的日报</h3>
      <table class="data-table">
        <thead>
          <tr><th>读数编号</th><th>前序读数</th><th>版本</th><th>隔离</th><th>隔离率</th><th>批次凭据</th><th>发布时间</th></tr>
        </thead>
        <tbody>
          <tr
            v-for="row in board.published_reports"
            :key="String(row['指标读数编号'])"
            :data-testid="`published-${row['指标读数编号']}`"
          >
            <td>{{ row['指标读数编号'] }}</td>
            <td>{{ row['前序读数编号'] || '—' }}</td>
            <td>{{ row['发布版本'] || '—' }}</td>
            <td>{{ row['隔离行分子'] }}/{{ row['隔离行分母'] }}</td>
            <td>{{ row['隔离率'] }}</td>
            <td>{{ row['批次凭据'] || '—' }}</td>
            <td>{{ row['采集时间'] }}</td>
          </tr>
          <tr v-if="!board.published_reports.length">
            <td colspan="7" class="empty-state">该渠道尚无已发布日报</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="board-section" data-testid="section-candidates">
      <h3>正在审的候选</h3>
      <table class="data-table">
        <thead>
          <tr><th>更正编号</th><th>基准读数</th><th>更正口径</th><th>覆盖对账</th><th>领取人</th><th>状态</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr
            v-for="row in board.candidates"
            :key="String(row['更正编号'])"
            :data-testid="`candidate-${row['更正编号']}`"
            :data-status="String(row.status)"
          >
            <td>{{ row['更正编号'] }}</td>
            <td>{{ row['关联读数编号'] }}</td>
            <td>{{ row['更正前分子分母'] }} → {{ row['更正后分子分母'] }}</td>
            <td>{{ row['覆盖对账'] }}</td>
            <td>{{ row['领取人'] || '—' }}</td>
            <td>{{ row.status }}</td>
            <td class="row-actions">
              <button
                class="link"
                type="button"
                :data-testid="`claim-${row['更正编号']}`"
                @click="claim(String(row['更正编号']))"
              >领取候选</button>
              <button
                class="link"
                type="button"
                :data-testid="`publish-${row['更正编号']}`"
                @click="publish(String(row['更正编号']))"
              >确认发布</button>
              <button
                class="link"
                type="button"
                :data-testid="`coverage-${row['更正编号']}`"
                @click="showCoverage(String(row['更正编号']))"
              >查看覆盖</button>
            </td>
          </tr>
          <tr v-if="!board.candidates.length">
            <td colspan="7" class="empty-state">暂无正在审的候选</td>
          </tr>
        </tbody>
      </table>

      <div v-if="board.claimed_candidates.length" class="board-note">
        <h4>已领取但未发布的候选（含随后失效）</h4>
        <table class="data-table">
          <thead>
            <tr><th>更正编号</th><th>领取人</th><th>状态</th><th>复核动作</th></tr>
          </thead>
          <tbody>
            <tr
              v-for="row in board.claimed_candidates"
              :key="`claimed-${row['更正编号']}`"
              :data-testid="`claimed-${row['更正编号']}`"
            >
              <td>{{ row['更正编号'] }}</td>
              <td>{{ row['领取人'] }}</td>
              <td>{{ row.status }}</td>
              <td>{{ row['复核动作'] || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="board-section" data-testid="section-requarantined">
      <h3>稍后重新隔离的事项</h3>
      <table class="data-table">
        <thead>
          <tr><th>对账编号</th><th>原行</th><th>动作</th><th>当时隔离</th><th>对账状态</th><th>对账时间</th></tr>
        </thead>
        <tbody>
          <tr
            v-for="item in board.requarantined"
            :key="item['对账编号']"
            :data-testid="`requarantined-${item['对账编号']}`"
          >
            <td>{{ item['对账编号'] }}</td>
            <td>{{ item['关联批次'] }}/{{ item['原始行号'] }}</td>
            <td>{{ item['回补动作'] }}</td>
            <td>{{ item['隔离行分子'] }}/{{ item['隔离行分母'] }}</td>
            <td>{{ item['对账状态'] }}</td>
            <td>{{ item['对账时间'] }}</td>
          </tr>
          <tr v-if="!board.requarantined.length">
            <td colspan="6" class="empty-state">无稍后重新隔离的事项</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="board-section" data-testid="section-pending">
      <h3>待发起的下一次更正</h3>
      <p v-if="!board.pending_ledger.length" class="empty-state">没有待更正的对账事项</p>
      <ul v-else class="ledger-list">
        <li v-for="item in board.pending_ledger" :key="item['对账编号']">
          <span>{{ item['对账编号'] }}</span>
          <span>原行 {{ item['关联批次'] }}/{{ item['原始行号'] }}</span>
          <span>{{ item['回补动作'] }}</span>
          <span>当时隔离 {{ item['隔离行分子'] }}/{{ item['隔离行分母'] }}</span>
        </li>
      </ul>
    </section>

    <div v-if="coverage" class="modal-backdrop" data-testid="coverage-modal">
      <div class="modal">
        <h3>更正 {{ coverage['更正编号'] }} 覆盖的实际动作</h3>
        <p class="page-desc">
          {{ coverage['更正前分子分母'] }} → {{ coverage['更正后分子分母'] }}，
          共覆盖 {{ coverage.covered_count }} 个动作。
        </p>
        <table class="data-table">
          <thead><tr><th>对账编号</th><th>动作</th><th>原行</th><th>结果</th></tr></thead>
          <tbody>
            <tr v-for="item in coverage.covered_actions" :key="item['对账编号']">
              <td>{{ item['对账编号'] }}</td>
              <td>{{ item['回补动作'] }}</td>
              <td>{{ item['原始行号'] }}</td>
              <td>{{ item['回补结果'] }}</td>
            </tr>
          </tbody>
        </table>
        <h4>未纳入的事项（仍可发起下一次更正）</h4>
        <table class="data-table">
          <thead><tr><th>对账编号</th><th>动作</th><th>对账状态</th><th>可发起更正</th></tr></thead>
          <tbody>
            <tr v-for="item in coverage.uncovered_actions" :key="`un-${item['对账编号']}`">
              <td>{{ item['对账编号'] }}</td>
              <td>{{ item['回补动作'] }}</td>
              <td>{{ item['对账状态'] }}</td>
              <td>{{ item['可发起更正'] ? '是' : '否' }}</td>
            </tr>
          </tbody>
        </table>
        <div class="page-actions">
          <button class="btn" type="button" data-testid="close-coverage" @click="coverage = null">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  claimCorrection,
  loadCorrectionCoverage,
  loadReviewBoard,
  publishCorrection,
  type CoverageDetail,
  type ReviewBoard,
} from '@/api/backend-service'

const board = ref<ReviewBoard>({
  channel: '',
  published_reports: [],
  candidates: [],
  claimed_candidates: [],
  requarantined: [],
  pending_ledger: [],
})
const channel = ref('')
const coverage = ref<CoverageDetail | null>(null)
const errorMessage = ref('')
const notice = ref('')
const reviewer = '日报审核员'

async function reload() {
  errorMessage.value = ''
  try {
    board.value = await loadReviewBoard(channel.value.trim())
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '审核台读取失败'
  }
}

async function claim(correctionNo: string) {
  errorMessage.value = ''
  notice.value = ''
  try {
    const result = await claimCorrection(correctionNo, reviewer)
    if (!result.ok) throw new Error(result.message)
    notice.value = result.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '领取候选失败'
  }
}

async function publish(correctionNo: string) {
  errorMessage.value = ''
  notice.value = ''
  try {
    const result = await publishCorrection(correctionNo, reviewer)
    if (!result.ok) throw new Error(result.message)
    notice.value = result.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '确认发布失败'
  }
}

async function showCoverage(correctionNo: string) {
  errorMessage.value = ''
  try {
    coverage.value = await loadCorrectionCoverage(correctionNo)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '覆盖范围读取失败'
  }
}

onMounted(reload)
</script>
