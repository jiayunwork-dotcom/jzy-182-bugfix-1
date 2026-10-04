import { useMemo, useState } from 'react'

const EXAMPLE = {
  name: '四任务示例',
  // 团队按北京时间作息：日历（工作日、09:00-18:00）按该时区的墙钟判定；
  // 不带偏移的 start_time 就是该时区的墙钟时间。
  // 不填 timezone 时默认 UTC（旧项目口径，数字不变）。
  timezone: 'Asia/Shanghai',
  start_time: '2026-01-05T09:00:00',
  resources: [{ id: 'eng', name: '工程师', capacity: 1 }],
  tasks: [
    { id: 'A', name: '需求', predecessors: [], demands: { eng: 1 },
      aggressive_days: 4, conservative_days: 6 },
    { id: 'B', name: '后端开发', predecessors: ['A'], demands: { eng: 1 },
      aggressive_days: 6, conservative_days: 10 },
    { id: 'C', name: '前端开发', predecessors: ['A'], demands: { eng: 1 },
      aggressive_days: 3, conservative_days: 5 },
    { id: 'D', name: '发布', predecessors: ['B', 'C'], demands: {},
      aggressive_days: 2, conservative_days: 4 },
  ],
  thresholds: { yellow_pct: 50, red_pct: 80 },
}

export default function ProjectForm({ onCreate }) {
  const [text, setText] = useState(JSON.stringify(EXAMPLE, null, 2))
  const [errors, setErrors] = useState([])
  const [busy, setBusy] = useState(false)

  const parsedOk = useMemo(() => {
    try {
      JSON.parse(text)
      return true
    } catch {
      return false
    }
  }, [text])

  async function submit() {
    setBusy(true)
    setErrors([])
    try {
      const payload = JSON.parse(text)
      const result = await onCreate(payload)
      if (result?.error) setErrors(result.error.errors || [])
    } catch (e) {
      if (e.errors) setErrors(e.errors)
      else setErrors([{ field: 'body', message: e.message }])
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card">
      <h2>录入 / 导入项目</h2>
      <p className="hint">
        粘贴任务网络 JSON（资源、任务紧前关系、激进取/保守工期、发烧分界线）。
        所有校验与排程都在后端完成。
      </p>
      <textarea
        className="json-input"
        rows={18}
        value={text}
        spellCheck={false}
        onChange={(e) => setText(e.target.value)}
      />
      <div className="row">
        <button onClick={submit} disabled={busy || !parsedOk}>
          {busy ? '提交中…' : '创建项目并生成计划'}
        </button>
        <button className="secondary"
          onClick={() => setText(JSON.stringify(EXAMPLE, null, 2))}>
          载入四任务示例
        </button>
        {!parsedOk && <span className="error-text">JSON 格式有误</span>}
      </div>
      {errors.length > 0 && (
        <div className="error-box">
          <strong>以下字段被拒收：</strong>
          <ul>
            {errors.map((e, i) => (
              <li key={i}><code>{e.field || '(body)'}</code> — {e.message}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
