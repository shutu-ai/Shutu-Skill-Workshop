/**
 * Project-local OpenCode V2 observation bridge.
 *
 * It intentionally only emits small event metadata. Loading this file is
 * explicit and the host owns the actual output sink; it does not change input,
 * output, permissions, prompt, or tool results.
 */
import { Plugin } from "@opencode/plugin"

export default Plugin.define({
  id: "shutu-workshop-observer",
  async setup(ctx) {
    const emit = (event: Record<string, unknown>) => {
      const sink = process.env.SHUTU_WORKSHOP_EVENT_SINK
      if (!sink) return
      // The host-specific bridge owns transport and redaction. This plugin only
      // sends a bounded metadata record to an explicitly configured sink.
      void fetch(sink, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(event),
      }).catch(() => undefined)
    }
    await ctx.tool.hook("execute.before", (event) => {
      emit({
        type: "tool.before",
        sessionID: event.sessionID,
        callID: event.callID,
        tool: event.tool,
      })
    })
    await ctx.tool.hook("execute.after", (event) => {
      emit({
        type: "tool.after",
        sessionID: event.sessionID,
        callID: event.callID,
        tool: event.tool,
        status: event.status,
      })
    })
  },
})
