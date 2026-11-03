import { Plugin } from "@opencode/plugin"

const BUILD_AGENT = "build"
const DOCS_AGENT = "docs-updater"
const DOCS_PROMPT = `Build セッションの作業が完了しました。
呼び出された時点の実装変更に合わせて、README.md と docs/ 配下などの
プロジェクトドキュメントを最新化してください。

- 必ず project-docs スキルを読み込み、モード B（実装変更への追従）に従う。
- 変更が既にコミットされている場合は、直近のコミット
  （git log -1 --stat / git show --stat HEAD）を確認して対象を判断する。
- ドキュメントのみを編集し、実装コードは変更しない。
- コミットはしない（auto-committer が担当する）。
- 変更がなく、ドキュメントが既に最新であれば何も編集せず、その旨を報告する。`

export default Plugin.define({
  id: "catfinder.auto-docs-on-build",
  setup(ctx) {
    const controller = new AbortController()
    let queue: Promise<void> = Promise.resolve()

    const runDocsUpdate = async (sessionID: string) => {
      const child = await ctx.session.create({
        parentID: sessionID,
        agent: DOCS_AGENT,
        title: "docs-updater",
      })
      await ctx.session.prompt({
        sessionID: child.id,
        text: DOCS_PROMPT,
      })
      await ctx.session.wait({ sessionID: child.id })
    }

    const consume = async () => {
      for await (const event of ctx.event.subscribe({
        types: ["session.idle"],
        signal: controller.signal,
      })) {
        const sessionID = event.data.sessionID
        try {
          const session = await ctx.session.get({ sessionID })
          if (session.agent !== BUILD_AGENT) {
            continue
          }
          // Skip sessions that a build/other agent spawned for a subtask.
          if (session.parentID) {
            continue
          }

          // Run after every top-level Build turn. The docs-updater agent
          // decides whether there is anything to update.
          queue = queue
            .then(() => runDocsUpdate(sessionID))
            .catch((error) => {
              console.error("[auto-docs-on-build] docs-updater failed", error)
            })
        } catch (error) {
          if (controller.signal.aborted) {
            return
          }
          console.error("[auto-docs-on-build] failed to handle session.idle", error)
        }
      }
    }

    void consume()
    return () => controller.abort()
  },
})
