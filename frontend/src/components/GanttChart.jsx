// Gantt-style plan view rendered purely from the backend plan JSON.
// Critical-chain tasks are highlighted; the project buffer (PB) and feeding
// buffers (FB) are drawn as hatched bars; resource edges are listed.

function barColor(task, onCc) {
  return onCc ? 'var(--cc)' : 'var(--normal)'
}

export default function GanttChart({ payload, plan }) {
  const rows = payload.task_order.map((id) => {
    const t = payload.tasks[id]
    const s = plan.schedule[id]
    return { id, name: t.name, ...s, demands: t.demands,
             agg: t.aggressive_days, cons: t.conservative_days }
  })

  const pb = plan.project_buffer
  const totalMax = pb.end_day
  const dayWidth = 100 / totalMax

  return (
    <section className="card">
      <h2>计划甘特图</h2>
      <div className="legend">
        <span><i className="swatch cc" /> 关键链任务</span>
        <span><i className="swatch normal" /> 非关键任务</span>
        <span><i className="swatch pb" /> 项目缓冲 PB</span>
        <span><i className="swatch fb" /> 汇入缓冲 FB</span>
      </div>
      <div className="gantt">
        <div className="gantt-scale">
          {Array.from({ length: Math.floor(totalMax) + 1 }, (_, d) => (
            <span key={d} style={{ left: `${d * dayWidth}%` }}>{d}</span>
          ))}
        </div>

        {rows.map((r) => (
          <div className="gantt-row" key={r.id}>
            <div className="gantt-label">
              <strong>{r.id}</strong> {r.name}
              {r.on_critical_chain && <em className="cc-tag">CC</em>}
              <small> 激进 {r.agg}d / 保守 {r.cons}d
                {Object.keys(r.demands).length > 0 &&
                  ` · ${Object.entries(r.demands)
                    .map(([k, v]) => `${k}×${v}`).join(', ')}`}
              </small>
            </div>
            <div className="gantt-track">
              <div
                className={`bar ${r.on_critical_chain ? 'bar-cc' : 'bar-normal'}`}
                style={{
                  left: `${r.start_day * dayWidth}%`,
                  width: `${Math.max((r.finish_day - r.start_day) * dayWidth, 0.5)}%`,
                }}
                title={`${r.id} 第 ${r.start_day}–${r.finish_day} 工作日`}
              >
                {r.id}
              </div>
            </div>
          </div>
        ))}

        {plan.feeding_buffers.map((fb) => (
          <div className="gantt-row buffer-row" key={fb.id}>
            <div className="gantt-label">
              <em>{fb.id}</em>
              <small> 汇入 {fb.feeds_task}（链：{fb.chain.join('→')}）</small>
            </div>
            <div className="gantt-track">
              <div className="bar bar-fb"
                style={{ left: `${fb.start_day * dayWidth}%`,
                         width: `${fb.size_days * dayWidth}%` }}
                title={`${fb.id} = ${fb.size_days} 工作日`}>
                {fb.id} {fb.size_days}d
              </div>
            </div>
          </div>
        ))}

        <div className="gantt-row buffer-row">
          <div className="gantt-label">
            <em>PB</em><small> 项目缓冲（链尾）</small>
          </div>
          <div className="gantt-track">
            <div className="bar bar-pb"
              style={{ left: `${pb.start_day * dayWidth}%`,
                       width: `${pb.size_days * dayWidth}%` }}
              title={`项目缓冲 ${pb.size_days} 工作日`}>
              PB {pb.size_days}d
            </div>
          </div>
        </div>
      </div>

      <div className="plan-meta">
        <span>关键链：<strong>{plan.critical_chain.join(' → ')}</strong></span>
        <span>计划工期（激进）：<strong>{plan.makespan_days} 工作日</strong></span>
        <span>含项目缓冲：<strong>{pb.end_day} 工作日</strong></span>
      </div>
      {plan.resource_edges.length > 0 && (
        <div className="plan-meta">
          <span>资源冲突边（等待关系）：
            {plan.resource_edges.map((e) =>
              `${e[0]}→${e[1]}`).join('，')}
          </span>
        </div>
      )}
      <details>
        <summary>缓冲推算依据</summary>
        <ul>
          {pb.basis.map((b) => (
            <li key={b.task_id}>{b.task_id}: 保守 {b.conservative_days} −
              激进 {b.aggressive_days} = {b.delta_days}d</li>
          ))}
          <li>PB = 0.5 × Σ = {pb.size_days}d</li>
        </ul>
      </details>
    </section>
  )
}
