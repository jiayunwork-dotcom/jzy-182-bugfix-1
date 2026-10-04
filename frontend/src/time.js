// Timezone helpers.
//
// The backend owns a per-project timezone ("Asia/Shanghai" or a fixed
// offset like "+08:00"); naive timestamps are local wall time in it.
// The browser must render and default timestamps in the *project*
// timezone, not in the machine zone (the team is in Beijing even if the
// container or some laptop is set to UTC), so we never rely on
// Date's local getters -- everything goes through Intl with an explicit
// timeZone, or a manual shift for fixed offsets (Intl does not accept
// "+08:00" as a timeZone argument).

const FIXED_OFFSET_RE = /^([+-])(\d{2}):?(\d{2})?$/
const PREFIXED_OFFSET_RE = /^(?:UTC|GMT)([+-]\d{2}:?\d{0,2})$/

export function isFixedOffset(tz) {
  return tz === 'UTC' || tz === 'Z'
    || FIXED_OFFSET_RE.test(tz || '')
    || PREFIXED_OFFSET_RE.test(tz || '')
}

function shiftToFixedOffset(date, tz) {
  let offset = tz
  const pre = PREFIXED_OFFSET_RE.exec(tz || '')
  if (pre) offset = pre[1]
  let shift = 0
  if (offset !== 'UTC' && offset !== 'Z') {
    const m = FIXED_OFFSET_RE.exec(offset)
    const mins = Number(m[2]) * 60 + Number(m[3] || 0)
    shift = (m[1] === '+' ? 1 : -1) * mins * 60000
  }
  // Reading the shifted instant with UTC getters yields the wall clock
  // of the original instant in the target fixed-offset zone.
  return new Date(date.getTime() + shift).toISOString().slice(0, 23)
}

function partsInZone(date, timeZone) {
  if (!timeZone || isFixedOffset(timeZone)) {
    return shiftToFixedOffset(date, timeZone || 'UTC')
  }
  // IANA name: ask Intl for the wall-clock parts in that zone
  const fmt = new Intl.DateTimeFormat('en-GB', {
    timeZone, hour12: false,
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
  const p = Object.fromEntries(fmt.formatToParts(date)
    .filter((x) => x.type !== 'literal')
    .map((x) => [x.type, x.value]))
  const hour = p.hour === '24' ? '00' : p.hour
  return `${p.year}-${p.month}-${p.day}T${hour}:${p.minute}:${p.second}`
}

/** "YYYY-MM-DDTHH:mm" for a datetime-local input, in the project zone. */
export function localInputValue(date, timeZone) {
  return partsInZone(date, timeZone).slice(0, 16)
}

/** Human-readable wall-clock string in the project zone. */
export function formatInZone(dateLike, timeZone) {
  const date = new Date(dateLike)
  if (Number.isNaN(date.getTime())) return String(dateLike)
  const [datePart, timePart] = partsInZone(date, timeZone).split('T')
  const hm = timePart.slice(0, 5)
  return `${datePart} ${hm} ${timeZone || 'UTC'}`
}
