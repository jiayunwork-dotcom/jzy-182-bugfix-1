// Wall-clock helpers bound to the project's working timezone.
//
// The backend stores timestamps with explicit offsets and renders them in
// the project's own zone; a naive timestamp (no offset) is attached to that
// zone.  These helpers let the UI show and enter *team wall time* no matter
// which timezone the browser itself runs in (the reported bug: on a
// UTC-configured browser the report form defaulted to UTC wall time,
// 8 hours behind the Beijing clock).

const FIXED_OFFSET =
  /^(?:Etc\/GMT|UTC|GMT)([+-])(\d{1,2})(?::?([0-5]\d))?$/

function pad2(n) {
  return String(n).padStart(2, '0')
}

// Fixed offset in minutes for names like Etc/GMT-8 / UTC+05:30 / GMT-5;
// null for IANA zones (Asia/Shanghai) whose offset can change with DST.
export function fixedOffsetMinutes(timeZone) {
  if (!timeZone || timeZone === 'UTC') return 0
  const m = FIXED_OFFSET.exec(timeZone)
  if (!m) return null
  const [, sign, hh, mm] = m
  let mins = Number(hh) * 60 + Number(mm || 0)
  if (timeZone.startsWith('Etc/')) {
    // POSIX Etc zones use the inverted sign: Etc/GMT-8 == UTC+8.
    mins = sign === '-' ? mins : -mins
  } else if (sign === '-') {
    mins = -mins
  }
  return mins
}

function wallParts(date, timeZone) {
  const fixed = fixedOffsetMinutes(timeZone)
  if (fixed !== null) {
    const w = new Date(date.getTime() + fixed * 60000)
    return {
      year: String(w.getUTCFullYear()),
      month: pad2(w.getUTCMonth() + 1),
      day: pad2(w.getUTCDate()),
      hour: pad2(w.getUTCHours()),
      minute: pad2(w.getUTCMinutes()),
    }
  }
  const dtf = new Intl.DateTimeFormat('en-CA', {
    timeZone, hour12: false,
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  })
  const parts = Object.fromEntries(
    dtf.formatToParts(date).filter((x) => x.type !== 'literal')
      .map((x) => [x.type, x.value]))
  // en-CA may render midnight as hour "24"
  return { ...parts, hour: parts.hour === '24' ? '00' : parts.hour }
}

// Value for <input type="datetime-local">: current wall time in project zone.
export function wallNowInput(timeZone) {
  const p = wallParts(new Date(), timeZone)
  return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`
}

// Human-readable wall time of an ISO instant in the project zone.
export function formatWall(iso, timeZone) {
  const p = wallParts(new Date(iso), timeZone)
  return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}`
}
