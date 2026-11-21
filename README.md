# 治数工坊 · 数据治理与生命周期管理平台

面向资产注册、元数据索引与搜索、质量规则引擎、数据画像与分级、脱敏与操作日志、生命周期策略、冷热归档、血缘追踪、影响分析、变更捕获与契约治理的数据治理与生命周期管理平台。

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

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 资产注册 | `asset_registry` | 数据资产 | 资产编号、资产名称、资产类型 |
| 元数据索引 | `metadata_index` | 元数据条目 | 条目编号、所属资产、字段名称 |
| 质量规则 | `quality_rule` | 质量检查 | 规则编号、目标资产、检查类型 |
| 质检批次 | `quality_batch` | 质检批次 | 批次编号、资产组、质量窗口 |
| 分区质检台账 | `partition_ledger` | 分区质检台账 | 台账编号、批次编号、资产编号 |
| 数据画像 | `profile_stats` | 数据画像 | 画像编号、目标资产、行数统计 |
| 数据分级 | `classification` | 分级标签 | 标签编号、目标字段、敏感等级 |
| 生命周期 | `lifecycle_policy` | 生命周期策略 | 策略编号、目标资产、创建阶段 |
| 冷热归档 | `cold_archive` | 归档任务 | 任务编号、源资产、归档条件 |
| 保留清理 | `retention_job` | 清理计划 | 计划编号、目标资产、保留天数 |
| 脱敏引擎 | `data_anonym` | 脱敏规则 | 规则编号、目标字段、脱敏算法 |
| 操作日志 | `operation_log` | 操作记录 | 记录编号、目标资产、操作人 |
| 变更捕获 | `change_capture` | 变更记录 | 记录编号、目标资产、变更类型 |
| 血缘追踪 | `lineage_track` | 血缘关系 | 关系编号、上游资产、下游资产 |
| 影响分析 | `impact_analysis` | 影响报告 | 报告编号、变更资产、影响下游 |
| 标签体系 | `tag_registry` | 业务标签 | 标签编号、标签名称、标签层级 |
| 治理评分 | `govern_score` | 评分任务 | 任务编号、评估对象、评分维度 |
| 数据契约 | `data_contract` | 契约定义 | 契约编号、数据资产、Schema约束 |
| 合规扫描 | `compliance_scan` | 扫描任务 | 任务编号、目标资产、合规条款 |
| 问题追踪 | `issue_track` | 治理问题 | 问题编号、关联资产、问题类型 |
| 版本履历 | `version_history` | 版本快照 | 快照编号、目标资产、版本标签 |
| 调度治理 | `schedule_job` | 治理调度 | 作业编号、治理类型、目标资产 |
| 差异报告 | `diff_report` | 差异记录 | 记录编号、比较对象、比较维度 |
| 负责人管理 | `owner_mgr` | 责任人 | 指派编号、目标资产、负责角色 |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 多资产质检批次

`quality_batch` 把一组事实表 + 维表登记成**可补检单元**，`partition_ledger`
承接逐表逐分区回写的检查结果。核心行为：

- **按到齐情况调度**：成员未到齐时只检查已到资产，整组结论为「待补齐 / 不可比」；
  未到齐资产不生成台账，也不借用邻表的阈值或测点。
- **分区去重**：批量提交按「资产 + 分区」归并，同一分区重复导入只保留一份质量依据，
  并在证据上累计重复导入次数；接入失败会清掉该分区旧证据，补齐后可正常重提。
- **逐表有效规则**：每张表登记自己的完整性下限（`目标窗口` 维度），已暂停规则不参与；
  表没有有效规则时直接拒绝补检，不会回退到实收表已通过的阈值。
- **补检可追溯**：补检记录与台账都写明来源资产、来源窗口、来源分区。
- **调度联动**：批次会同步一个 `schedule_job` 作业（`依赖检查` 写明消费哪一批次），
  用于查询未结调度项；整组通过后作业自动收口为「已完成」。

接口（专用路由先于通用资源路由注册）：

```
POST /api/quality_batch/register                 登记批次与成员
POST /api/quality_batch/rules                    登记某表某窗口的有效规则
POST /api/quality_batch/{id}/submit              批量提交（支持接入失败）
POST /api/quality_batch/{id}/schedule            调度质检
POST /api/quality_batch/{id}/supplement          补检（记录来源）
GET  /api/quality_batch/{id}/report              三表关联质量报告 + 未结调度项
GET  /api/quality_batch/{id}/ledger              分区台账（可按 asset_code 过滤）
```

示例数据里 `QBAT-0001`（资产组 `RETAIL_SETTLE`、窗口 `2026-11-21`）即为
「实收事实表 + 店铺维表已到、币种维表等待上午交付」的初始场景。

领域：数据工程
子方向：数仓与数据湖
