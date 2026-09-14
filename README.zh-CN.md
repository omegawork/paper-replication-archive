# Paper Replication Archive 中文指南

[English](README.md) · [版本下载](https://github.com/omegawork/paper-replication-archive/releases) · [v5 审查记录](docs/v5-audit.md)

这是用于精读论文与科学复现的通用 Agent Skill。它把论文主张、执行计划、原始结果、定量对照和真实独立审查连接起来，同时让既有授权范围内的修复与续跑自动继续。

## 安装

需要 Python 3.10 或更新版本，Skill 自带工具只使用标准库。论文项目本身的依赖另行处理。

```text
git clone --branch v5.0.0 https://github.com/omegawork/paper-replication-archive.git
cd paper-replication-archive
python scripts/install_skill.py --action update --source . --target <你的Agent技能目录>/paper-replication-archive
python scripts/install_skill.py --action check --source . --target <你的Agent技能目录>/paper-replication-archive
```

把占位路径替换为实际目录。也可从 Release 下载 ZIP，校验 SHA-256 后安装。通用入口位于 `skills/paper-replication-archive/SKILL.md`，必须保留其整个目录及相对路径资源。省略 `--target` 时安装器使用 Codex 技能目录。

安装器会保护现有本地修改：先备份、比较并合并，再安装到新目录；不会强行覆盖未知文件。安装文件验证与宿主热加载是两件事，更新后按所用 Agent 的方式重新加载技能。

## 使用

```text
使用 $paper-replication-archive 精读文献 <论文路径或链接>
使用 $paper-replication-archive 复现文献 <论文路径或链接>
使用 $paper-replication-archive 复现文献 <论文路径或链接> 中 Fig.3
```

可一并说明固定科学约定、执行主机、资源预算与目标。默认继承当前任务已有授权，记录指令来源；每次运行另外绑定当前代码、输入和独立审查。隔离环境依赖修复、纠错、预算内重试、续跑、绘图修复和负面结果整理不需要重新批准整批目标。明确要求“仅预览”或“逐次审批”时仍严格遵守。

只有改变目标、科学约定、执行范围或增加预算等实质变化才需要用户决定。代理应先诊断并完成可独立推进的工作，再提出一个具体问题。新哈希本身不是重新授权的理由。

## 完整流程与兼容范围

完整流程保留 Stage0 → StageA 精读 → StageB 策略 → StageC 逐目标串行执行 → StageCSummary → StageD 交付。生产者与 Critic 必须是真实、独立的原生子代理，登记实际平台返回的身份；不能用主代理自写的“审查报告”冒充独立审查。

任何能加载 Agent Skills 目录的宿主都可读取入口。具备原生独立子代理、Python 和文件执行工具的环境可以适配完整流程；缺少子代理的平台可用于阅读辅助、准备、只读检查与导出，不能宣称完成正式独立审查。已实际验证的宿主范围见审查记录，不能把“文件可安装”说成“任何 Agent 都已端到端验证”。

`agents/openai.yaml` 只是可选 Codex 界面配置。通用协议不要求调用某个平台特有的 API。报告支持 `init-case --language zh-CN` 和 `--language en`，不再用中文字符占比作为阻断条件。

## 科学结果与工程状态

- 计算完成、展示质量、比较结论、科学验收分别报告。
- 正式科学尝试每个目标最多四次；环境、传输、格式与展示修复另行计数，并受累计预算约束。
- 运行开始就保存标识与状态；中断恢复先确认已有进程和调度状态，不覆盖残留目录、不重复启动计算。
- 图例或排版修复复用已验证数据，不消耗新的科学尝试；不能据此升级科学结论。
- 内部恒等式验证不计入论文结果复现成功率。
- 负面终结记录只能支持负面结论；档案完整不能替代用户要求的科学复现。

v4 历史档案只读支持状态、审计、证据验证和原样导出。v5 不自动改写旧授权、事件或科研结果。

## 开发与许可

```text
python -B -m unittest discover -s tests -v
python skills/paper-replication-archive/scripts/lint_skill.py
python scripts/package_release.py --output dist
```

CI 覆盖 Windows、Linux、macOS 与 Python 3.10/3.14。合成测试验证软件行为，不代表某篇真实论文已复现。[合成论文](examples/synthetic-paper.md) 可用于宿主适配验收。

执行仍使用宿主的权限控制；复制源码目录不构成安全沙箱。哈希证明内容完整性，不能认证用户或代理身份。请勿在问题反馈中上传私密会话、凭据或未获授权的研究材料。项目使用 [MIT 许可证](LICENSE)，贡献说明见 [CONTRIBUTING.md](CONTRIBUTING.md)。
