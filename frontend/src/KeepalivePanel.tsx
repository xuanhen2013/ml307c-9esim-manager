import { useState } from "react"
import { CalendarClockIcon, ChevronDownIcon, PlusIcon, SaveIcon, Trash2Icon } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardAction, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/lib/utils"
import type { KeepaliveFormTask, KeepaliveSettings, Profile, StatusData } from "./App"

type Props = {
  profiles: Profile[]
  tasks: KeepaliveFormTask[]
  settings: KeepaliveSettings
  snapshot: NonNullable<StatusData["keepalive"]>
  busy: boolean
  onTasksChange: (tasks: KeepaliveFormTask[]) => void
  onSettingsChange: (settings: KeepaliveSettings) => void
  onSave: () => void
  onReset: () => void
}

export function KeepalivePanel({ profiles, tasks, settings, snapshot, busy, onTasksChange, onSettingsChange, onSave, onReset }: Props) {
  const [expanded, setExpanded] = useState<string | null>(null)
  const dirty = tasks.length !== snapshot.tasks.length || tasks.some((task, index) =>
    Object.entries(task).some(([key, value]) => value !== snapshot.tasks[index]?.[key as keyof typeof task])) || settings.queue_gap_seconds !== snapshot.settings.queue_gap_seconds
  const locked = busy || !!snapshot.active_run || snapshot.queued_runs.length > 0
  function update(id: string, fields: Partial<KeepaliveFormTask>) {
    onTasksChange(tasks.map((task) => task.id === id ? { ...task, ...fields } : task))
  }
  function addTask() {
    const id = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
    const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date())
    onTasksChange([...tasks, { id, label: "", enabled: false, profile_iccid: profiles.find((p) => p.is_active)?.iccid || profiles[0]?.iccid || "", target_number: "", message: "Keep alive", cron_expression: "", schedule_type: "interval", start_date: today, send_time: "09:00", first_delay_days: 70, interval_days: 85 }])
    setExpanded(id)
  }
  const runs = [snapshot.active_run, ...snapshot.queued_runs, ...snapshot.recent_runs].filter((run) => run !== null)

  return <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_280px]">
    <Card className="min-w-0">
      <CardHeader><CardTitle>保号任务</CardTitle><CardDescription>按天间隔发送短信 · 北京时间</CardDescription><CardAction><Button variant="outline" size="sm" disabled={locked || !profiles.length} onClick={addTask}><PlusIcon />添加任务</Button></CardAction></CardHeader>
      <CardContent className="space-y-4">
        {tasks.map((task) => {
          const open = expanded === task.id
          const saved = snapshot.tasks.find((item) => item.id === task.id)
          const blocked = ["failed", "attention"].includes(saved?.runtime_state || "")
          const profileName = profiles.find((p) => p.iccid === task.profile_iccid)?.display_name || "选择卡片"
          return <div key={task.id} className="rounded-lg border">
            <div className="flex items-start justify-between gap-3 p-4">
              <div className="min-w-0"><p className="truncate text-sm font-medium">{task.label || "新建任务"}</p><p className="mt-1 text-xs text-muted-foreground">{profileName} · 首次 {task.first_delay_days} 天，之后每 {task.interval_days} 天</p></div>
              <div className="flex shrink-0 items-center gap-1"><Badge variant={blocked ? "destructive" : "secondary"}>{blocked ? "需处理" : task.enabled ? "已开启" : "已暂停"}</Badge><Button size="sm" variant="ghost" aria-expanded={open} onClick={() => setExpanded(open ? null : task.id)}><ChevronDownIcon className={cn(open && "rotate-180")} />{open ? "收起" : "编辑"}</Button></div>
            </div>
            <div className="grid gap-3 border-t bg-muted/20 px-4 py-3 text-sm sm:grid-cols-2">
              <div><p className="text-xs text-muted-foreground">{blocked || !task.enabled ? "原定时间" : "下次发送"}</p><p className="mt-1 font-medium tabular-nums">{saved?.next_run_label || "保存后计算"}</p>{dirty ? <p className="mt-1 text-xs text-muted-foreground">时间以已保存的配置为准</p> : null}</div>
              <div><p className="text-xs text-muted-foreground">收件号码</p><p className="mt-1 font-medium tabular-nums">{task.target_number || "待填写"}</p></div>
            </div>
            {saved?.error ? <p className="border-t px-4 py-3 text-sm text-destructive">{saved.error}</p> : null}
            {open ? <div className="space-y-4 border-t p-4">
              <div className="flex items-center justify-between gap-3"><Label htmlFor={`enabled-${task.id}`}>自动发送</Label><Switch id={`enabled-${task.id}`} checked={task.enabled} disabled={locked || saved?.runtime_state === "attention"} onCheckedChange={(enabled) => update(task.id, { enabled })} /></div>
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2"><Label htmlFor={`name-${task.id}`}>任务名称</Label><Input id={`name-${task.id}`} value={task.label} onChange={(e) => update(task.id, { label: e.target.value })} placeholder="例如 Lebara 保号" disabled={locked} /></div>
                <div className="space-y-2"><Label htmlFor={`card-${task.id}`}>使用卡片</Label><Select value={task.profile_iccid} onValueChange={(value) => update(task.id, { profile_iccid: value || "" })} disabled={locked}><SelectTrigger id={`card-${task.id}`} className="w-full"><SelectValue>{profileName}</SelectValue></SelectTrigger><SelectContent>{profiles.map((p) => <SelectItem key={p.iccid} value={p.iccid}>{p.display_name}</SelectItem>)}</SelectContent></Select></div>
                <div className="space-y-2"><Label htmlFor={`start-${task.id}`}>起算日期</Label><Input id={`start-${task.id}`} type="date" value={task.start_date || ""} onChange={(e) => update(task.id, { start_date: e.target.value })} disabled={locked} /></div>
                <div className="space-y-2"><Label htmlFor={`first-${task.id}`}>首次等待（天）</Label><Input id={`first-${task.id}`} type="number" min={1} max={3650} value={task.first_delay_days ?? 70} onChange={(e) => update(task.id, { first_delay_days: Number(e.target.value) })} disabled={locked} /></div>
                <div className="space-y-2"><Label htmlFor={`interval-${task.id}`}>后续间隔（天）</Label><Input id={`interval-${task.id}`} type="number" min={1} max={3650} value={task.interval_days ?? 85} onChange={(e) => update(task.id, { interval_days: Number(e.target.value) })} disabled={locked} /><p className="text-xs text-muted-foreground">从上次短信提交成功起计算。</p></div>
                <div className="space-y-2"><Label htmlFor={`time-${task.id}`}>首次发送时间</Label><Input id={`time-${task.id}`} type="time" value={task.send_time || "09:00"} onChange={(e) => update(task.id, { send_time: e.target.value })} disabled={locked} /></div>
                <div className="space-y-2 sm:col-span-2"><Label htmlFor={`number-${task.id}`}>收件号码</Label><Input id={`number-${task.id}`} value={task.target_number} onChange={(e) => update(task.id, { target_number: e.target.value })} placeholder="+国家区号及手机号" disabled={locked} /><p className="text-xs text-muted-foreground">短信费用按运营商漫游资费计算。</p></div>
              </div>
              <div className="space-y-2"><Label htmlFor={`message-${task.id}`}>短信内容</Label><Textarea id={`message-${task.id}`} value={task.message} onChange={(e) => update(task.id, { message: e.target.value })} maxLength={70} rows={2} disabled={locked} /><p className="text-xs text-muted-foreground">最多 70 字，不支持 emoji。</p></div>
              <div className="flex items-center justify-between gap-3"><p className="text-xs text-muted-foreground">开启已到期的任务后会尽快执行。</p><Button variant="ghost" size="sm" className="text-destructive" disabled={locked} onClick={() => onTasksChange(tasks.filter((item) => item.id !== task.id))}><Trash2Icon />移除</Button></div>
            </div> : null}
          </div>
        })}
        {!tasks.length ? <div className="flex min-h-44 flex-col items-center justify-center gap-3 rounded-lg border border-dashed p-6 text-center"><CalendarClockIcon className="size-6 text-muted-foreground" /><p className="text-sm">还没有保号任务</p></div> : null}
      </CardContent>
      <CardFooter className="flex-wrap gap-2"><Button disabled={locked || !dirty} onClick={onSave}><SaveIcon />保存配置</Button><Button variant="outline" disabled={locked || !dirty} onClick={() => { onReset(); setExpanded(null) }}>撤销修改</Button>{dirty ? <span className="ml-auto text-xs text-muted-foreground">有未保存的修改</span> : null}</CardFooter>
    </Card>
    <div className="space-y-5">
      <Card><CardHeader><CardTitle>执行方式</CardTitle></CardHeader><CardContent className="space-y-4 text-sm"><p className="leading-6 text-muted-foreground">到期后切到指定卡，等待联网并发送短信，完成后恢复原卡。结果通过飞书通知。</p><div className="space-y-2"><Label htmlFor="queue-gap">任务间隔（秒）</Label><Input id="queue-gap" type="number" min={30} max={1800} value={settings.queue_gap_seconds} disabled={locked} onChange={(e) => onSettingsChange({ queue_gap_seconds: Number(e.target.value) })} /></div><p className="text-xs leading-5 text-muted-foreground">失败或结果不明会暂停任务，不自动重发。NAS 离线错过的任务会在恢复后执行一次。</p></CardContent></Card>
      <Card><CardHeader><CardTitle>执行记录</CardTitle></CardHeader><CardContent>{runs.length ? <div className="space-y-4">{runs.map((run) => <div key={run.id} className="text-sm"><p className="font-medium">{run.label}</p><p className="mt-1 text-xs text-muted-foreground">{run.updated_at || run.scheduled_for_label}</p><p className="mt-1 text-xs leading-5">{run.error || run.last_message || run.state}</p></div>)}</div> : <p className="py-3 text-sm text-muted-foreground">暂无执行记录</p>}</CardContent></Card>
    </div>
  </div>
}
