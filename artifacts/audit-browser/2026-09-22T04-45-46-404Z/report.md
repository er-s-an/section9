# Browser remediation audit

- Base: http://127.0.0.1:9019
- Started: 2026-09-22T04:45:46.406Z
- Finished: 2026-09-22T04:46:08.904Z
- Checks: 17/21 passed
- Failures: 4
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
- [ ] memory and muted are both enabled
- [ ] UI blocks memory+muted without POST
- [ ] backend rejects memory+muted with 422
- [ ] rejected memory+muted does not create run
- [x] current scoreboard identifies selected/current version
- [x] current scoreboard exposes retained unknown usage
- [x] current version keeps unmeasured cells empty
- [x] legacy version selection requests legacy data
- [x] legacy unknown usage agrees with real records
- [x] scoreboard polls while tab is open
