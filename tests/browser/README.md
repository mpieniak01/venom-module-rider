# Automated Rider regressions

`make check` runs Python checks, frontend checks **and real browser regressions**.
The browser functions are executed, rather than only checked for JavaScript syntax.
No running Venom installation, model, credentials, SSH connection or physical Pi
is needed. Do not point this gate at an operator panel.

The gate builds the existing inert React fixtures, starts a temporary static
server on an automatically allocated loopback port and creates a fresh browser
context for every case. Requests to APIs and other origins are blocked unless the
scenario explicitly fulfils them from memory. WebSockets and direct HTTP from the
scenario are blocked too. An initial negative test verifies that these protections
are active. The static server has no backend or proxy.

## Running

Use the configured host frontend dependencies and its existing Playwright browser:

```sh
make check
make check-browser
make check-browser BROWSER_ARGS='--only video-comparison'
```

`HOST` selects the companion Venom checkout, as for the other Make targets. For a
standalone invocation, `RIDER_WEB_ROOT` selects its `web-next` directory. If the
Playwright default Chromium is unavailable, explicitly select an existing
compatible executable with `RIDER_BROWSER_EXECUTABLE`. The gate never downloads a
browser and never silently skips cases when a dependency is missing.

```sh
RIDER_BROWSER_EXECUTABLE=/path/to/chromium make check
```

The runner is `scripts/check_browser_regressions.mjs`. It uses the frontend's
existing `esbuild` and `playwright` dependencies. It is project test automation,
independent of interactive Codex browser tools.

## Protected behaviour

- The known-room preset remains an explicit choice. Operator changes, validation,
  stale configuration, reset and PL/EN/DE labels retain their behaviour. Its JSON
  fixture is checked against the real server catalog by `test_room_profile.py`.
- Calibration retains the measured defaults and overlay. Unavailable state cannot
  be written, retries retain the device revision and opening a form never saves it.
- Sessions remain separate after recording, navigation between tabs and reload.
  A photo keeps its source identity; downloads and ZIP export retain the selected
  session. The current-session tools remain below the archive preview.
- RAW and saved classifications share one player and one position. Controls stay
  stable during slow JPEG or mask loading. Speeds ×1/×2/×5 can change during
  playback without seeking, pausing or desynchronizing the pair. A controlled
  browser clock verifies the actual frame deadlines at every speed. A rejected frame has no borrowed mask;
  playback continues afterwards. Stale study, hash and dimensions are rejected.
- Previews precede controls and configuration. Recording information and analysis
  actions stay in compact adjacent boxes. Loading/help messages do not return.
  Historical photos and narrow screens remain supported.
- Journey fixtures check preparation before a fresh ARM intent, explicit approval,
  writable configuration, STOP during pending operations and manual takeover.
  The matrix includes the known-room preset with DA2, SegFormer and YOLO identities.
  These are in-memory contracts and do not actuate anything.

The Python suite also covers source-video persistence, index integrity, whole-film
analysis, quota accounting, STOP boundaries and source/policy/model invalidation.
Optional cross-repository tests require the verified pure-source Pi export named
by `RIDER_TEST_PI_ROOT`; they do not import device transports or hardware services.
The companion Pi repository maintains its own navigation/lease/journey tests.

Results are written to ignored `test-results/browser-regressions/report.json`,
including the source revision, dirty state and browser versions. Failures retain a
screenshot and Playwright trace in the same directory. A failed case or uncaught
browser exception returns a nonzero exit status. New fixes should add a regression in the layer where the failure occurred.
