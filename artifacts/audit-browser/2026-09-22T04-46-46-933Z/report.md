# Browser remediation audit

- Base: http://127.0.0.1:9019
- Started: 2026-09-22T04:46:46.936Z
- Finished: 2026-09-22T04:47:10.757Z
- Checks: 21/21 passed
- Failures: 0
- Console errors: 0

- [x] health identity available
- [x] chat reaches active request
- [x] reset remains enabled during chat
- [x] reset API responded
- [x] reset advances generation and releases active work
- [x] reset cancellation is visible
- [x] post-reset chat returns real business answer
- [x] UI enters muted mode
- [x] muted UI injection sends condition=muted
- [x] muted run persists muted condition
- [x] muted run drops dialog and receives none
- [x] memory and muted are both enabled
- [x] UI blocks memory+muted without POST
- [x] backend rejects memory+muted with 422
- [x] rejected memory+muted does not create run
- [x] current scoreboard identifies selected/current version
- [x] current scoreboard exposes retained unknown usage
- [x] current version keeps unmeasured cells empty
- [x] legacy version selection requests legacy data
- [x] legacy unknown usage agrees with real records
- [x] scoreboard polls while tab is open
