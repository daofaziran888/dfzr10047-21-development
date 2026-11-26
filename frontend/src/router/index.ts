import { createRouter, createWebHistory } from 'vue-router'

import CorrectionReview from '@/views/CorrectionReview.vue'
import LedgerDetail from '@/views/LedgerDetail.vue'
import Overview from '@/views/Overview.vue'
import ReadingDetail from '@/views/ReadingDetail.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    { path: '/corrections/review', name: 'correction-review', component: CorrectionReview },
    {
      path: '/ledger/:entryNo',
      name: 'ledger-detail',
      component: LedgerDetail,
      props: true,
    },
    {
      path: '/readings/:readingNo',
      name: 'reading-detail',
      component: ReadingDetail,
      props: true,
    },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
