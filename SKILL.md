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
- Codex does not expose a supported API for changing the model or reasoning effort of an already-running conversation; `/model` opens an interactive picker and does not accept inline arguments. If the current selection already matches the route, continue in the current session. If it differs, say that the active session cannot be hot-switched and give the exact routed command. Use `scripts/codex-with-autoroute` to apply the route automatically at process startup, or `--run` when the user explicitly asks for a separate Codex process.
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
python3 scripts/autoroute.py --signals '{"test_failures":2,"changed_files":14}' "Refactor the sync layer"
python3 scripts/autoroute.py --mode auto --run "Implement the approved repository-wide migration"
~/.agents/skills/autoroute/scripts/codex-with-autoroute "Debug this sync issue"
```

Use `references/configuration.md` for config precedence, `references/routing.md` for the scoring and fallback model, and `references/evaluation.md` when measuring impact. The bundled `rules/default.json` and `evals/cases.json` are baseline, editable project resources rather than claims about any particular provider's current catalog. Do not treat a keyword match as certainty: report the evidence and use the catalog's supported capabilities as the final constraint.

## Usage examples

本 Skill 用于根据当前任务自动选择模型和推理强度。安装并触发后，普通编码任务
也会进入自动路由；不需要用户再单独说“选择模型”或“切换模型”。官方 CLI 不支持
在已运行的会话内热切换模型，因此自动生效的边界是启动 routed Codex 进程。

使用 `scripts/codex-with-autoroute` 启动 Codex 时，`auto` 模式在进程启动前应用
模型和推理强度。在已运行的 Codex 会话中触发本 Skill 时，先分析；若路由结果与当前
设置不同，明确说明无法热切换，并给出可执行的 routed 启动命令。用户配置为
`suggest` 或 `manual` 时遵循配置，不改变启动设置。

### 示例一：触发 Skill 后自动路由

**用户：**“这个跨模块缓存迁移应该用什么模型？请自动选择后继续处理。”

先检查当前可用模型，按复杂度、范围、推理要求、风险、上下文和迭代周期六个维度
评分，得到 `model` 和 `effort`。若这是 routed 启动器启动的会话，报告其已生效；
若这是旧会话，报告无法热切换并提供 routed 启动命令。回复中说明选中的模型、
推理强度、任务等级和分数、六个维度的依据，以及模型发现来源。

### 示例二：用户要求在新会话执行

**用户：**“迁移方案已经批准，请自动选择模型，并在新的 Codex 会话中执行这项迁移。”

用户明确要求“新的 Codex 会话”时，使用 `--run` 启动独立进程。先报告选中的模型和
推理强度，再启动该进程；不要把路由结果描述成已经完成代码修改。

### 示例三：任务已经多次失败，需要升级

**用户：**“这个修复已经有两次测试失败，改动扩大到 14 个文件，请重新评估后继续。”

将 `test_failures=2` 和 `changed_files=14` 作为观察到的运行信号传给路由器。达到自适应
升级阈值时，提高推理强度或模型层级，并在回复中指出触发升级的具体信号。

### 示例四：配置为建议或手动模式

**用户：**“给这个仓库做一次模型路由评估，只输出建议，不要切换会话。”

使用 `suggest` 模式，只返回路由结果，不改变启动设置。如果项目配置为 `manual`，
只提供信息性建议，并明确说明当前 Codex 设置没有改变。

### 示例五：普通编码请求自动路由

**用户：**“给 Button 加一个 loading 状态。”

这也是普通实现请求，因此先使用 AutoRoute 分析模型与推理强度。如果路由结果就是
当前设置，直接继续实现；如果不同，说明当前会话无法热切换，给出 routed 启动命令
或询问是否用新进程执行，不要谎称当前会话设置已改变。

## Response shape in conversation

直接为用户进行路由时，返回：选中的模型、推理强度、任务等级和分数、六个维度及其依据、
模型发现来源、当前模式的行为、生效边界，以及是否发生自适应升级。保持结果简洁，不要
添加无关的角色设定、报告模板或额外内容层。`suggest` 模式只返回建议；`manual` 模式只
提供信息性建议，并明确说明当前设置没有改变。
