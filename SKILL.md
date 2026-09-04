---
name: autoroute
description: Automatically analyze every coding task and route it to the best available Codex model and reasoning effort. Use for all ordinary coding tasks unless the user requests a specific model or effort or configures suggest/manual routing.
metadata:
  short-description: Route coding tasks to the right model
---

# AutoRoute

AutoRoute is a routing layer for Codex coding work. Its goal is to minimize total cost for the required quality, including retries and failure recovery—not merely to minimize reasoning tokens.

## Operating contract

- Discover available models and supported reasoning levels at runtime. Do not assume that a model named in documentation or an old catalog is available to this user.
- On first use or after the availability TTL, run the lightweight model probes and cache their results. Route to verified models when probe results are available; if a selected model execution fails, retry once with the user's configured default model and a supported/default effort when its capabilities are known.
- Respect `autoroute.enabled` and `autoroute.mode` when a project or user config provides them. `manual` leaves the current Codex settings alone; `suggest` reports a recommendation; `auto` selects the model and effort and applies them through the routed launcher or a new `codex` process.
- Preserve an explicitly requested model or effort. Treat it as a user constraint and explain any incompatibility instead of silently overriding it; when capability metadata is unavailable, label the effort support as undiscovered rather than verified.
- Analyze six dimensions: complexity, scope, reasoning demand, risk, context size, and iteration horizon. Record concise evidence for each score.
- Select model and reasoning effort independently. Choose the closest supported effort when the catalog exposes support; otherwise preserve the explicit/default effort and mark support as undiscovered.
- With multiple models, prefer discovered capability metadata; otherwise use the conservative family defaults in `references/routing.md`. Select only models present in the discovered catalog.
- Use workload specialization when it is explicit: long-horizon work may prefer GPT-5.2, while complex coding/debugging remains on Sol; if the specialized model is unavailable, fall back to the nearest available tier.
- If signals show failed tests, repeated retries, cross-language changes, or a larger-than-expected diff, apply adaptive escalation and explain the trigger.
- When invoked inside an active Codex thread, do not stop at a recommendation. Present two explicit choices: (1) queue the recommended model/effort on the current thread and continue with its existing history, or (2) keep the current model and continue. Wait for the user's choice before switching. Use `scripts/autoroute.py --session` only after the user chooses option 1; it uses `CODEX_THREAD_ID` and `codex queue` rather than a separate session or terminal keystrokes.
- If discovery fails, fall back to the current configured model and a supported/default effort, and mark the recommendation as degraded.
- Keep AutoRoute state separate from `CODEX_HOME`: use the platform user cache by default, honor `AUTOROUTE_CACHE_DIR`/`cache_dir`, and fall back to a temporary cache if needed. Cache persistence must never abort routing.
- Treat live `codex exec` probes as optional capability checks. If app-server startup is blocked by a sandbox or permissions policy, retain the discovered inventory, report `probe_status=blocked`, and route using catalog/current-model metadata instead of declaring every model unavailable.

## Use the helper

When Codex itself can inspect the request or repository, first make a semantic 0–5 judgment for all six dimensions, then pass those values with `--scores`. Use prompt-only analysis as the fallback for standalone CLI use. Run `scripts/autoroute.py --help` for the complete interface. Typical calls:

```bash
python3 scripts/autoroute.py "Add a loading state to Button"
python3 scripts/autoroute.py --mode suggest --json "Debug intermittent React state desync"
python3 scripts/autoroute.py --scores '{"complexity":4,"scope":3,"reasoning":5,"risk":2,"context":3,"iteration":4}' "Investigate and fix the issue"
python3 scripts/autoroute.py --workload research "Compare these implementation options"
python3 scripts/autoroute.py --workload architecture "Design the repository boundary"
python3 scripts/autoroute.py --workload auto "Infer the workload from this task"
python3 scripts/autoroute.py --signals '{"test_failures":2,"changed_files":14}' "Refactor the sync layer"
python3 scripts/autoroute.py --mode auto --session "Continue the approved repository-wide migration"
~/.codex/skills/autoroute/scripts/codex-with-autoroute "Debug this sync issue"
```

用户实际使用时，直接在任务前加 `$autoroute` 即可，例如：`$autoroute 帮我修复这个跨模块缓存 bug`。Skill 先返回选中的模型、推理强度、任务等级、六维评分、workload 及 `workload_source`，然后给出两个选择：

1. **切换到推荐模型并继续**：在当前 Codex thread 排队下一轮，保留本会话历史和当前任务上下文。
2. **保持当前模型继续**：不执行切换，直接用当前模型继续。

必须等用户明确选择后再执行第 1 项。当前线程可用时，使用 `python3 scripts/autoroute.py --session ...` 或等价的 `codex queue --thread "$CODEX_THREAD_ID" --message ... -m <model> -c 'model_reasoning_effort="<effort>"'`。如果没有 thread id，说明无法在原线程排队，并提供保留上下文的 `codex resume <session-id> -m <model>` 备用命令；不要把它描述成已经切换成功。

`workload_source` 用于说明 workload 的来源：`cli`（命令行显式指定）、`config`（配置文件指定）、`inferred`（根据任务自动推断）或 `adaptive`（观察到失败/重试/范围扩大后升级）。

Use `references/configuration.md` for config precedence, `references/routing.md` for the scoring and fallback model, and `references/evaluation.md` when measuring impact. The bundled `rules/default.json` and `evals/cases.json` are baseline, editable project resources rather than claims about any particular provider's current catalog. Do not treat a keyword match as certainty: report the evidence and use the catalog's supported capabilities as the final constraint.

## Usage examples

本 Skill 用于根据当前任务自动选择模型和推理强度。安装并触发后，普通编码任务
也会进入自动路由；不需要用户再单独说“选择模型”或“切换模型”。在活动 Codex thread
中，切换采用排队下一轮的方式，thread 历史会继续作为上下文；用户始终可以选择不切换。

使用 `scripts/codex-with-autoroute` 启动 Codex 时，`auto` 模式在进程启动前应用
模型和推理强度。在已运行的 Codex 会话中触发本 Skill 时，先分析并展示“切换并继续”
与“保持当前模型”两个选择；只有用户选择切换后，才对当前 thread 排队下一轮。用户
配置为 `suggest` 或 `manual` 时仍不自动执行切换，但显式选择后可执行 `--session`。

### 示例一：触发 Skill 后自动路由

**用户：**“这个跨模块缓存迁移应该用什么模型？请自动选择后继续处理。”

先检查当前可用模型，按复杂度、范围、推理要求、风险、上下文和迭代周期六个维度
评分，得到 `model` 和 `effort`。回复中说明选中的模型、推理强度、任务等级和分数、
六个维度的依据，以及模型发现来源，并给出两个选择：切换到推荐模型并继续，或保持
当前模型继续。必须等用户明确选择后再执行切换。

### 示例二：用户要求在新会话执行

**用户：**“迁移方案已经批准，请自动选择模型，并在新的 Codex 会话中执行这项迁移。”

只有用户明确要求“新的 Codex 会话”时，才使用 `--run` 启动独立进程。普通 `$autoroute`
交互优先使用当前 thread 排队切换，不要擅自新建会话。

### 示例三：任务已经多次失败，需要升级

**用户：**“这个修复已经有两次测试失败，改动扩大到 14 个文件，请重新评估后继续。”

将 `test_failures=2` 和 `changed_files=14` 作为观察到的运行信号传给路由器。达到自适应
升级阈值时，提高推理强度或模型层级，并在回复中指出触发升级的具体信号。

### 示例四：配置为建议或手动模式

**用户：**“给这个仓库做一次模型路由评估，只输出建议，不要切换会话。”

使用 `suggest` 模式，先返回路由结果和两个选择，不自动切换；如果项目配置为 `manual`，
只提供信息性建议，并明确说明当前设置没有改变。

### 示例五：普通编码请求自动路由

**用户：**“给 Button 加一个 loading 状态。”

这也是普通实现请求，因此先使用 AutoRoute 分析模型与推理强度。如果路由结果就是
当前设置，直接继续实现；如果不同，展示“切换并继续”与“保持当前模型”选项，等待
用户选择。切换选项通过当前 thread 排队，不能确认排队成功前不要声称模型已改变。

## Response shape in conversation

直接为用户进行路由时，返回：选中的模型、推理强度、任务等级和分数、六个维度及其依据、
模型发现来源、当前模式的行为、生效边界，以及是否发生自适应升级。活动 thread 且推荐
不同于当前模型时，必须附上“切换并继续（保留上下文）”和“保持当前模型继续”两个选择，
等待明确选择后再执行。保持结果简洁，不要添加无关的角色设定、报告模板或额外内容层。
