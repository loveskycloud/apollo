# Replay remains on the empty layout / log_time

## Reproduced failure

The screenshot pattern is a populated Layers catalog, no pose or debug data,
and an implicit `log_time` in the media bar. This is the empty layout recording,
not an additional clock in the converted bag.

The browser must receive the imported messages over the proxy stream separately
from the HTTP conversion/catalog/window requests. A successful HTTP window
response is not evidence of data arrival in the browser.

Two concrete failure paths were reproduced:

1. `?url=` or whitespace-only URL parameters disabled the default proxy
   connection. HTTP import still succeeded, but the active recording remained
   `ad_layout_workspace` with no playhead. Evidence:
   `test-artifacts/replay-stall-20260913/empty-url-before/`.
2. After disconnecting a previously used proxy, source deduplication treated
   cached recordings as an active subscription. Loading that source selected
   an old recording without reconnecting. Old pose data could even advance
   while the new request's receipt never arrived.

The user's full browser URL was not supplied; reproducing these paths establishes
the defects, not proof that their page used a particular URL parameter.

## Changes

- Normalize empty URL parameters before selecting the startup source.
- Local replay checks the configured proxy subscription and reconnects when
  absent. Live proxy deduplication uses active receivers only, not cached stores.
- Find a request's unique stream receipt across received recordings and bind the
  playback session to its owning recording. Reject responses for another MCAP.
- Do not initialize the new bag from the previous recording's clocks or let a
  persisted workspace/foreign recording take over its route.
- Initial `SetTime` / `Pause` commands must actually take effect before prefetch
  can interpret play state. The controller's initial Following state must not
  arm buffer auto-resume. Layout restoration must not override this initial seek.
- Show a visible connection error and a reconnect/retry button, and expose
  read-only `get_playback_state()` diagnostics. Empty workspaces display
  `Waiting for data`, not a usable `log_time` selector.

## Acceptance conditions

`scripts/test_replay_lifecycle_browser.py` uses real mouse clicks for Sim Replay
and the first Play action. It does not set the playhead through an internal API.
The optional fault injection blocks proxy requests, clicks the visible Retry
button after restoring the network, and deliberately removes the subscription
before opening the second run.

Each ready state must have an active receiver, no error/pending receipt, an
acknowledged recording equal to the active recording, and a paused first pose.
Each playback sample must advance the cursor and pose timestamp; pose sample age
must remain below 100 ms. The sequence covers run-1 → run-2 → run-1 → reload/reopen.

The earlier `connection-recovery/` output was invalidated because its old
assertions accepted cached data with zero receivers. Its raw evidence is retained
with `INVALIDATED.md`; it must not be cited as a passing recovery test.

`--staging-assets` is explicitly a predeployment check. Deployed acceptance must
omit that flag so all HTML, JS and Wasm come from the actual 9090 service.

No original bags, simulation results or conversion caches need deletion for
this connection fix. Existing tabs must reload to run the updated viewer.

## Deployed verification — 2026-09-13

`test-artifacts/replay-stall-20260913/deployed-recovery/` passed without
`--staging-assets`, against the rebuilt 9090 / 9876 service in
`apollo_neo_dev_wangsheng`:

- Task `efcce7f9cf4c49c1`, empty `url` parameter, real Sim and Play button clicks.
- Injected network failure produced an explicit connection error; clicking
  Reconnect after restoring the network established a real subscription.
- Removing the subscription before run-2 was followed by a newly connected
  receiver and a newly acknowledged recording, not a cached-data false pass.
- Four opens were paused at absolute 1,000,000,000 ns with pose at the same time.
  Each had no pending receipt/error and an acknowledged ID equal to its active ID.
- All 16 playback samples advanced both the playhead and pose timestamp; the
  final samples reached approximately 9.9–10.0 seconds. Refresh/reopen passed.
- No Wasm panic/page errors occurred. Network errors in this artifact are the
  deliberately injected disconnect and are retained, not hidden.

Five production-HTML URL selection tests, 29 Python regression tests, native
and Wasm Clippy, and the complete viewer build passed. Clippy retains existing
warnings; this is not a full-workspace Rust test run.

The more recently opened task `d897596d6b454fe1` also passed the strengthened
clock/browser test on the deployed service, using the same empty-URL entry.
Evidence: `test-artifacts/replay-stall-20260913/deployed-latest-clock/`.
Both clocks passed paused seeks at absolute 2, 30, 58, 6 and 3 seconds;
all ten pose timestamps equal the requested timestamps. Both Play-button checks
advanced, all five debug panels and the chassis HUD were populated, all 70
queries used only the two product clocks, and no page/console errors occurred.
