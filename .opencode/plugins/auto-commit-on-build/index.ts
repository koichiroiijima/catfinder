import { Plugin } from "@opencode/plugin"

const BUILD_AGENT = "build"
const AUTO_COMMIT_AGENT = "auto-committer"
const AUTO_COMMIT_PROMPT = `未コミットの変更を確認し、commit スキルの規約に従ってコミットしてください。
変更がなければ何もせず、その旨を報告してください。`

type StatusResult = { data?: unknown } | unknown[]

function statusFiles(result: StatusResult): unknown[] {
  if (Array.isArray(result)) {
    return result
  }
  const data = (result as { data?: unknown }).data
  return Array.isArray(data) ? data : []
}

export default Plugin.define({
  id: "catfinder.auto-commit-on-build",
  setup(ctx) {
    const controller = new AbortController()
    let queue: Promise<void> = Promise.resolve()

    const runAutoCommit = async (sessionID: string) => {
      const child = await ctx.session.create({
        parentID: sessionID,
        agent: AUTO_COMMIT_AGENT,
        title: "auto-committer",
      })
      await ctx.session.prompt({
        sessionID: child.id,
        text: AUTO_COMMIT_PROMPT,
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

          const status = await ctx.vcs.status()
          if (statusFiles(status as StatusResult).length === 0) {
            continue
          }

          // Serialize commits so two idle sessions cannot race in Git.
          queue = queue
            .then(() => runAutoCommit(sessionID))
            .catch((error) => {
              console.error("[auto-commit-on-build] auto-committer failed", error)
            })
        } catch (error) {
          if (controller.signal.aborted) {
            return
          }
          console.error("[auto-commit-on-build] failed to handle session.idle", error)
        }
      }
    }

    void consume()
    return () => controller.abort()
  },
})
