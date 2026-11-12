# 口径校准台

一个纯本地、离线运行、结果可复现的**多口径指标校准流水线**：读取本地 YAML 口径声明与 CSV 数据样例，逐口径计算指标，交叉比对分组结果，把差异归因到 filter / metric / exclusions / grain 条款，最终输出可归档的 Markdown、JSON 报告并写入本地 SQLite 台账。

## 1. 定位与边界

**是**：

- 配置驱动的多口径指标校准引擎。
- 口径声明可插拔的 YAML 工作流。
- 确定性、可复现、可回归测试的计算流水线。
- 面向「口径差异清单 + 证据线索 + 校准建议」的分析口径治理工具。

**不是**：

- 不是报表统计工具、数据看板或可视化产品。
- 不是通用 ETL、SQL 执行器、云端服务或调度平台。
- 不连接真实数据库，不调用外部 API，不做预测或异常检测模型。
- 不提供用户体系、权限、多租户、上传或远程 URL 抓取。

核心产出是**差异和解释**，而不是图表。

## 2. 快速上手

### 2.1 环境

- Python 3.10+
- 仅依赖 `PyYAML`、`Flask`、`pytest`
- 不需要网络服务；首次安装依赖时可由本地包源或已缓存 wheels 完成

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

### 2.2 跑测试

```bash
pytest -q
```

测试只读取仓库内合成 fixture，数据库与报告写入 pytest 临时目录，不污染仓库。

### 2.3 启动本地服务

```bash
python -m koujing.server
```

默认仅监听 `127.0.0.1:5000`。浏览器打开 `http://127.0.0.1:5000/` 可看到本地说明页；页面样式内联，不引用 CDN、字体或任何外部资源。

可用参数：

```bash
python -m koujing.server --port 5001
```

服务端不会接受自定义 host、上传文件、任意数据目录或远程地址。

### 2.4 跑一次 CLI 校准

```bash
python -m koujing.cli run \
  --profiles profile_sales_a \
  --profiles profile_sales_b \
  --dataset-dir fixtures/datasets \
  --out report/
```

输出样例（任务 ID 由口径声明、数据文件内容与容差共同决定）：

```text
task_id: kj-35cd48628bfd
summary: profiles=2 matched=4 over=2 only_base=1 only_compare=1
report: report\calibration-kj-35cd48628bfd.json
report: report\calibration-kj-35cd48628bfd.md
```

默认容差为绝对值 `0`、相对值 `0.1`，可显式覆盖：

```bash
python -m koujing.cli run \
  --profiles profile_sales_a --profiles profile_sales_b \
  --tolerance-abs 50 --tolerance-rel 0.05 \
  --out report/
```

默认**不写入生成时间**，因此同一命令连续运行两次，报告文件字节级一致。如果需要在报告中记录时间：

```bash
python -m koujing.cli run \
  --profiles profile_sales_a --profiles profile_sales_b \
  --timestamp --out report/
```

查看台账：

```bash
python -m koujing.cli list --limit 20
python -m koujing.cli show kj-35cd48628bfd
python -m koujing.cli show kj-35cd48628bfd --out report/replay
```

`show --out` 会按 task_id 从 SQLite 读取完整结果并重新生成 Markdown 与 JSON 报告。

### 2.5 HTTP 接口

```text
GET  /                  本地静态说明页
GET  /tasks             最近任务 JSON，支持 ?limit=N
GET  /tasks/<task_id>   任务详情 JSON
POST /run               触发一次本地校准
```

示例：

```bash
curl -s http://127.0.0.1:5000/run \
  -H "Content-Type: application/json" \
  -d '{"profiles":["profile_sales_a","profile_sales_b"],"tolerance_abs":0,"tolerance_rel":0.1}'
```

`/run` 只接受 fixture 中已有的 profile id；请求体不接受任意路径、上传文件、远程地址或数据集目录。未知字段与非法容差返回 400 结构化错误。

## 3. 口径声明 YAML

每个 YAML 文件声明一个 profile。目录下所有 `*.yaml` 按文件名稳定排序加载；指定 `--profiles` 时按请求顺序加载。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | 唯一标识，字母数字开头，仅含字母、数字、`_`、`-` |
| `name` | string | 人类可读名称 |
| `source` | string | `fixtures/datasets` 下的 CSV 文件名，不含路径 |
| `grain` | enum | `order` / `user` / `event`，声明数据观察粒度 |
| `filter` | string | 行级过滤表达式，必须返回布尔值，不能含聚合函数 |
| `metric` | string | 聚合指标表达式，必须至少包含一个聚合函数 |
| `dimensions` | list[string] | 非空分组维度列名；比对双方必须完全一致 |
| `exclusions` | list[string] | 排除条件；任一条为真即剔除该行，可为空列表 |
| `notes` | string | 口径备注，允许空字符串 |

加载时严格校验：缺失字段、未知字段、类型错误、非法 grain、路径穿越、缺失数据集、重复列、维度列不存在、表达式语法错误、未知列、filter 中出现聚合、metric 缺少聚合，都会返回带具体路径的错误，例如：

```text
profile_sales_a.yaml:metric: 表达式错误: unexpected_token: ...
```

`grain` 是数据观察单元声明，参与口径归因；输出分组键由 `dimensions` 决定。源数据应已经处于声明粒度，本工具不做跨粒度上卷。

完整示例（`fixtures/profiles/profile_sales_a.yaml`）：

```yaml
id: profile_sales_a
name: 甲公司已支付订单销售额口径
source: orders_a.csv
grain: order
filter: "status == 'paid'"
metric: "sum(amount)"
dimensions:
  - region
exclusions:
  - "channel == 'test'"
notes: "仅统计已支付订单，排除测试渠道；分组维度为区域，订单粒度。"
```

### 3.1 执行顺序

```text
读取 CSV
→ 应用 filter
→ 应用 exclusions（任一排除表达式为真则剔除）
→ 按 dimensions 分组
→ 在组内执行 metric 聚合
→ 记录统计与无法解析行数
```

统计字段：

- `rows_read`：读取的 CSV 数据行数。
- `rows_filtered`：filter 返回真的行数。
- `rows_excluded`：被 exclusions 剔除的行数。
- `rows_aggregated`：成功进入输出分组的行数。
- `groups`：成功聚合的分组数。
- `unparsable_rows`：filter / exclusion / 维度 / 指标求值失败的行数；某组指标聚合失败时，该组行数计入此项。

## 4. 表达式语法

安全求值器使用自写分词器与递归下降解析器，**不调用 Python 的 eval / exec**。

支持：

- 数值字面量：`1`、`2.5`。
- 字符串字面量：`'paid'`、`"settled"`，支持 `\\`、`\'`、`\"`、`\n`、`\t`。
- 布尔字面量：`true`、`false`。
- 列引用：如 `amount`、`region`。
- 算术：`+`、`-`、`*`、`/`、一元 `+` / `-`。
- 比较：`==`、`!=`、`<`、`<=`、`>`、`>=`。
- 逻辑：`and`、`or`、`not`，支持短路求值。
- 集合判断：`region in ['华东', '华南']`。
- 括号与列表字面量。
- 聚合函数：`sum`、`count`、`avg`、`min`、`max`。

明确不支持：

- 任何 SQL 语法、子查询、JOIN、窗口函数。
- 正则、日期时间函数、字符串截取或拼接。
- 取模 `%`、幂运算 `**`、位运算、赋值、三元表达式。
- 用户自定义函数或未列出的函数。
- 聚合函数嵌套，例如 `sum(avg(amount))`。
- filter 中使用聚合；metric 不使用聚合。
- `null` / 缺失值专用语义；CSV 中的空字符串参与数值运算会返回结构化类型错误。
- 任意代码执行、导入模块或访问文件系统。

说明：

- CSV 中的数值字符串会在需要时转换为数字，`amount > 100` 可直接使用。
- 两个字符串比较保持字典序；字符串与数字比较时尝试数值转换。
- 除零、未知列、类型不匹配、未知函数、非法语法均返回 `ExpressionError`，包含 `code`、`message`、`position`。
- 指标结果只允许有限数值；NaN 与无穷大会被拒绝。
- 计算值统一保留最多 6 位小数，整数形态归一为整数，保证 JSON 稳定。

## 5. 比对、归因与报告

### 5.1 比对规则

- 第一个 profile 是基准，其余 profile 分别与基准做两两比对。
- 双方 `dimensions` 必须完全一致，否则返回结构化错误。
- 分组按维度键对齐，输出：匹配组、仅基准有组、仅比对有组。
- 匹配组计算 `diff = compare - base` 与差异率 `diff / abs(base)`。
- 基准值为 0 且差异非 0 时，差异率记录为 `null`，并标记 `base_zero: true`，不会输出 `inf`。
- 分组视为容差内，当且仅当：`abs(diff) <= tolerance_abs` **或** `abs(diff_rate) <= tolerance_rel`。
- 容差边界包含等号；所有容差必须是非负有限数。

### 5.2 归因规则

对每个差异组（超差组、仅单侧有组），逐项比对：

- `filter`
- `metric`
- `exclusions`（规范化 JSON 字符串）
- `grain`

只收集真实不同的条款，生成「候选证据」，不声称因果关系。每条线索都标注候选影响分组（当前比对中全部差异组）。若声明条款完全相同，即使数值不同也不生成条款级归因。

### 5.3 报告字段

JSON 顶层键顺序固定：

```text
task_id
generated_at
base_profile
summary
profiles
calculations
comparisons
attributions
suggestions
```

Markdown 固定包含七个章节：

1. 任务元信息
2. 参与口径清单
3. 各口径计算统计
4. 比对结果
5. 超差与缺失分组明细
6. 差异归因线索
7. 校准建议

Markdown 片段：

```markdown
## 4. 比对结果

| 基准 | 比对 | 匹配组 | 容差内 | 超差 | 仅基准 | 仅比对 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| profile_sales_a | profile_sales_b | 4 | 2 | 2 | 1 | 1 |

## 5. 超差与缺失分组明细

| 类型 | 基准口径 | 比对口径 | 分组键 | 基准值 | 比对值 | 差异 | 差异率 | 标记 |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| 超差 | profile_sales_a | profile_sales_b | region=华中 | 400 | 600 | 200 | 50.0000% | 超差 |
| 仅基准有 | profile_sales_a | profile_sales_b | region=西北 | 450 | — | — | — | 缺失 |
```

### 5.4 SQLite 台账

单文件 `koujing.db`，包含：

- `tasks`：任务元信息与完整 payload。
- `profiles_loaded`：本次参与口径及计算统计。
- `comparisons`：比对摘要。
- `diffs`：超差与单边缺失明细。
- `attributions`：条款级归因线索。

写入使用单个事务；任何插入失败都会回滚，不留下半截任务。`task_id` 由口径声明、数据文件内容哈希与容差确定，同一输入重复运行会幂等替换同一任务，而不是产生重复记录。

## 6. 目录结构

```text
.
├── README.md
├── CHANGELOG.md
├── requirements.txt
├── pyproject.toml
├── .gitignore
├── koujing/
│   ├── __init__.py
│   ├── server.py            # Flask 应用与路由
│   ├── cli.py               # run / list / show
│   ├── config.py            # 路径与默认容差
│   ├── models.py            # dataclass 与固定序列化
│   ├── loader.py            # YAML 加载与严格校验
│   ├── store.py             # SQLite 存取与事务
│   ├── store_schema.py      # 建表 DDL
│   ├── expression.py        # 安全分词、解析、校验
│   ├── expr_eval.py         # 安全求值器实现
│   ├── calculator.py        # 单口径计算
│   ├── comparator.py        # 交叉比对与容差
│   ├── attributor.py        # 条款级归因与建议
│   ├── reporter.py          # Markdown / JSON 报告
│   ├── runner.py            # 共享编排入口
│   └── templates/
│       └── index.html       # 本地静态说明页
├── fixtures/
│   ├── profiles/
│   │   ├── profile_sales_a.yaml
│   │   ├── profile_sales_b.yaml
│   │   └── profile_active_user.yaml
│   ├── datasets/
│   │   ├── orders_a.csv
│   │   ├── orders_b.csv
│   │   └── events.csv
│   └── expected/
│       └── sales_gap.json
└── tests/
    ├── conftest.py
    ├── test_expression.py
    ├── test_loader.py
    ├── test_calculator.py
    ├── test_comparator.py
    ├── test_attributor.py
    ├── test_reporter.py
    ├── test_runner.py
    ├── test_server.py
    └── test_store.py
```

`expression.py` / `expr_eval.py` 与 `store.py` / `store_schema.py` 的拆分是为了让每个源文件不超过 300 行；对外入口仍然是同一个包。

## 7. 明确不实现

- 不接入任何真实数据库、数据仓库、消息队列、云存储。
- 不实现 SQL 解析或 SQL 执行。
- 不实现前端交互式图表、可视化看板、拖拽式报表。
- 不实现指标预测、异常检测模型、机器学习。
- 不实现用户认证、权限、多租户。
- 不实现数据上传、任意路径读取、远程 URL 抓取。
- 不实现与 Excel 的双向导入导出。
- 不实现调度器、分布式执行、并发任务编排。
- 不实现跨粒度上卷；`grain` 仅作为声明与归因依据。
- 不实现报告 HTML 页面；Markdown 与 JSON 是正式产物。

以上清单与实际代码一致：仓库没有数据库驱动、网络客户端、图表库、上传路由或调度组件。

## 8. 离线、确定性与合规声明

- 运行期不发起任何外部网络请求；服务仅监听回环地址 `127.0.0.1`。
- 页面资源全部本地化，不引用 CDN、外部字体、外部脚本或图片。
- 数据、口径、期望输出全部来自仓库内 fixture，且均为合成内容，不含真实公司、真实人名、真实金额样例、真实域名或外部 IP。
- 任务 ID、分组顺序、归因顺序、JSON 键顺序、Markdown 章节顺序均为确定性逻辑。
- 默认关闭时间戳；启用 `--timestamp` 时仅影响 `generated_at` 字段，不影响 task_id。
- 报告中数值最多保留 6 位小数，不输出 NaN / Infinity。
- 本工具只处理本地样例数据；使用者应自行确保实际接入数据时的合规性，但本项目不包含该接入能力。

## 9. 主要验收命令

```bash
pip install -r requirements.txt
pytest -q
python -m koujing.server
python -m koujing.cli run \
  --profiles profile_sales_a --profiles profile_sales_b \
  --dataset-dir fixtures/datasets --out report/
```
