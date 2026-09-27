import { useState } from "react"
import { BellIcon, ChevronDownIcon, PlusIcon, SaveIcon, Trash2Icon } from "lucide-react"
import { toast } from "sonner"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardAction, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { cn } from "@/lib/utils"
import type { Profile } from "./App"

type RuleForm = {
  id: string
  label: string
  profile_iccid: string
  enabled: boolean
  baseline_date: string
  reset_baseline?: boolean
}

export type ReceiveReminderSnapshot = {
  rules: (RuleForm & {
    profile_name: string
    anchor_label: string
    due_label: string
    due_at: string
    days_remaining: number
    last_sms_id: string
    last_received_label: string
    stage: number | null
  })[]
  period_days: number
  warning_days: number[]
  scheduler_enabled: boolean
  error: string
}

type Props = {
  profiles: Profile[]
  snapshot?: ReceiveReminderSnapshot | null
  busy: boolean
  onSaved: () => unknown
}

function formRule({ id, label, profile_iccid, enabled, baseline_date }: RuleForm): RuleForm {
  return { id, label, profile_iccid, enabled, baseline_date }
}

export function ReceiveReminderPanel({ profiles, snapshot, busy, onSaved }: Props) {
  const [draft, setDraft] = useState<RuleForm[] | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  if (!snapshot) return null
  const rules = draft ?? snapshot.rules.map(formRule)
  const locked = busy || saving
  const available = profiles.filter((p) => !rules.some((r) => r.profile_iccid === p.iccid))
  function update(id: string, fields: Partial<RuleForm>) {
    setDraft(rules.map((rule) => rule.id === id ? { ...rule, ...fields } : rule))
  }
  function addRule() {
    const profile = available[0]
    if (!profile) return
    const id = globalThis.crypto?.randomUUID?.() ?? `receive-${Date.now()}`
    setDraft([...rules, { id, label: `${profile.display_name} 收码提醒`, profile_iccid: profile.iccid, enabled: false, baseline_date: "" }])
    setExpanded(id)
  }
  async function save() {
    setSaving(true)
    try {
      const response = await fetch("/api/receive-reminders", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ rules }) })
      const result = await response.json() as { ok?: boolean; error?: string }
      if (!response.ok || !result.ok) throw new Error(result.error || "保存收码提醒失败")
      await onSaved()
      setDraft(null)
      toast.success("收码提醒已保存")
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存收码提醒失败")
    } finally {
      setSaving(false)
    }
  }

  return <Card>
    <CardHeader><CardTitle className="flex items-center gap-2"><BellIcon className="size-4" />收码提醒</CardTitle><CardDescription>365 天内收到一次验证码 · 飞书提醒</CardDescription><CardAction><Button variant="outline" size="sm" disabled={locked || !available.length} onClick={addRule}><PlusIcon />添加提醒</Button></CardAction></CardHeader>
    <CardContent className="space-y-4">
      {snapshot.error ? <p className="text-sm text-destructive">{snapshot.error}</p> : null}
      {rules.map((rule) => {
        const saved = snapshot.rules.find((r) => r.id === rule.id)
        const open = expanded === rule.id
        const profileName = profiles.find((p) => p.iccid === rule.profile_iccid)?.display_name || "选择卡片"
        const urgent = rule.enabled && saved?.stage != null
        const state = !rule.enabled ? "已暂停" : saved?.stage === 0 ? "已到期" : urgent ? "即将到期" : "已开启"
        return <div key={rule.id} className="rounded-lg border">
          <div className="flex flex-wrap items-center justify-between gap-3 p-4">
            <div><p className="text-sm font-medium">{rule.label || "新建提醒"}</p><p className="mt-1 text-xs text-muted-foreground">{profileName} · 收到验证码后重新计算 365 天</p></div>
            <div className="flex items-center gap-1"><Badge variant={urgent ? "destructive" : "secondary"}>{state}</Badge><Button size="sm" variant="ghost" aria-expanded={open} onClick={() => setExpanded(open ? null : rule.id)}><ChevronDownIcon className={cn(open && "rotate-180")} />{open ? "收起" : "编辑"}</Button></div>
          </div>
          <div className="grid gap-3 border-t bg-muted/20 px-4 py-3 text-sm sm:grid-cols-3">
            <div><p className="text-xs text-muted-foreground">当前起算时间</p><p className="mt-1 font-medium tabular-nums">{saved?.anchor_label || "保存后计算"}</p><p className="mt-1 text-xs text-muted-foreground">{saved?.last_sms_id ? `识别到验证码 · 短信 #${saved.last_sms_id}` : "手动起算"}</p></div>
            <div><p className="text-xs text-muted-foreground">365 天期限</p><p className="mt-1 font-medium tabular-nums">{saved?.due_label || "—"}</p><p className="mt-1 text-xs text-muted-foreground">北京时间{draft ? " · 以已保存的配置为准" : ""}</p></div>
            <div><p className="text-xs text-muted-foreground">剩余时间</p><p className={cn("mt-1 font-medium tabular-nums", urgent && "text-destructive")}>{saved ? saved.days_remaining <= 0 ? "已到期，请收码" : `${saved.days_remaining} 天` : "—"}</p></div>
          </div>
          {open ? <div className="space-y-4 border-t p-4">
            <div className="flex items-center justify-between gap-3"><Label htmlFor={`receive-enabled-${rule.id}`}>飞书提醒</Label><Switch id={`receive-enabled-${rule.id}`} checked={rule.enabled} disabled={locked} onCheckedChange={(enabled) => update(rule.id, { enabled })} /></div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2"><Label htmlFor={`receive-name-${rule.id}`}>提醒名称</Label><Input id={`receive-name-${rule.id}`} value={rule.label} disabled={locked} onChange={(e) => update(rule.id, { label: e.target.value })} /></div>
              <div className="space-y-2"><Label htmlFor={`receive-card-${rule.id}`}>对应卡片</Label><Select value={rule.profile_iccid} disabled={locked} onValueChange={(value) => update(rule.id, { profile_iccid: value || "" })}><SelectTrigger id={`receive-card-${rule.id}`} className="w-full"><SelectValue>{profileName}</SelectValue></SelectTrigger><SelectContent>{profiles.filter((p) => p.iccid === rule.profile_iccid || available.some((a) => a.iccid === p.iccid)).map((p) => <SelectItem key={p.iccid} value={p.iccid}>{p.display_name}</SelectItem>)}</SelectContent></Select></div>
              <div className="space-y-2 sm:col-span-2"><Label htmlFor={`receive-date-${rule.id}`}>最近真实收码或开卡日期</Label><div className="flex flex-wrap gap-2"><Input className="w-auto" id={`receive-date-${rule.id}`} type="date" value={rule.baseline_date} disabled={locked} onChange={(e) => update(rule.id, { baseline_date: e.target.value, reset_baseline: true })} /><Button variant="outline" disabled={locked || !rule.baseline_date} onClick={() => update(rule.id, { reset_baseline: true })}>按此日期重新计时</Button></div><p className="text-xs leading-5 text-muted-foreground">{rule.reset_baseline ? "保存后会用此日期覆盖此前的自动识别结果。" : "自动识别有误或漏识别时，可在这里校正日期。"}</p></div>
            </div>
            <div className="flex justify-end"><Button variant="ghost" size="sm" className="text-destructive" disabled={locked} onClick={() => setDraft(rules.filter((r) => r.id !== rule.id))}><Trash2Icon />移除提醒</Button></div>
          </div> : null}
        </div>
      })}
      {!rules.length ? <p className="rounded-lg border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">为需要定期收码的卡片添加提醒。</p> : null}
      <p className="text-xs leading-5 text-muted-foreground">提前 30、7、1 天及到期时提醒，每个阶段一次。只统计带验证码关键词和代码的短信，测试短信及未归属卡片的历史导入不计入；自动识别可能有遗漏。本功能不会切卡或发短信。</p>
    </CardContent>
    <CardFooter className="flex-wrap gap-2"><Button disabled={locked || draft === null} onClick={() => void save()}><SaveIcon />保存提醒</Button><Button variant="outline" disabled={locked || draft === null} onClick={() => setDraft(null)}>撤销修改</Button>{draft !== null ? <span className="ml-auto text-xs text-muted-foreground">有未保存的修改</span> : null}</CardFooter>
  </Card>
}
