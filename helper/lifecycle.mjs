// When the one helper process starts, stops and restarts (D5), as pure
// functions. Each takes the current state (and whether the process runs) and
// returns the next state plus the actions HelperProcess.qml applies to its
// Process and Timers. tests/lifecycle.test.mjs drives them under node.
//
// Actions: start, stop, scheduleStop {ms}, cancelStop, scheduleRestart {ms},
// cancelRestart. A timer step re-checks, when it fires, the condition that
// scheduled it (CODING_STANDARDS: shown state follows its source).

export const STOP_DEBOUNCE_MS = 1500
export const RESTART_MIN_MS = 1000
export const RESTART_MAX_MS = 30000

// refs counts registered widget copies; backoffMs is the next restart delay.
export function initial() {
  return { refs: 0, backoffMs: RESTART_MIN_MS }
}

function next(state, changes, actions) {
  return { state: Object.assign({}, state, changes), actions: actions }
}

// A widget copy appeared. A reload's 0 -> 1 lands inside the stop debounce,
// so cancelling the stop keeps the same process.
export function register(state, running) {
  const actions = [{ type: "cancelStop" }]
  if (!running) actions.push({ type: "start" })
  return next(state, { refs: state.refs + 1 }, actions)
}

// A widget copy went away. At zero, a restart still waiting out its backoff
// would outlive the last copy, so it is cancelled with the stop scheduled.
export function unregister(state) {
  const refs = Math.max(0, state.refs - 1)
  const actions = refs === 0
    ? [{ type: "cancelRestart" }, { type: "scheduleStop", ms: STOP_DEBOUNCE_MS }]
    : []
  return next(state, { refs: refs }, actions)
}

export function stopDue(state, running) {
  return next(state, {}, state.refs === 0 && running ? [{ type: "stop" }] : [])
}

// The process ended. With copies registered it died on its own: restart after
// the backoff, which doubles up to RESTART_MAX_MS and resets on hello.
export function processEnded(state) {
  if (state.refs === 0) return next(state, {}, [])
  return next(
    state,
    { backoffMs: Math.min(state.backoffMs * 2, RESTART_MAX_MS) },
    [{ type: "scheduleRestart", ms: state.backoffMs }])
}

export function restartDue(state, running) {
  return next(state, {}, state.refs > 0 && !running ? [{ type: "start" }] : [])
}

// The helper said hello: it started cleanly, so the next crash waits the minimum.
export function hello(state) {
  return next(state, { backoffMs: RESTART_MIN_MS }, [])
}
