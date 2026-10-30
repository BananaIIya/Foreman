# M0 决策与验收

日期：2026-10-12。结论：继续开发。现有订阅已经能通过 T3 Code 执行文件修改，M1 的 worker 配置切换也已完成真实验证。

## 锁定版本

- 上游：[pingdotgg/t3code](https://github.com/pingdotgg/t3code)。
- 基线 commit：`58d253231d83f669a2cdfefa5fe479751d22c582`。
- Fork：[BananaIIya/t3code](https://github.com/BananaIIya/t3code)，开发分支：`feat/m0-m1-workers`。
- 从源码运行，依赖按上游 `pnpm-lock.yaml` 安装。已安装的 T3 Code Alpha 0.0.42 保持原状。
- 开发数据独立放在本仓库的 `.t3`；未修改本机 `~/.t3` 数据库、设置或 secrets。

## 实际验收

通过本地源码服务器的线程命令调用已有 Codex、Claude provider。每次使用独立线程，将 git 仓库中的 `status.txt` 从 `PENDING\n` 改为 `DONE\n`。只有运行完成、内容正确、且文件差异仅包含 `status.txt` 才记为成功。

| Worker      | 模型               | 成功次数 | 是否达到至少 4/5 |
| ----------- | ------------------ | -------- | ---------------- |
| Codex       | `gpt-6-luna`       | 5/5      | 是               |
| Claude Code | `claude-haiku-5-5` | 5/5      | 是               |

十次运行的完成状态、耗时、文件内容 SHA-256 和持久化 worker 配置见 [原始结果](evidence/m0-worker-results.json)。之后又在同一线程、同一工作目录中，先切换至 Haiku、再切回 Luna，执行同一条文件修改指令，两次均成功；见 [M1 切换结果](evidence/m1-worker-switch-results.json)。

前端已打开，使用“New thread”创建线程，选择 Claude Haiku 5.5，发送只要求回复的测试消息。线程持久化后返回 `UI_M0_OK`，刷新后仍可打开。

用户已确认 Nebius 额度领取完成。本次执行未发起 Nebius 推理，也未写入真实 Nebius API key；账户到账情况采用用户确认，未独立读取账单或余额。Nebius 默认禁用，留待后续阶段使用。

## 预装工具

工具位于工作目录旁的 `.tools`，未提交到 fork，也未改动系统全局 PATH。

| 工具           | 版本    | 本地路径                               |
| -------------- | ------- | -------------------------------------- |
| Node.js        | 24.21.0 | `../.tools/node_modules/.bin/node`     |
| pnpm           | 11.10.0 | `../.tools/node_modules/.bin/pnpm`     |
| OpenCode       | 1.19.0  | `../.tools/node_modules/.bin/opencode` |
| mini-swe-agent | 2.4.6   | `../.tools/mini-swe-agent/bin/mini`    |

OpenCode 版本检查和 mini-swe-agent 的帮助命令通过，均未调用模型。mini-swe-agent 使用 `MSWEA_GLOBAL_CONFIG_DIR` 指向 `../.tools/mini-swe-agent-config`，避免首次启动写入系统配置目录。

## 踩到的坑

1. 本机已有的是打包版 T3 Code，完成本次源码开发仍需单独 clone。上游要求 Node 24；本地工具目录解决版本差异。
2. Codex CLI 在普通 shell 中可找到，但持久终端 PATH 不同。隔离开发设置显式指定本机 Codex 和 Claude 的 binary path，复用既有登录。
3. 当前 T3 有 provider instance registry。`worker.provider` 使用实例 ID，而不是共享的驱动名称，以保留不同账号和配置的隔离。
4. 开发模式的 Node 文件监听会被类型检查触发连续重启。真实模型验收采用不带监听的源码服务；前端仍通过 Vite 单一来源代理访问服务器。
5. 浏览器初次访问需要配对；临时配对凭据和验收访问令牌只保存在隔离开发状态中，不进入报告或 Git。
6. 全量 `serverSettings.test.ts` 在本机沙箱中有三项符号链接文件监听测试超时；在沙箱外单独重跑这三项后全部通过。新增 Nebius secret 测试及现有 secret 生命周期测试也通过，未修改监听实现。

## 继续条件

M0 的 worker 可行性成立，M1 已为后续监测层提供明确的 worker.provider/model。Nebius 的模型目录是静态配置，并以无网络的测试验证开关；没有把目录可见当作真实账户推理成功。后续阶段需使用账户 API key 验证具体模型的实际可用性，再开始 Nebius 推理测试。

## 代码验证

相关定向测试共覆盖 240 个测试用例，均通过，包括共享设置默认值、OpenCode 适配器回归、worker 持久化与切换、Nebius 禁用/缺少密钥时拒绝推理、secret 隔离和脱敏，以及沙箱外的文件监听复核。contracts、provider-opencode、server、web、mobile 五个包的类型检查通过。改动文件的格式检查、lint 和 Git 空白检查通过；lint 剩余提示来自上游未使用的声明等已有代码。

凭据检查未发现本次改动包含真实 key 或临时验收令牌。上游的脱敏单元测试及 vendored 测试包含模拟 key 字符串，不将这些 fixture 误算为真实凭据，也未为消除匹配而改动它们。
