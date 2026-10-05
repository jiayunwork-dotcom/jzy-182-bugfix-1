import { useState } from 'react'
import { formatWall } from '../time.js'

// Fever chart: x = critical-chain completion %, y = project-buffer consumed %.
// The two project thresholds are *horizontal* boundary lines dividing the
// plane into green / yellow / red bands (see docs/DECISIONS.md).

const W = 560
const H = 360
const PAD = 46

export default function FeverChart({ fever, timezone = 'UTC', loading }) {
  const [selected, setSelected] = useState(null)
  if (loading) return <section className="card">加载发烧图…</section>
  if (!fever) return null
  const { yellow_pct, red_pct } = fever.thresholds
  const points = fever.points

  const x = (pct) => PAD + (pct / 100) * (W - PAD - 12)
  const y = (pct) => H - PAD - (Math.min(pct, 100) / 100) * (H - PAD - 12)

  const path = points.length > 0
    ? 'M ' + points.map((p) =>
        `${x(p.critical_chain_completion_pct)},${y(p.buffer_consumed_pct)}`
      ).join(' L ')
    : ''

  return (
    <section className="card">
      <h2>发烧图（缓冲消耗轨迹）</h2>
      <p className="hint">
        横轴：关键链完成百分比；纵轴：项目缓冲消耗百分比。
        分界线 黄 {yellow_pct}% / 红 {red_pct}%。点击轨迹上的点，
        查看该汇报时刻各任务的剩余工期。
      </p>
      <div className="fever-wrap">
        <svg viewBox={`0 0 ${W} ${H}`} className="fever-svg">
          {/* bands */}
          <rect x={PAD} y={y(100)} width={W - PAD - 12}
            height={y(red_pct) - y(100)} className="band red" />
          <rect x={PAD} y={y(red_pct)} width={W - PAD - 12}
            height={y(yellow_pct) - y(red_pct)} className="band yellow" />
          <rect x={PAD} y={y(yellow_pct)} width={W - PAD - 12}
            height={y(0) - y(yellow_pct)} className="band green" />

          {/* axes */}
          <line x1={PAD} y1={H - PAD} x2={W - 12} y2={H - PAD}
            stroke="#555" />
          <line x1={PAD} y1={12} x2={PAD} y2={H - PAD} stroke="#555" />
          {[0, 25, 50, 75, 100].map((v) => (
            <g key={`x${v}`}>
              <line x1={x(v)} y1={H - PAD} x2={x(v)} y2={H - PAD + 4}
                stroke="#555" />
              <text x={x(v)} y={H - PAD + 18} textAnchor="middle"
                className="axis-text">{v}</text>
            </g>
          ))}
          {[0, 25, 50, 75, 100].map((v) => (
            <g key={`y${v}`}>
              <line x1={PAD - 4} y1={y(v)} x2={PAD} y2={y(v)}
                stroke="#555" />
              <text x={PAD - 8} y={y(v) + 4} textAnchor="end"
                className="axis-text">{v}</text>
            </g>
          ))}
          <text x={W / 2} y={H - 6} textAnchor="middle"
            className="axis-title">关键链完成 %</text>
          <text x={14} y={H / 2} textAnchor="middle"
            className="axis-title"
            transform={`rotate(-90 14 ${H / 2})`}>缓冲消耗 %</text>

          {/* trajectory */}
          {path && <path d={path} className="fever-path" />}
          {points.map((p, i) => (
            <circle key={p.report_id}
              cx={x(p.critical_chain_completion_pct)}
              cy={y(p.buffer_consumed_pct)}
              r={selected === i ? 7 : 5}
              className={`fever-dot zone-${p.zone}`}
              onClick={() => setSelected(i)}>
              <title>{`${formatWall(p.at, timezone)} · ${p.zone.toUpperCase()}`}</title>
            </circle>
          ))}
          {points.length === 0 && (
            <text x={W / 2} y={H / 2} textAnchor="middle" className="empty">
              还没有汇报，提交第一条汇报后轨迹出现
            </text>
          )}
        </svg>

        {selected !== null && points[selected] && (
          <PointDetail point={points[selected]} timezone={timezone}
            onClose={() => setSelected(null)} />
        )}
      </div>
    </section>
  )
}

function PointDetail({ point, timezone, onClose }) {
  return (
    <div className="point-detail">
      <h3>{formatWall(point.at, timezone)} 时刻</h3>
      <p>汇报 {point.report_id} · 区域 <strong>{point.zone}</strong> ·
        链完成 {point.critical_chain_completion_pct}% ·
        缓冲消耗 {point.buffer_consumed_pct}%</p>
      <table>
        <thead>
          <tr><th>任务</th><th>状态</th><th>剩余工期（工作日）</th><th>CC</th></tr>
        </thead>
        <tbody>
          {point.tasks.map((t) => (
            <tr key={t.task_id}>
              <td>{t.task_id} {t.name}</td>
              <td>{statusLabel(t.status)}</td>
              <td>{t.remaining_days}</td>
              <td>{t.on_critical_chain ? '是' : ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <button className="secondary" onClick={onClose}>关闭</button>
    </div>
  )
}

function statusLabel(s) {
  return { not_started: '未开始', in_progress: '进行中', done: '已完成' }[s] || s
}
