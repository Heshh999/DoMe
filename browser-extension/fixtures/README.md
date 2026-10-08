# DOM fixtures

Hand-written, minimal YouTube-like structures used by `test/player-adapter.test.ts`. They contain only
the elements the adapter reads or clicks (`video.html5-main-video`, `.html5-video-player`, the
`.ytp-*` buttons, `ytd-watch-flexy[theater]`, the title heading). They are not copies of youtube.com
and carry no scripts. If YouTube changes its DOM, update the selectors in `src/content/detect.ts`
and these fixtures together; the manual checklist in `../README.md` is the real-browser check.

| File | Scenario | URL used by the tests |
| --- | --- | --- |
| `watch-playing.html` | Normal watch page, video playing, next available, no playlist; carries the always-present `.ytp-live-badge` hidden by the same CSS rule YouTube uses (`is_live` must stay false) | `/watch?v=dQw4w9WgXcQ` |
| `ad-showing.html` | Watch page while an advertisement plays (`.ad-showing`) | `/watch?v=dQw4w9WgXcQ` |
| `live.html` | Live stream (`.ytp-time-display.ytp-live` with the badge shown, infinite duration) | `/watch?v=5qap5aO4i9A` |
| `shorts.html` | Shorts player (`#shorts-player`, no next/previous buttons) | `/shorts/aBcDeFgHiJk` |
| `no-next.html` | Last video of a playlist: next disabled, previous available | `/watch?v=kJQP7kiw5Fk&list=PL123` |
| `home-no-player.html` | YouTube page without a player (content script attached, nothing to control) | `/` |
| `tabs.json` | Tab descriptions for the background tests, including a tab whose content script is not attached | n/a |
