import { useEffect, useState } from 'react'

// Report submission form. Reports are append-only; the backend folds them by
// (timestamp, report id), so out-of-order submission is accepted.

function nowIsoLocal() {
  const d = new Date()
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset())
  return d.toISOString().slice(0, 16)
}

export default function ReportForm({ payload, onSubmitted, lastState }) {
  const [reportId, setReportId] = useState('')
  const [taskId, setTaskId] = useState(payload.task_order[0] || '')
  const [at, setAt] = useState(nowIsoLocal())
  const [kind, setKind] = useState('remaining')
  const [remaining, setRemaining] = useState('1')
  const [notice, setNotice] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    setReportId(`r${Date.now().toString().slice(-6)}`)
  }, [])

  async function submit(event) {
    event.preventDefault()
    setNotice(null)
    setError(null)
    const body = {
      report_id: reportId.trim(),
      task_id: taskId,
      at: new Date(at).toISOString(),
      kind,
    }
    if (kind === 'remaining') {
      body.remaining_days = Number(remaining)
    }
    try {
      const result = await onSubmitted(body)
      setNotice(result.duplicate
        ? `汇报 ${result.report_id} 已存在，按幂等处理（状态不变）`
        : `汇报 ${result.report_id} 已追加，曲线已刷新`)
    } catch (e) {
      setError(e.message)
    }
  }

  return (
    <section className="card">
      <h2>提交进度汇报</h2>
      <form onSubmit={submit} className="report-form">
        <label>汇报编号
          <input value={reportId} onChange={(e) => setReportId(e.target.value)}
            required />
        </label>
        <label>任务
          <select value={taskId} onChange={(e) => setTaskId(e.target.value)}>
            {payload.task_order.map((id) => (
              <option key={id} value={id}>
                {id} · {payload.tasks[id].name}
              </option>
            ))}
          </select>
        </label>
        <label>汇报时刻
          <input type="datetime-local" value={at}
            onChange={(e) => setAt(e.target.value)} required />
        </label>
        <label>类型
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="remaining">剩余工期</option>
            <option value="done">已完成</option>
          </select>
        </label>
        {kind === 'remaining' && (
          <label>剩余工期（工作日）
            <input type="number" min="0" step="0.5" value={remaining}
              onChange={(e) => setRemaining(e.target.value)} required />
          </label>
        )}
        <button type="submit">追加汇报</button>
      </form>
      {notice && <div className="notice">{notice}</div>}
      {error && <div className="error-box">{error}</div>}
      {lastState && (
        <div className="current-state">
          当前：链完成 {lastState.critical_chain_completion_pct}% ·
          PB 消耗 {lastState.project_buffer.consumed_pct}% ·
          区域 <strong className={`zone-text-${lastState.zone}`}>
            {lastState.zone.toUpperCase()}</strong>
        </div>
      )}
    </section>
  )
}
