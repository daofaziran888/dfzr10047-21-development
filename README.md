# 解构工坊 · 异构文件解析与字段抽取编排平台

面向异构文件投递、格式探测、编码归一、解析器路由、抽取模板版本、字段解析、坏行隔离、结构嗅探、嵌套展平、取值转型、单位归一、输出契约、结果投递、解析水位与重解析编排的数据采集接入平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8004/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5177/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8004`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 文件投递 | `file_intake` | 投递批次 | 批次编号、来源渠道、原始文件名 |
| 格式探测 | `format_probe` | 探测结论 | 探测编号、关联批次、魔数特征 |
| 编码归一 | `encoding_norm` | 转码记录 | 转码编号、关联批次、原始编码 |
| 解析路由 | `parser_route` | 路由决策 | 决策编号、关联批次、推断格式 |
| 抽取模板 | `extract_tmpl` | 模板版本 | 模板编号、模板名称、适用格式 |
| 字段解析 | `field_parse` | 解析字段 | 解析编号、关联批次、命中模板 |
| 坏行隔离 | `row_quarantine` | 隔离行 | 隔离编号、关联批次、原始行号 |
| 解析批次 | `parse_batch` | 批次记录 | 批次记录编号、关联投递批次、解析器标识 |
| 结构嗅探 | `schema_sniff` | 结构推断 | 嗅探编号、关联批次、样本行数 |
| 分隔规则 | `delimiter_rule` | 规则条目 | 规则编号、适用格式、分隔符 |
| 嵌套展平 | `nested_flatten` | 展平路径 | 展平编号、关联模板、原始路径 |
| 取值转型 | `value_cast` | 转型规则 | 转型编号、目标类型、来源类型 |
| 单位归一 | `unit_normalize` | 归一系数 | 归一编号、字段名称、原始单位 |
| 输出契约 | `output_contract` | 契约条目 | 契约编号、关联批次、输出列集合 |
| 结果投递 | `result_deliver` | 投递记录 | 投递记录编号、关联契约、目标位置 |
| 解析水位 | `parse_watermark` | 水位读数 | 水位编号、来源渠道、已解析至位点 |
| 重解析任务 | `reparse_task` | 重解析单 | 重解析编号、关联批次、重解析原因 |
| 重复指纹 | `dup_fingerprint` | 指纹记录 | 指纹编号、关联批次、内容指纹 |
| 抽样复核 | `sample_review` | 复核样本 | 样本编号、关联批次、抽样比例 |
| 解析指标 | `parse_metric` | 指标读数 | 指标读数编号、统计窗口、来源渠道 |
| 回补对账 | `reinstate_ledger` | 回补对账记录 | 对账编号、关联隔离编号、原始行号、对账状态 |
| 日报更正 | `report_correction` | 日报更正 | 更正编号、关联读数编号、更正前/后分子分母 |
| 模板差异 | `tmpl_diff` | 差异条目 | 差异编号、旧模板版本、新模板版本 |
| 投递审计 | `intake_audit` | 审计条目 | 审计编号、关联对象、操作类型 |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 隔离回补 → 日报更正

解析指标只给百分比时无法复核，因此每条读数都落真实的隔离分子/分母、
实际隔离行号与批次凭据（`批次凭据` 形如 `PB-011:20,PB-012:10`）。

1. 坏行隔离详情提供**审批入口**：选择受影响的结算读数与要纳入的回补对账记录，
   提交后生成日报更正候选（`app/routers/workflow.py`）。
2. 回补或重新隔离都会写一条**回补对账**（`reinstate_ledger`）：
   - 同一原行的同一次回补用事件键去重，重复提交只记一次影响；
   - 稍后到达的迟到结论触发重新隔离，生成独立对账记录并独立计数，
     原回补记录转为「已抵消」，历史仍可查。
3. 更正候选冻结提交时的分子分母，**发布前**按有效隔离重新核对：
   基准已变化（例如原行再次隔离）时拒绝发布并把候选标记失效，
   原已领取候选与待做更正分别可查。
4. 确认发布另发新读数版本（`PM-xxx-C1`、`PM-xxx-C2`…），原日报读数不被改写，
   历史读数保留当时的分子分母与实际隔离行号。
5. 审核台 `/corrections/review` 分三区展示：已发布日报、正在审的候选、稍后重新隔离的事项。

关键接口：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/workflow/quarantine/{隔离编号}/correction-entry` | 隔离行上的更正审批入口 |
| POST | `/api/workflow/corrections` | 提交日报更正候选 |
| POST | `/api/workflow/corrections/{更正编号}/claim` | 审核员领取候选 |
| POST | `/api/workflow/corrections/{更正编号}/publish` | 确认发布（发布前复核基准） |
| GET | `/api/workflow/corrections/{更正编号}/coverage` | 所选更正覆盖的实际动作 |
| GET | `/api/workflow/ledger/{对账或隔离编号}/coverage` | 从对账回到原行看覆盖范围 |
| GET | `/api/workflow/review-board` | 审核台三分区 |
| GET | `/api/workflow/readings/{读数编号}/detail` | 读数详情（分子分母、行号、凭据） |
| POST | `/api/workflow/quarantine/{隔离编号}/requarantine` | 迟到结论触发重新隔离 |

领域：数据工程
子方向：数据采集、爬虫与RPA
