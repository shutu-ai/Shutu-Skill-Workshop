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
    let inFlight = 0
    let dropped = 0
    let failed = 0
    const send = async (event: Record<string, unknown>) => {
      const sink = process.env.SHUTU_WORKSHOP_EVENT_SINK
      const token = process.env.SHUTU_WORKSHOP_EVENT_SINK_TOKEN
      if (!sink || !/^https?:\/\//i.test(sink) || !token) return
      if (inFlight >= 4) {
        dropped += 1
        return
      }
      inFlight += 1
      const controller = new AbortController()
      const timer = setTimeout(() => controller.abort(), 1500)
      try {
        const response = await fetch(sink, {
          method: "POST",
          headers: {
            "content-type": "application/json",
            authorization: `Bearer ${token}`,
          },
          body: JSON.stringify(event),
          signal: controller.signal,
        })
        if (!response.ok) failed += 1
      } catch {
        failed += 1
      } finally {
        clearTimeout(timer)
        inFlight -= 1
      }
    }
    const emit = (event: Record<string, unknown>) => {
      void send(event)
    }
    await send({
      type: "observer.ready",
      plugin_id: "shutu-workshop-observer",
      api_generation: "v2",
      host_version: ctx.app.version,
    })
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
    return () => {
      emit({
        type: "observer.stopped",
        plugin_id: "shutu-workshop-observer",
        dropped,
        failed,
      })
    }
  },
})
