# 关键链（CCPM）进度管理小平台

按关键链法管理多研发项目进度的前后端小平台：FastAPI 负责全部计算
（网络校验、资源冲突消解、关键链识别、缓冲推算、汇报流投影、发烧图），
React 18 页面只做录入与展示；数据落在挂卷的单个 SQLite 文件里。

规则口径（资源消解、等长选链、缓冲公式、乱序/并发汇报、冻结关键链、
分区方式、已知偏长场景）见 **[docs/DECISIONS.md](docs/DECISIONS.md)**。

## 目录结构（核心逻辑按模块拆分）

```
backend/
  app/
    validation.py   # 网络建模与字段级校验（成环/引用/容量/分界线…）
    scheduler.py    # 资源冲突消解（确定性 Serial-SGS）+ 关键链识别
    buffers.py      # 缓冲推算口径（0.5×(保守−激进)）及其逐项依据
    worktime.py     # 工作日历（绑定项目时区，周一至周五 09:00-18:00）
    timezones.py    # 项目作息时区解析/自动判定/老数据推断
    projection.py   # 汇报流折叠 → 当前/历史时刻状态投影（纯函数）
    fever.py        # 发烧图轨迹查询（每个点带任务剩余工期快照）
    store.py        # SQLite 持久化（projects + 追加式 reports，WAL）
    api.py          # HTTP 接口层（不做计算）
    errors.py       # 统一错误响应
    main.py         # FastAPI 应用，同时提供静态页面与 /api
  tests/            # pytest：调度/校验/缓冲/投影/日历/接口/重启/并发
frontend/
  src/
    api.js                       # 后端 API 客户端
    App.jsx                      # 项目列表 + 页签骨架
    components/ProjectForm.jsx   # 录入/导入任务网络
    components/GanttChart.jsx    # 甘特式排布，CC 与缓冲高亮
    components/FeverChart.jsx    # 发烧图轨迹 + 点选查看剩余工期
    components/ReportForm.jsx    # 提交进度汇报
Dockerfile            # 两阶段：node:20-alpine 构建 → python:3.12-slim 运行
docker-compose.yml    # 卷 ccpm-data → /data
docs/DECISIONS.md     # 全部规则口径的交付说明
```

## 一键运行（Docker）

```bash
docker compose up --build
# 浏览器打开 http://localhost:8000
```

镜像先在 `node:20-alpine` 中 `npm run build` 出静态文件，再拷入
`python:3.12-slim` 运行镜像，由同一个 uvicorn 进程提供页面和接口；
SQLite 数据库在卷 `/data/ccpm.db`。

## 本地开发

```bash
# 后端（Python 3.12）
cd backend
pip install -r requirements-dev.txt
CCPM_DB_PATH=./data/ccpm.db CCPM_STATIC_DIR=../frontend/dist \
  uvicorn app.main:app --reload --port 8000

# 前端（Node.js 20，:5173 的 /api 代理到 :8000）
cd frontend
npm install
npm run dev
```

## 测试

```bash
cd backend && pip install -r requirements-dev.txt && pytest
```

覆盖（64 个用例）：

* 四任务算例关键链 = **15**（A→B→C→D，B 先占工程师）；
* 无资源冲突时关键链 == 纯依赖关键路径（A→B→D = 12）；
* 所有工期同乘正整数：关键链不变、长度与缓冲同比放大；
* 汇入缓冲识别与 0.5×Δ 口径；确定性：重复计算结果逐字节一致；
* 汇报乱序到达 == 顺序到达；同时刻按汇报编号裁决；
* 重复汇报幂等；历史时刻回放 == 前缀折叠；
* 重启后从 SQLite 恢复的状态/发烧图与重启前一致；
* 多线程并发汇报后的状态与串行提交一致；
* 作息时区：`+08:00` 项目发烧轨迹逐点等于同墙上时间的 `Z` 对照组；计划
  开/完工落在当地工作时段；IANA 时区、裸时间戳、历史回放均覆盖；
* 老数据过渡：`+08:00` 旧库在启动迁移后直接出正确计划/发烧图且可重复
  执行；纯 `Z` 旧项目迁移前后每个数字逐字段不变；
* 全部拒收路径（成环、未知引用、负工期、agg>cons、超容量、分界线越界、
  无法识别的时区）。

## HTTP 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/projects` | 录入项目，同步返回排好的计划（关键链/PB/FB/甘特数据） |
| GET | `/api/projects` | 项目列表 |
| GET | `/api/projects/{id}/plan` | 输入 + 计划 |
| GET | `/api/projects/{id}/state?at=` | 当前（或 `at` 时刻回放的）投影状态 |
| GET | `/api/projects/{id}/fever` | 发烧图轨迹（每个点带任务剩余工期快照） |
| POST | `/api/projects/{id}/reports` | 追加一条汇报，返回最新状态 |

### 项目 JSON 示例

```json
{
  "name": "四任务示例",
  "start_time": "2026-01-05T09:00:00+08:00",
  "timezone": "Asia/Shanghai",
  "resources": [{"id": "eng", "name": "工程师", "capacity": 1}],
  "tasks": [
    {"id": "A", "name": "需求", "predecessors": [], "demands": {"eng": 1},
     "aggressive_days": 4, "conservative_days": 6},
    {"id": "B", "name": "后端", "predecessors": ["A"], "demands": {"eng": 1},
     "aggressive_days": 6, "conservative_days": 10},
    {"id": "C", "name": "前端", "predecessors": ["A"], "demands": {"eng": 1},
     "aggressive_days": 3, "conservative_days": 5},
    {"id": "D", "name": "发布", "predecessors": ["B", "C"], "demands": {},
     "aggressive_days": 2, "conservative_days": 4}
  ],
  "thresholds": {"yellow_pct": 50, "red_pct": 80}
}
```

### 汇报 JSON 示例

```json
{"report_id": "r12", "task_id": "B", "at": "2026-01-20T17:00:00+08:00",
 "kind": "remaining", "remaining_days": 2.5}
```

`kind` 为 `done` 时不带 `remaining_days`。编号重复按幂等处理；时刻早于
已有汇报会自动插回正确位置重算（不拒收）。不带偏移量的裸时间戳（如
`2026-01-20T17:00:00`）按项目自己的作息时区解释。

### 时区口径

每个项目绑定一个作息时区（`timezone`，IANA 名称如 `Asia/Shanghai` 或固定
偏移如 `UTC+08:00`），工作日历的 09:00–18:00 按该时区墙上时间计算；缺省时
按 `start_time` 的偏移自动判定一次。升级前已录入的老项目在首次启动时自动
推断回填并重建计划，无需删库重录；全程 `Z` 数据的项目数字保持不变。
理由、代价与迁移细节见 **[docs/DECISIONS.md](docs/DECISIONS.md)** 第 6 节。
