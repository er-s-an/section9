const FOCUSABLE_SELECTOR = 'button:not(:disabled), a[href], summary, [tabindex="0"]'

export function getVisibleFocusableElements(container: ParentNode): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter((item) => {
    if (item.getClientRects().length === 0) return false

    const closedDetails = item.closest('details:not([open])')
    if (!closedDetails) return true

    const isSummaryOfClosedDetails = item.tagName === 'SUMMARY' && item.parentElement === closedDetails
    const hiddenByAnotherClosedDetails = Boolean(closedDetails.parentElement?.closest('details:not([open])'))
    return isSummaryOfClosedDetails && !hiddenByAnotherClosedDetails
  })
}
