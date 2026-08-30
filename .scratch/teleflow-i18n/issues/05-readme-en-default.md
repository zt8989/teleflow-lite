# 05: 英文 README 作为默认，中文引用

**What to build:** 把项目说明改为「英文为默认、中文可引用」的双语文档结构。交付：

- 将当前中文 `README.md` 的内容**移植为英文**，作为新的默认 `README.md`（结构、目录表、命令、音频路由/IVR/汇报说明等全部英文；代码块/命令保持原样）。
- 中文版移至 `README.zh-CN.md`，顶部加一行指向英文默认文档的链接。
- 英文 `README.md` 顶部加一行指向 `README.zh-CN.md`（中文）的链接。
- 如 `AGENTS.md`/其他文档引用了 `README.md` 语言，顺带注明现以英文为默认。

**Blocked by:** None (can start immediately) — 文档任务，可与代码票并行。

**Status:** ready-for-agent

- [ ] 新的 `README.md` 为英文，内容覆盖原中文 README 的全部章节。
- [ ] `README.zh-CN.md` 含原中文内容，顶部链接到英文默认 README。
- [ ] 英文 `README.md` 顶部链接到 `README.zh-CN.md`。
- [ ] 无残留指代「中文 README 为默认」的过时说明。
