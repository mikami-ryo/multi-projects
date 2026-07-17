# Tournament Control extension boundary

The application keeps its current behavior while exposing stable boundaries for
future scoreboard themes and Auto Monitor actions.

## Editable scoreboard workspace

On first launch, bundled scoreboard files are copied to:

```text
%APPDATA%\TournamentControl\scoreboard
```

The local HTTP server serves this directory. Existing files are never
overwritten during startup or application updates, so HTML, CSS, JavaScript,
images, and fonts can be customized without rebuilding the executable.

## Events

Detectors publish `AppEvent` values through `EventBus`. Current Auto Monitor
events include:

- `detector.started`
- `detector.error`
- `detector.stopped`
- `screen.game_set`
- `score.detected`

Publishing an event does not itself change the scoreboard or score data.

## Presentation state and rules

The default `%APPDATA%\TournamentControl\automation_rules.json` contains no
rules, so current behavior is unchanged. Rules can later map detector events to
presentation actions without importing detector code into the scoreboard.

```json
{
  "version": 1,
  "rules": [
    {
      "enabled": true,
      "event": "score.detected",
      "payload": { "winner": "left" },
      "action": { "type": "set_theme", "theme": "results" }
    }
  ]
}
```

Presentation state is available to browser sources at:

```text
http://127.0.0.1:8765/scoreboard-state.json
```

The state supports `automatic` and `manual` modes. In manual mode, event rules
cannot change `activeTheme`. The existing scoreboard does not yet consume theme
state; this endpoint is the compatibility layer for a future theme loader.
