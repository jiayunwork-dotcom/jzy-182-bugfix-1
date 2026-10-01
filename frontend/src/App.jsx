import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import ProjectForm from './components/ProjectForm.jsx'
import GanttChart from './components/GanttChart.jsx'
import FeverChart from './components/FeverChart.jsx'
import ReportForm from './components/ReportForm.jsx'

export default function App() {
  const [projects, setProjects] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [planData, setPlanData] = useState(null)
  const [fever, setFever] = useState(null)
  const [state, setState] = useState(null)
  const [tab, setTab] = useState('gantt')

  const refreshList = useCallback(async () => {
    const data = await api.listProjects()
    setProjects(data.projects)
    return data.projects
  }, [])

  useEffect(() => { refreshList().catch(() => {}) }, [refreshList])

  const openProject = useCallback(async (id) => {
    setSelectedId(id)
    setTab('gantt')
    const [{ payload, plan }, f, s] = await Promise.all([
      api.getPlan(id), api.getFever(id), api.getState(id),
    ])
    setPlanData({ payload, plan })
    setFever(f)
    setState(s)
  }, [])

  const refreshExecution = useCallback(async () => {
    if (!selectedId) return
    const [f, s] = await Promise.all([
      api.getFever(selectedId), api.getState(selectedId)])
    setFever(f)
    setState(s)
  }, [selectedId])

  async function handleCreate(payload) {
    try {
      const result = await api.createProject(payload)
      const list = await refreshList()
      await openProject(result.id)
      return result
    } catch (e) {
      return { error: e }
    }
  }

  async function handleReport(body) {
    const result = await api.postReport(selectedId, body)
    await refreshExecution()
    return result
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <h1>关键链进度平台</h1>
        <nav>
          {projects.length === 0 && <p className="hint">暂无项目</p>}
          {projects.map((p) => (
            <button key={p.id}
              className={`nav-item ${p.id === selectedId ? 'active' : ''}`}
              onClick={() => openProject(p.id)}>
              {p.name}
            </button>
          ))}
        </nav>
        <button className="secondary new-btn"
          onClick={() => { setSelectedId(null); setPlanData(null) }}>
          ＋ 新建项目
        </button>
      </aside>

      <main className="content">
        {!selectedId && <ProjectForm onCreate={handleCreate} />}

        {selectedId && planData && (
          <>
            <div className="tabs">
              <button className={tab === 'gantt' ? 'active' : ''}
                onClick={() => setTab('gantt')}>计划甘特图</button>
              <button className={tab === 'fever' ? 'active' : ''}
                onClick={() => { setTab('fever'); refreshExecution() }}>
                发烧图</button>
              <button className={tab === 'report' ? 'active' : ''}
                onClick={() => setTab('report')}>进度汇报</button>
            </div>

            {tab === 'gantt' && (
              <GanttChart payload={planData.payload} plan={planData.plan} />
            )}
            {tab === 'fever' && (
              <FeverChart fever={fever}
                loading={!fever} />
            )}
            {tab === 'report' && (
              <>
                <ReportForm payload={planData.payload}
                  onSubmitted={handleReport} lastState={state} />
                <FeverChart fever={fever} loading={!fever} />
              </>
            )}
          </>
        )}
      </main>
    </div>
  )
}
