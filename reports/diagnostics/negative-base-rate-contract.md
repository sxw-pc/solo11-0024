# 负基准下 `diff_rate` 求值契约漂移诊断报告

- 报告日期：2026-11-14
- 范围：`koujing/comparator.py`、`koujing/models.py`、`koujing/store.py`、
  `koujing/reporter.py`、`koujing/runner.py`、`koujing/cli.py`
- 约束：**未修改任何业务代码**；全部复现脚本与临时测试位于仓库外
  `/tmp/kj-repro/`，pytest 依赖安装在 `/tmp/kj-pylibs/`。
- 验证基线：`pytest -q tests/test_comparator.py tests/test_store.py tests/test_runner.py`
  → **27 passed**；全量 `pytest -q` → **92 passed**。

---

## 1. 摘要（结论先行）

差异率的"求值—序列化—落库—回读—渲染"链路上存在 **一个已实现的隐式契约**（分母取
`abs(base_value)`，即差异率是**无方向的相对幅度**），但该契约从未被显式声明，且与
`diff` 的有向符号、Markdown 的带符号百分比显示、以及 `diffs` 表用 SQL `NULL` 同时
表示多种语义的做法互相拉扯。具体结论分三层：

1. **真实回读链路（`get_task` → `render_markdown`，即 `show --out`）当前并不产生
   自相矛盾的输出**。原因是 `Store.get_task` 只回读 `tasks.payload_json`
   （`koujing/store.py:210-227`），`diffs` 表是**只写表**，全仓没有任何代码从
   `diffs` 反查渲染。因此同一份 payload 渲染两次必然字节一致（复现实测确认）。
2. **矛盾是语义层面与潜伏性的**：负基准时 `diff_rate` 的符号方向与金融学惯例
   `(compare-base)/base` 相反；极小基准时 6 位小数归一化会把真实差异静默抹成
   `diff=0 / rate=0 / within_tolerance=True`；`diffs.diff_rate` 的 `NULL` 把
   "基准为 0 不计算"、"仅基准有"、"仅比对有"三种语义合并成一个值。
3. **一旦未来有人把 `diffs` 表当作查询源（建表 DDL 显然是为此准备的，见
   `store_schema.py:49-63`），或把分母改成带符号 `base_value`，现有潜伏问题会立即
   升级为显性的方向与容差误判**。本报告第 7 节给出单一求值契约建议。

---

## 2. 涉及代码与当前事实

| 环节 | 位置 | 当前行为 |
| --- | --- | --- |
| 求值 | `koujing/comparator.py:70-87` | `diff = compare - base`；`base_zero = base_value == 0`；零基准时 `rate = 0.0 if diff == 0 else None`；否则 `rate = normalize(diff / abs(base_value))`；`relative_ok = rate is not None and abs(rate) <= rel_tolerance` |
| 序列化 | `koujing/models.py:162-205` | `MatchedGroup.diff_rate`（`float | None`）与 `base_zero` 原样进 `to_dict()`，`None` → JSON `null` |
| 落库 | `koujing/store.py:149-187` | 仅 `within_tolerance=False` 的 matched 写 `diffs`；`diff_value/diff_rate` 直接取 payload；`only_base/only_compare` 行固定写 `diff_value=NULL, diff_rate=NULL, base_zero=0` |
| 回读 | `koujing/store.py:210-227` | 只 `json.loads(tasks.payload_json)`，**不读 `diffs` 表** |
| 渲染 | `koujing/reporter.py:27-30, 93-128` | `_rate(None)` 无条件返回 `"分母为0，不计算"`；否则 `f"{float(value):.4%}"`（带符号百分比）；`base_zero` 行标记 `"分母为0"` |
| 回读重渲染 | `koujing/runner.py:142-151`、`koujing/cli.py:100-114` | `regenerate_reports` / `show --out` 均以回读 payload 调 `write_reports` |
| 归一化 | `koujing/models.py:12-23` | float 一律 `round(_, 6)`，结果为整数则降级为 `int`；非有限值抛错；`-0.0` → 整数 `0` |

---

## 3. 可复现的输入样例

仓库自带 `fixtures/datasets/*.csv` 的指标均为正数（已核查，无负值/零值分组），无法
经 CSV 自然触发负基准。因此触发输入按测试套件自己的构造方式（对照
`tests/conftest.py:48-75` 的 `calc_factory`）直接构造 `CalculationResult`，覆盖题目
要求的三类情形：

| 分组键 | base | compare | 触发意图 |
| --- | ---: | ---: | --- |
| `neg-flip` | **-100** | **110** | 负基准、跨越正负号翻转，`diff=210` |
| `neg-mirror` | 100 | -110 | 上一行的镜像，用于对照符号方向 |
| `tiny-hidden` | 0.0000004 | 0.0000008 | 极小基准，真实相对变化 +100% |
| `zero-both` | 0 | 0 | base=0 且 compare=0 |
| `zero-nonzero` | 0 | 5 | base=0 且 compare≠0 |
| `exact-zero` | 100 | 100 | diff 恰为零（非零基准） |
| `small-over` | 0.000001 | 0.000002 | 极小但未被抹零的 100% 超差 |
| `only-base-side` / `only-compare-side` | 7 / 缺失 | 缺失 / 9 | 单侧缺失行 |

容差统一取 `tolerance_abs=0, tolerance_rel=0`（任何非零差异即超差），保证
`diffs` 行可观察。

---

## 4. 逐步复现命令

> 本机没有预装 pip/pytest。以下命令把 pytest、PyYAML、Flask 安装到仓库外的
> `/tmp/kj-pylibs`，不触碰系统环境与仓库文件。

```bash
# 0) 基线（应全绿）
PYTHONPATH=/tmp/kj-pylibs:/workspace python3 -m pytest -q \
    tests/test_comparator.py tests/test_store.py tests/test_runner.py

# 1) 全链路一次性复现：compare -> to_dict -> save_task -> 直查 diffs
#    -> get_task 回读 -> render_markdown，并打印 8 项定向探针
python3 -I /tmp/kj-repro/repro_negative_base.py /workspace /tmp/kj-pylibs \
    | tee /tmp/kj-repro/repro-output.txt

# 2) 真实 CLI 回读重渲染路径（show --out）
PYTHONPATH=/tmp/kj-pylibs:/workspace python3 -m koujing.cli show \
    kj-repro-negative-base --db /tmp/kj-repro/ledger/repro.db \
    --format markdown --out /tmp/kj-repro/regen
grep -E "neg-flip|zero-nonzero" /tmp/kj-repro/regen/calibration-kj-repro-negative-base.md

# 3) 以 pytest 形式固化"当前（有争议的）行为"（6 项特征化测试，位于仓库外）
PYTHONPATH=/tmp/kj-pylibs:/workspace python3 -m pytest -q \
    /tmp/kj-repro/test_char_current_behavior.py
```

---

## 5. 观察结论（实测输出）

### 5.1 求值层：负基准的方向翻转（探针 A/B，均 OBSERVED）

```
key          base     compare   diff   rate(abs)  signed(diff/base)  base0  within
neg-flip     -100.0   110.0     210    2.1        -2.1               False  False
neg-mirror    100.0  -110.0    -210   -2.1        -2.1               False  False
```

- `neg-flip` 中 `diff=+210`（compare 比 base 大），但按 `diff/abs(base)` 得到
  **`diff_rate=+2.1`（+210%）**；按带符号金融惯例 `diff/base` 应为 **-2.1（-210%）**。
- 镜像两行的**绝对变化量同为 210**，在带符号分母下两者比率都应是 `-2.1`；当前实现
  却给出 `+2.1` 与 `-2.1` 两个相反符号。即：**`diff_rate` 的符号跟随 `diff`，而不是
  跟随"相对基准的变化方向"**。
- 容差判定本身没有算错：`abs(rate) <= tolerance_rel`（`comparator.py:77`）配合
  `abs()` 分母，构成的是纯粹的**幅度比较**。问题在于 Markdown 用 `:.4%` 把这个
  "幅度"渲染成带正负号的百分比（`reporter.py:30`），读者会把符号误读为方向。

### 5.2 求值层：极小基准被归一化静默抹零（探针 D，OBSERVED）

```
tiny-hidden   base=4e-7  compare=8e-7  -> diff=0  rate=0.0  base_zero=False  within=True
```

- `normalize_number` 在做差前后分别 `round(..., 6)`（`models.py:12-23`）：
  `4e-7 → 0`（int）、`8e-7 → 1e-06`，于是 `compare-base = 1e-06 → round 后 = 0`。
- 一个真实 **+100%** 的相对变化被报告为 `diff=0, diff_rate=0.0,
  within_tolerance=True`，并且因为 `_insert_diffs` 跳过容差内分组
  （`store.py:158-159`），**台账里连一行诊断记录都不会留下**——这是最危险的静默
  None/零值场景。
- 附带边界：`normalize_number(-0.0)` 返回整数 `0`（实测），因此 `-0.0` 基准会被
  判为 `base_zero=True`，与"基准真的为零"不可区分。

### 5.3 零基准两分支（探针 E，OBSERVED）

```
zero-both      base=0 compare=0  -> diff=0  rate=0.0  base_zero=True  within=True
zero-nonzero   base=0 compare=5  -> diff=5  rate=None base_zero=True  within=False
```

- `compare_calculations` 内部两情形区分正确（`comparator.py:72-73`）。
- 但 JSON 里 `0.0` 与 `null` 的差异在下游被削弱：`diffs` 表中 `zero-both` 因
  within 被整行跳过，`zero-nonzero` 的 `diff_rate` 落为 `NULL`——**只看 `diffs`
  表无法区分"零基准零差异（未落库）"与"零基准非零差异（NULL）"，更无法与单侧
  缺失行的 NULL 区分**（见 5.5）。

### 5.4 diff 恰为零

- 非零基准 `exact-zero`：`rate = normalize(0/100) = 0.0`（float，因走 else 分支并经
  `float(...)` 包装）。
- 零基准 `zero-both`：rate 由字面量 `0.0` 赋值。
- 两者内存中都是 `0.0`，JSON 都是 `0.0`，**当前没有实际冲突**；但两条产生路径
  （字面量 vs 除法）没有统一的构造函数，属于契约漂移的温床。

### 5.5 落库层：`diffs` 表的 NULL 多义与类型漂移（探针 F/G，OBSERVED）

直查 SQLite 的实测行：

```
label                 base     compare   diff      rate   base0  status
region=neg-flip       -100.0   110.0     210.0     2.1    0      over_tolerance
region=zero-nonzero   0.0      5.0       5.0       NULL   1      over_tolerance
region=only-base-side 7.0      NULL      NULL      NULL   0      only_base
region=only-compare-side NULL  9.0       NULL      NULL   0      only_compare
```

- **NULL 三义**：`diff_rate IS NULL` 同时命中（a）零基准非零差异的超差行、
  （b）仅基准有、（c）仅比对有。`base_zero` 列只能把（a）与（b/c）分开，
  （b）与（c）仍完全同形。
- **类型漂移**：payload 中 `diff=5` 是 `int`（`normalize_number` 降级所致），
  进 REAL 列后变成 `5.0`（float）。任何按 `isinstance(x, int)` 做下游判断的消费方
  在两个数据源上会得到不同结果。
- 实测 `_rate(None) == "分母为0，不计算"`（探针 H，OBSERVED，`reporter.py:27-30`）。
  今天渲染只读 payload、单侧行在 `_detail_table` 里另有分支，所以显示正确；但只要
  未来改为从 `diffs` 表驱动渲染，单侧缺失行的 NULL 会被这句硬编码文案误标成
  "分母为 0"。

### 5.6 回读重渲染层：当前一致，但一致性是"巧合"（第 5 节实测）

```
payload before/after save identical: True
markdown live vs reloaded identical: True
```

`show --out` 与 `regenerate_reports` 都只消费 `payload_json`，因此同一组 matched
在精确比对、JSON、Markdown、回读重渲染四处当前给出相同结果。**题目担心的
"互相矛盾的方向与 base_zero 标记"在现版本不可直接复现**；真正可复现的是：
①负基准下符号方向与惯例相反（5.1）；②极小基准静默抹零且不落任何诊断（5.2）；
③`diffs` 表的 NULL/类型契约与 payload 不同构（5.5）。一旦启用 `diffs` 作为查询源
或修改分母公式，矛盾即显性化。

---

## 6. 根因定位

1. **缺少单一求值函数与显式契约**。`diff / abs(base_value)` 这一关键公式内联在
   `compare_calculations` 里（`comparator.py:75`），"比率是无方向幅度"这一决策没有
   命名、没有文档、没有类型载体；渲染层却按"有方向百分比"显示（`:.4%`），存储层
   又用可空 REAL 承接。三层各自合理，组合后语义不闭合。
2. **`None` 被复用为多重哨兵**。零基准非零差异 → `None`；单侧缺失 → SQL NULL；
   字段缺省也是 NULL。哨兵没有判别字段（`diffs` 表无 `rate_status` 之类的枚举列），
   `_rate(None)` 又把 None 唯一映射成"分母为0"文案。
3. **归一时序先于差异判定**。`normalize_number` 的 6 位小数舍入发生在差值进入容差
   比较之前（`comparator.py:70`），小量级基准的真实差异在比较开始前就被抹成 0，
   且没有任何"被舍入吞没"的结构化提示。
4. **双数据源不同构**。`payload_json`（JSON，含 int/null/完整状态枚举）与 `diffs`
   表（REAL、NULL 多义、只存超差行）描述同一组差异，却没有共享的 (de)serializer，
   `_insert_diffs` 直接搬字段（`store.py:160-170`），类型与语义漂移无人守门。

---

## 7. 单一求值契约建议

### 7.1 推荐契约：显式的"无方向幅度 + 有方向差值"双字段（方案 A，迁移风险最低）

把求值收敛到**一个纯函数**（例如 `evaluate_diff(base, compare) -> DiffVerdict`），
全链路只允许它产出 `diff/diff_rate/base_zero/within_tolerance`，`models`、`store`、
`reporter` 不得再自行解释：

```python
# 建议的语义（伪代码，不在本次改动范围内实施）
@dataclass(frozen=True)
class DiffVerdict:
    diff: float                      # 永远 = compare - base，唯一的方向载体
    diff_rate: float | None          # 永远 >= 0 的相对幅度；不可计算时为 None
    rate_status: str                 # "computed" | "base_zero_equal" |
                                     # "base_zero_nonzero" | "rounded_to_zero"
    base_zero: bool
    within_tolerance: bool
```

契约条款：

1. **方向符号约定**：`diff = compare - base` 保留符号，是唯一方向字段；
   `diff_rate = |compare-base| / |base|` **恒为非负幅度**，符号不承载方向语义。
   Markdown 渲染幅度时去掉正负号（`f"{rate:.4%}"` 而非 `:.4%` 的带符号输出），
   方向改由 `diff` 列或独立箭头标记表达，消除"负基准下 +210%"的误读。
2. **零基准**：`base==0 且 diff==0` → `rate=None, rate_status="base_zero_equal"`；
   `base==0 且 diff!=0` → `rate=None, rate_status="base_zero_nonzero"`。**不再用
   数值 `0.0` 表达"零基准零差异"**，让 `None + 判别枚举`承接全部不可计算情形，
   替代今天 `0.0` 与 `None` 并存的两分支。
3. **舍入吞没**：当原始（舍入前）差值非零、舍入后为零时，置
   `rate_status="rounded_to_zero"`，且该分组**仍写一行 `diffs`**（见第 8 节），
   不得静默归入 within。
4. **容差**：保持 `absolute_ok or (rate_status=="computed" and rate <= rel)`。
   因为幅度恒非负，判定不再需要 `abs(rate)`，但**必须保留"仅对 computed 状态做
   相对判定"的守卫**。

> 备选方案 B（带符号金融惯例 `diff_rate = diff/base`）语义最直观，但会改变所有
> 负基准分组的历史输出与任务摘要，并使 `fixtures/expected/sales_gap.json` 等字节级
> 快照需要重基线；在当前数据全为正数的情况下收益为零、成本不为零，故不推荐。
> 若未来确需带符号比率，应**新增字段**（如 `diff_rate_signed`）而不是改写现有字段。

### 7.2 `base_zero` 与 `within_tolerance` 的兼容风险

- **`abs()` 不可随手删除**：若采纳方案 B 或任何让 `diff_rate` 带符号的改法，却把
  `comparator.py:77` 的 `abs(rate)` 一并去掉，则负基准的负比率（如 -2.1）会满足
  `-2.1 <= 0.1` 而**全部误判为容差内**。探针 C 已实测该陷阱（OBSERVED）。
- **零基准只能靠绝对容差获救的现状要保留**：`base_zero=True` 时相对判定恒为
  False（`rate is None`），`within_tolerance` 完全取决于 `absolute_ok`。现有测试
  `test_zero_denominator_within_absolute_tolerance` 锁定了该行为，契约调整时必须
  继续通过。
- **`base_zero` 的输入边界**：`-0.0` 经归一化等于 `0`，建议在契约文档中明确
  "base_zero 针对归一化后的值"，并在 `rate_status` 中保留可追溯性。
- **`within_tolerance` 与落库跳过逻辑耦合**：`_insert_diffs` 以
  `within_tolerance` 为唯一过滤条件（`store.py:158`）。任何新增的
  `rounded_to_zero` 诊断若不调整该过滤，就无法落库；调整过滤则会改变
  `tests/test_store.py` 中 `diffs == 4` 的计数预期，需同步评估。

### 7.3 需要返回结构化诊断而非静默 None/零的位置

| # | 位置 | 现状 | 建议 |
| --- | --- | --- | --- |
| 1 | `comparator.py:72-75` | 零基准与不可计算用裸 `None`，极小值抹零无感知 | 统一返回 `DiffVerdict`，以 `rate_status` 枚举区分四种状态 |
| 2 | `models.py:119` (`MatchedGroup.diff_rate`) | `float | None` 单字段 | 增加 `rate_status` 字段并在 `to_dict()` 输出，使 JSON 消费方可判别 |
| 3 | `store.py:160-187` (`_insert_diffs`) | NULL 同时表示三种语义；within 行整行不写 | `diffs` 增加 `rate_status TEXT NOT NULL` 列；`rounded_to_zero` 行强制落库；单侧行状态分别为 `only_base/only_compare` 而非 NULL 哨兵 |
| 4 | `reporter.py:27-30` (`_rate`) | `None` 硬编码"分母为0，不计算" | 改为按 `rate_status` 分派文案（不可计算/双侧为零/舍入吞没/缺失），幅度不再输出带符号百分号 |
| 5 | `models.py:12-23` (`normalize_number`) | 静默 `round` 并 int 降级 | 增加可选的"舍入前后是否变化"返回或审计计数，供比较层识别 `rounded_to_zero` |
| 6 | `store.py:210-227` (`get_task`) | 只回读 payload，`diffs` 表无人消费 | 若未来开放 `diffs` 查询接口，必须返回 `rate_status` 而非让调用方猜 NULL；在此之前建议在表注释/文档中标注该表为"仅写诊断表" |

---

## 8. 影响面清单

- **输出字节稳定性**：方案 A 若同时修改 Markdown 百分号格式，会改变报告快照；
  `tests/test_runner.py::test_expected_sales_gap_fixture` 与
  `tests/test_reporter.py` 的字符串断言需要重基线。当前 fixtures 全为正数，
  `diff_rate` 数值本身不变，仅显示形态变化。
- **数据库 schema**：`diffs` 加列属于 DDL 变更；`Store` 没有迁移机制
  （`store_schema.py` 仅 `CREATE TABLE IF NOT EXISTS`），需要版本化或重建库策略。
- **HTTP 服务**：`server.py` 直接透传 payload，新增枚举字段对旧客户端是向后兼容的
  加法；但任何依赖 `diff_rate is None` 做判断的客户端应改为读 `rate_status`。
- **任务 ID 稳定性**：求值契约变更不进入 `build_task_id` 的 seed
  （`runner.py:51-64` 只含 profile、数据集摘要、容差），task_id 不受影响；但报告
  字节变化会影响 `test_repeated_run_is_byte_identical` 之外的外部比对流程。
- **测试资产**：`tests/test_comparator.py` 中
  `test_relative_tolerance_boundary_is_inclusive`、两个 `zero_denominator` 用例锁定
  了现状数值，契约落地时应先把本报告 `/tmp/kj-repro/test_char_current_behavior.py`
  的特征化用例迁入仓库并按新契约改写期望，再改实现。

---

## 9. 附录：复现产物与测试记录

- 复现脚本：`/tmp/kj-repro/repro_negative_base.py`（仓库外，未改业务代码）
- 特征化 pytest：`/tmp/kj-repro/test_char_current_behavior.py` → **6 passed**
- 一次性台账：`/tmp/kj-repro/ledger/repro.db`；重渲染产物：`/tmp/kj-repro/regen/`
- 完整探针输出：`/tmp/kj-repro/repro-output.txt`（8 项探针全部 OBSERVED）
- 要求的回归命令：
  `pytest -q tests/test_comparator.py tests/test_store.py tests/test_runner.py`
  → **27 passed**；全量 `pytest -q` → **92 passed**。
