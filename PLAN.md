% Lighter 价差监控与对冲交易方案（草案）

本方案基于本仓库提供的 Lighter Python SDK（只读 REST/WS 与交易签名/发送）。面向量化交易者的“跨交易对价差监控 + 自动/半自动对冲交易”需求，提供策略设计、系统架构与前端展示方案。后续细化可根据你的偏好与目标市场迭代。

## 1. 目标与范围
- 目标：实时比对不同交易对（同一或不同标的的合约市场）的价格/基差差异，生成做多/做空信号；支持自动下单与仅提醒两种模式；提供前端 Panel 展示实时价差、信号、持仓与交易记录。
- 范围：
  - 数据源：优先对接 Lighter 的 REST/WS；可扩展对接其他交易所（后续阶段）。
  - 交易执行：使用本 SDK 的签名与交易发送能力（SignerClient）。
  - 风控与监控：基本风控、订单状态跟踪、日志/告警。

## 2. 策略设计
### 2.1 价差定义
- 价格源：
  - 单市场：使用订单簿中位价 mid = (best_bid + best_ask)/2；也可用成交价/指数价/标记价（若可获取）。
  - 跨市场：统一转换到相同计价单位（同为 USD 计价或统一成 quote 资产）。
- 价差：spread(t) = P_A(t) − P_B(t)。
- 正则化：z(t) = (spread(t) − MA_lookback(spread)) / STD_lookback(spread)。

### 2.2 入场/出场逻辑
- 入场（均值回归范式）：
  - 当 z(t) ≥ enter_z_high：A 较贵、B 较便宜 → 在 A 做空、在 B 做多。
  - 当 z(t) ≤ −enter_z_high：A 较便宜、B 较贵 → 在 A 做多、在 B 做空。
- 出场：
  - 当 |z(t)| ≤ exit_z_low：平两边（可一次性或分批平仓）。
- 滞后与去抖：
  - 使用 enter_z_high > exit_z_low，避免频繁进出。
  - 配合冷却时间 min_hold_secs 与最小间隔 min_reentry_secs。

### 2.3 风险与约束
- 成本：考虑手续费、滑点、资金费率（永续合约）、提现/转账成本等。
- 风控：
  - 单笔最大名义、单边/净敞口上限、日内最多入场次数、最大回撤、超时撤单/重试等。
  - 异常波动/流动性骤降熔断，数据中断/延迟降级处理。
- 合约细节：
  - 保证金/杠杆设置，是否支持对冲模式；
  - 资金费率差异长期偏移，必要时动态偏移校正（“漂移”项）。

### 2.4 下单与滑点控制
- 下单类型：
  - 市价：快，但需容忍滑点，配价差/价值守护阈值（max_slippage_bps）。
  - 限价：可分两腿分批/同步挂单，若未成交超时撤单或切换 IOC/市价补单。
- 同步性：
  - 两腿下单尽量同时，有序保证：先流动性较差一侧尝试成交，另一侧对冲补齐。

### 2.5 模式切换
- 自动交易模式：检测到信号→风控通过→下单执行→跟踪成交→记录持仓与 PnL。
- 提醒模式：仅输出信号/建议，不实际下单；支持阈值与入场/出场提示。

## 3. 系统架构
### 3.1 模块划分
- Data Ingest（数据接入）
  - Lighter WS：`lighter.WsClient` 订阅所需 order_book / account_all 通道。
  - Lighter REST：补齐静态数据与容错拉取。
  - 其他交易所：按统一接口定义 Connector（后续阶段）。
- Normalizer（归一与聚合）
  - 统一价格、时间戳、交易对标识，维护滚动窗口与指标缓存。
- Signal Engine（信号引擎）
  - 计算 spread、MA、STD、Z-score，生成入场/出场指令。
- Risk Manager（风控）
  - 敞口、限额、冷却、熔断、滑点/价格守护校验。
- Execution Engine（执行引擎）
  - 使用 `lighter.SignerClient` 创建/撤销订单，跟踪状态与成交回报，处理 nonce 管理与错误重试。
- State Store（状态存储）
  - 持仓/订单/成交/信号/日志；初期可用 SQLite/本地文件，后续可换 PostgreSQL。
- Notifier（告警）
  - 控制台/文件日志，Webhook/Telegram/企业微信（可选）。
- Panel API（后端）
  - 提供 WebSocket/SSE 推送实时价差、信号、持仓、成交；暴露参数调整与模式切换。
- Panel UI（前端）
  - 实时看板与交互配置。

### 3.2 运行时形态
- Python 异步应用（`asyncio`）：
  - 单进程多任务：WS 订阅、信号计算、执行、面板推送并行。
  - 事件总线（内存队列）连接组件；后期可迁移到消息队列。

### 3.3 Lighter 适配要点
- 订阅/行情：`lighter.WsClient` 订阅所需 `order_book/{market_id}`，维护本地订单簿与中位价。
- 交易：`lighter.SignerClient` 创建订单（限价/市价），利用内置 NonceManager；Windows 环境暂不支持原生签名器（仅 Linux x86_64 / macOS arm64）。

## 4. 前端展示（Panel）
- 概览卡片：策略状态（自动/提醒）、当日 PnL、持仓净敞口、风控状态。
- 价差监控表：
  - 每个“配对”的实时 P_A/P_B、spread、z-score、信号状态、预计交易量与成本。
- 走势图：
  - spread 与 MA/±k·STD 的时间序列，标记入场/出场点。
- 交易与事件日志：
  - 下单/撤单/成交、告警、风控拒单原因、错误重试。
- 参数面板：
  - enter_z、exit_z、lookback、max_slippage_bps、名义/杠杆上限、冷却时间、风控开关。
- 模式切换：
  - 自动交易 vs 提醒模式，立即全平按钮与紧急熔断按钮。

实现路径建议：
- MVP：后端用 FastAPI + WebSocket；前端可先用简单的 `Streamlit` 快速起盘或一个小型 React/Vite 页面。

容器化（Windows + Docker Desktop）：
- 提供 `Dockerfile` 与 `docker-compose.yml`，面板 API 以容器运行（默认端口 8000），历史价差存储用容器挂载卷中的 SQLite 文件（`/data/arb.db`）。
- 启动：`docker compose up -d --build`，健康检查 `GET http://localhost:8000/health`，历史数据接口 `GET /api/spreads?pair=BTCUSDT&limit=1000`。

## 5. 实施计划（里程碑）
- 阶段 1：Aster + Lighter 提醒模式 MVP（统一架构，可扩展）
  1) 统一接口层：定义 `Connector` 抽象（订单簿/中位价、账户、下/撤单接口），并实现 `LighterConnector`（只读行情）与 `AsterConnector`（只读行情）。
  2) 信号引擎：滚动均值/方差与 z-score；支持多“跨所同币种配对”（如 BTC/USDT, XRP/USDT 等），输出提醒信号。
  3) Runner（提醒模式）：加载配置（交易对映射与阈值），并以 CLI/日志 + WebSocket/SSE 将实时价差与信号推送至 Panel（初版控制台展示可用）。
  4) 面板（初版）：最简看板（表格 + z-score 折线图），支持模式切换（提醒/自动，初期仅提醒）。
- 阶段 2：自动交易与执行细化
  1) 为 `LighterConnector` 与 `AsterConnector` 增强交易执行接口（限价/市价、滑点守护、撤单/补单、状态跟踪）。
  2) 风控：名义/敞口限制、冷却时间、熔断、最小流动性与最大滑点约束。
  3) 持久化：SQLite（信号/成交/持仓/日志）。
- 阶段 3：多平台扩展与生产化
  1) 新增交易所 Connector（按统一接口快速接入）。
  2) 面板强化（参数在线调整、历史回放）、监控告警、部署与灰度发布。

## 6. 关键参数（初稿）
- enter_z_high = 2.0（入场阈值）；exit_z_low = 0.5（出场阈值）。
- lookback_secs = 900（15 分钟滚动窗口）。
- min_liquidity_usd = 25_000（两侧最小可成交深度）。
- max_slippage_bps = 10–30（根据市场微调）。
- max_gross_notional_usd = 50_000；max_legs = 3。
- min_hold_secs = 30；min_reentry_secs = 60。
- stop_z = 4.0（极端偏离止损/减仓）。

## 7. 配置与机密
- 分离配置文件（YAML/ENV）：API host、账户/密钥、交易对映射、参数阈值。
- 机密管理：环境变量/本地密钥管理器，避免明文入库；生产环境隔离。

## 8. 运行与部署（建议）
- 开发：Poetry/venv；本地 Linux 或 macOS（用于签名器）。
- 测试网演练：使用 examples 中的模式与账户示例，先跑提醒模式，再逐步开启小额自动交易。
- 部署：Docker（基础镜像含 Python + 依赖）；分环境配置（testnet/mainnet）。

## 9. 开放问题
- 具体目标交易对/市场列表与资产优先级？
- 仅 Lighter 内部市场配对，还是跨平台？如跨平台，目标交易所名单？
- 目标杠杆/资金规模与最大承受回撤？
- 前端偏好：快速的 Streamlit 还是自定义前后端分离？

## 10. 代码骨架（最小实现）
- 目录结构（新增）：
  - `arb/` 策略与执行主包
    - `connectors/`
      - `base.py` 抽象接口：行情（mid/订单簿）、账户（后续）、交易（后续）
      - `lighter.py` Lighter 只读行情（先用 REST 拉取中位价/最优双边）
      - `aster.py` Aster 只读行情（REST 拉取，如最新价/订单簿）
    - `signal/zscore.py` 指标与信号计算
    - `models.py` 配对、样本、信号等数据结构
    - `config.py` 读取 YAML/ENV 的统一配置
    - `runner_reminder.py` 提醒模式入口，汇总并输出/推送 Panel 数据
    - `runner_auto.py` 自动交易入口（占位）

新增能力：
- Lighter 市场自动发现：从 `/api/v1/orderBooks` 拉取 `symbol -> market_id` 映射（已接入提醒 Runner）。
- 历史价差存储：`arb/storage/sqlite.py` 使用 SQLite 记录 spread/z-score；Panel 读取展示。
- Panel API：`arb/panel/server.py`（FastAPI），接口示例：
  - `GET /api/spreads?pair=BTCUSDT&limit=1000` 返回可绘图的数据（旧→新）。
  - `GET /health` 健康检查。

---

附注：本仓库代码为 SDK（API 客户端 + 签名器），不是 DEX 服务端。一般无需改动 SDK 源码；作为开发者只需调用接口与类即可。若后续发现缺少特定端点或能力，可新开 issue 或加适配层，不建议直接 fork 改动生成代码模块。
