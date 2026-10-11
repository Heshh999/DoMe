# DOM fixtures

Hand-written, minimal YouTube-like structures used by `test/player-adapter.test.ts` (and
`test/content-entry.test.ts`). They contain only the elements the adapter reads or clicks
(`video.html5-main-video`, `.html5-video-player`, the `.ytp-*` buttons, `ytd-watch-flexy[theater]`,
the title heading; on results pages the result renderers, their links, titles, channel names and
thumbnail badges). They are not copies of youtube.com and carry no scripts. If YouTube changes its
DOM, update the selectors in `src/content/detect.ts` / `src/content/results.ts` and these fixtures
together; the manual checklist in `../README.md` is the real-browser check. The search fixtures were
generated once from a small script (one renderer template per layout) so their rows stay consistent;
edit them by hand like the others.

| File | Scenario | URL used by the tests |
| --- | --- | --- |
| `watch-playing.html` | Normal watch page, video playing, next available, no playlist; carries the always-present `.ytp-live-badge` hidden by the same CSS rule YouTube uses (`is_live` must stay false) | `/watch?v=dQw4w9WgXcQ` |
| `ad-showing.html` | Watch page while an advertisement plays (`.ad-showing`) | `/watch?v=dQw4w9WgXcQ` |
| `live.html` | Live stream (`.ytp-time-display.ytp-live` with the badge shown, infinite duration) | `/watch?v=5qap5aO4i9A` |
| `shorts.html` | Shorts player (`#shorts-player`, no next/previous buttons) | `/shorts/aBcDeFgHiJk` |
| `no-next.html` | Last video of a playlist: next disabled, previous available | `/watch?v=kJQP7kiw5Fk&list=PL123` |
| `home-no-player.html` | YouTube page without a player (content script attached, nothing to control) | `/` |
| `search-results.html` | Classic results page (`ytd-search` → `ytd-item-section-renderer #contents` → `ytd-video-renderer`): a channel row, a live video, a 1:02:03 video, a "People also watched" shelf, a playlist, a mix, a duplicate row, a 200+ character title with a zero-width character, and 11 distinct videos across two sections (the 11th is beyond the 10-result cap) | `/results?search_query=lofi+beats` |
| `search-results-lockup.html` | Newer view-model results (`yt-lockup-view-model`, class names with and without the `-wiz` infix, `badge-shape` duration badges, channel link in the metadata row), a mix lockup (`list=` link) and a Shorts grid shelf | `/results?search_query=lofi+beats` |
| `search-results-ads-shorts.html` | An in-feed ad slot and a promoted video (both with watch links), a Shorts shelf and a Short shown as a `ytd-video-renderer` row (`/shorts/` link); only two rows are results | `/results?search_query=lofi+beats` |
| `search-no-results.html` | YouTube's "No results found" panel (`ytd-background-promo-renderer`): an empty success | `/results?search_query=qzxqzxqzx` |
| `tabs.json` | Tab descriptions for the background tests, including a tab whose content script is not attached | n/a |
