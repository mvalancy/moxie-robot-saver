/* A small, dependency-free safety boundary for opt-in live probes.
 *
 * The counter increments immediately before the real fetch starts. That makes it a count
 * of outbound attempts, not successful responses, logical questions, or retry loops. A
 * single instance must be shared by every arm of a probe.
 */
import { setTimeout as scheduleTimeout, clearTimeout as cancelTimeout } from "node:timers";

export class ProbeBudgetError extends Error {
  constructor(message) {
    super(message);
    this.name = "ProbeBudgetError";
  }
}

export class ProbeBudget {
  constructor({ maxAttempts, timeoutMs }) {
    if (!Number.isSafeInteger(maxAttempts) || maxAttempts < 1)
      throw new TypeError("maxAttempts must be a positive integer");
    if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1)
      throw new TypeError("timeoutMs must be a positive integer");
    this.maxAttempts = maxAttempts;
    this.timeoutMs = timeoutMs;
    this.attempts = 0;
  }

  get remaining() { return this.maxAttempts - this.attempts; }

  /** Perform one HTTP attempt and consume its complete body inside the same deadline.
   * Redirects are returned to the caller: following one can emit another request (and
   * forward Authorization) beneath a single JavaScript fetch call. */
  async requestText(input, init = {}, fetchImpl = globalThis.fetch) {
    if (this.remaining <= 0)
      throw new ProbeBudgetError(`gateway attempt budget exhausted (${this.attempts}/${this.maxAttempts})`);

    this.attempts += 1;
    const attempt = this.attempts;
    const controller = new AbortController();
    // Use Node's timer directly: several browser harnesses replace global timers, and a
    // test double must not be able to disable the safety deadline by accident.
    const timer = scheduleTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const response = await fetchImpl(input, {
        ...init,
        redirect: "manual",
        signal: controller.signal,
      });
      const text = await response.text();
      return { response, text };
    } catch (err) {
      if (controller.signal.aborted)
        throw new ProbeBudgetError(`gateway attempt ${attempt} timed out after ${this.timeoutMs} ms`);
      throw err;
    } finally {
      cancelTimeout(timer);
    }
  }

  summary() { return `${this.attempts}/${this.maxAttempts}`; }
}
