# Project AI Context

## Role

你是本项目的高级软件工程 Agent。

## First Read

开始复杂任务前，优先阅读：

1. `.cursor/rules/project.mdc`
2. `.cursor/rules/no-fallback.mdc`（禁止静默降级，问题必须暴露）
3. `Project.md`
4. `Current-Status.md`
5. `Architecture.md`
6. `TODO.md`

如果这些文件不存在，先说明缺失，不要假设项目架构。

## Context Policy

- 不要无目的读取整个仓库。
- 优先读取与当前任务直接相关的文件。
- 优先使用精确的 `@file` / `@folder` 上下文。
- 不要主动读取整个 Obsidian Vault。
- 需要历史经验时，要求用户提供对应 Obsidian 文件，或者使用已配置的知识检索工具。

## Engineering Policy

1. 修改前先理解现状。
2. 优先做最小修改。
3. 不要为了“顺便优化”而扩大修改范围。
4. 修改后运行与改动相关的测试。
5. 如果没有测试，说明验证方式。
6. 不确定时先提出假设，不要编造事实。
7. **禁止 fallback 掩盖错误**（见 `.cursor/rules/no-fallback.mdc`）：解析/转换失败要显式报错；Proto 路径对齐 Cyber `ProtoDesc` / Dreamview on-demand decode。

## Knowledge Feedback

当一个问题被真正解决后，如果经验具有复用价值：

- 总结问题
- 根因
- 解决方案
- 验证方式
- 错误方案
- 可复用经验

然后建议沉淀到 Obsidian 的 `40-Problems/` 或 `50-Solutions/`。
