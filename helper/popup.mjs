// Which widget copies have their popup open (D5: one helper per shell, many
// copies). The helper gets one flag, open while any copy's popup is open, so
// device frames flow while any popup shows the device rows. Pure functions;
// tests/popup.test.mjs checks them.

// The list of copies whose popup is open, with `owner` set to `isOpen`. A copy
// appears at most once; the list keeps the order copies opened in.
export function withOpenPopup(openPopups, owner, isOpen) {
  const rest = openPopups.filter((entry) => entry.owner !== owner)
  return isOpen ? rest.concat([{ owner }]) : rest
}

// True while any copy's popup is open.
export function anyPopupOpen(openPopups) {
  return openPopups.length > 0
}
