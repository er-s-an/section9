# Section9 UI system

- **Product:** Section9 local operations and evidence console
- **Surface:** Responsive desktop-first web application
- **Audience:** Operators reviewing an AI-assisted support workflow
- **Principle:** Show the current state and next safe action first; keep technical detail one deliberate step away.

## Visual direction

- Use a quiet neutral workspace, white content surfaces, and a restrained Section9 rust accent. Reserve green, amber, and red for actual status semantics.
- Prefer native system sans-serif typography and the existing Phosphor icon set. Do not load fonts or assets from a CDN.
- Keep the pixel office as an optional live visualization, not the default focal point.
- Avoid decorative gradients, glass effects, dense bento layouts, novelty animation, and equal-weight cards for unequal tasks.

## Tokens

| Role | Value |
|---|---|
| Page background | `#F4F6F5` |
| Surface | `#FFFFFF` |
| Primary text | `#17231F` |
| Secondary text | `#53635D` |
| Border | `#DCE4E0` |
| Section9 accent | `#A6532A` |
| Accent surface | `#F8EEE8` |
| Success | `#287653` |
| Warning | `#875000` |
| Failure | `#AD382E` |
| Focus ring | `#145E52` |

Use a 4/8px spacing rhythm, 8–12px card radius, subtle borders, and one restrained shadow level for overlays. Keep body text at 14–16px and secondary text at 12–14px; never encode a state by color alone.

## Information architecture

1. **Overview:** current service/incident state, the workflow stages, and the next safe operator action. Scenario rehearsal, chat, and the live office are secondary or explicitly labeled.
2. **Run history:** searchable/scannable records; selecting one opens a focused detail view with a clear close/back action. The URL and browser history represent the selected record.
3. **Playbooks:** readable summaries with a real detail view; provenance and reuse counts remain factual and local-only status is explicit.
4. **Acceptance:** sample/batch first, scenario and treatment comparisons next, experiment controls below the results.
5. **Product workspace:** separate overview, connections, issues/incidents, execution, and governance/collaboration. Keep permission gates and failure states visible; destructive/stop actions are visually separated.

Use progressive disclosure for raw event payloads, technical details, dependency status, and team internals. Keep all primary destinations discoverable in a persistent navigation region. Changing destinations updates the URL; browser Back returns to the prior view or closes the current detail.

## Interaction and accessibility

- Use links for navigation and buttons for actions. Clickable rows must be keyboard operable and announce their destination/action.
- Provide visible focus, accessible names, a logical heading tree, dialog labels, Escape-to-close, and focus restoration for overlays.
- Show loading, empty, error, permission-denied, and unknown states without implying success.
- Prevent duplicate submissions while an action is pending. Preserve real API response and approval semantics.
- Respect `prefers-reduced-motion`; do not make state comprehension depend on animation.

## Responsive behavior

- Verify at 375, 768, 1024, and 1440 CSS pixels.
- No page-level horizontal overflow. Collapse multi-column panels into one column; allow only intrinsically wide comparison tables to scroll within their own labeled region.
- Keep tap targets at least 44px high and maintain legible labels when navigation wraps or scrolls.

## Acceptance checklist

- [ ] The current state and the next relevant action have clear priority.
- [ ] Every apparent navigation/action affordance works and has a visible result.
- [ ] Details have a clear return path and URL/history behavior.
- [ ] Unknown, denied, failed, loading, and empty conditions remain distinct.
- [ ] Keyboard, focus, Escape, reduced motion, and narrow-screen behavior are checked in a real browser.
- [ ] Existing product permissions, evidence provenance, and business truth are unchanged.
