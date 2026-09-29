import { useState, type ReactNode } from "react"
import {
  AlertCircleIcon, ArrowRightIcon, BellIcon, CalendarClockIcon, CardSimIcon,
  CheckIcon, CopyIcon, InboxIcon, LoaderCircleIcon, RadioTowerIcon,
  RefreshCwIcon, SearchIcon, Settings2Icon, SignalIcon, TerminalIcon,
} from "lucide-react"
import { Toaster, toast } from "sonner"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardAction, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { cn } from "@/lib/utils"
import type { ActionEvent, Profile, SmsItem, StatusData } from "./App"

type Props = {
  status: StatusData | null
  loading: boolean
  refreshing: boolean
  busy: boolean
  actionLabel: string | null
  actionTarget: string | undefined
  autoRefresh: boolean
  refreshSeconds: number
  onAutoRefreshChange: (value: boolean) => void
  onRefreshSecondsChange: (value: number) => void
  notificationEditor: ReactNode
  keepaliveEditor: ReactNode
  logs: ActionEvent[]
  onRefresh: () => void
  onRefreshInbox: () => void
  onRecover: () => void
  onSwitch: (profile: Profile) => void
  onResendNotification: () => void
  onClearLogs: () => void
}

function messageTime(sms: SmsItem) {
  const raw = sms.imported ? sms.smsc_timestamp : sms.received_at
  if (raw) {
    const date = new Date(raw)
    if (!Number.isNaN(date.getTime())) {
      return new Intl.DateTimeFormat("zh-CN", {
        timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit", hour12: false,
      }).format(date)
    }
  }
  return sms.timestamp.replace(/（.*?）/g, "")
}

async function copyText(text: string) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
    } else {
      // LAN HTTP does not expose the secure-context Clipboard API.
      const focused = document.activeElement
      const selection = window.getSelection()
      const ranges = selection ? Array.from({ length: selection.rangeCount }, (_, index) => selection.getRangeAt(index).cloneRange()) : []
      const field = document.createElement("textarea")
      field.value = text
      field.readOnly = true
      field.style.cssText = "position:fixed;left:-9999px;top:0;opacity:0"
      document.body.appendChild(field)
      try {
        field.select()
        if (!document.execCommand("copy")) throw new Error("Copy unavailable")
      } finally {
        field.remove()
        if (focused instanceof HTMLElement) focused.focus({ preventScroll: true })
        if (selection) {
          selection.removeAllRanges()
          ranges.forEach((range) => selection.addRange(range))
        }
      }
    }
    toast.success("已复制")
  } catch { toast.error("复制失败，请手动选择文本") }
}

function Empty({ children }: { children: ReactNode }) {
  return <div className="flex min-h-40 flex-col items-center justify-center gap-3 p-6 text-sm text-muted-foreground"><InboxIcon className="size-6 opacity-50" />{children}</div>
}

function PhoneNumber({ number, label }: { number?: string; label: string }) {
  return <div className="flex items-center gap-1.5 text-sm tabular-nums">
    <span className="select-all">{number || "未记录号码"}</span>
    {number ? <Button size="icon-xs" variant="ghost" aria-label={`复制 ${label} 手机号`} title="复制手机号" onClick={() => void copyText(number)}><CopyIcon /></Button> : null}
  </div>
}

export function ReceiverView({
  status, loading, refreshing, busy, actionLabel, actionTarget,
  autoRefresh, refreshSeconds, onAutoRefreshChange, onRefreshSecondsChange,
  notificationEditor, keepaliveEditor, logs, onRefresh, onRefreshInbox,
  onSwitch, onResendNotification, onClearLogs, onRecover,
}: Props) {
  const [tab, setTab] = useState("inbox")
  const [query, setQuery] = useState("")
  const [cardFilter, setCardFilter] = useState("all")
  const [logSource, setLogSource] = useState("device")
  const visibleLogs = logSource === "device" ? (status?.device_diagnostics ?? []).map((entry) => ({
    ...entry, time: new Date(entry.time).toLocaleTimeString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" }),
  })) : logs
  const modem = status?.modem
  const online = status?.modem_available
  const signal = modem?.signal_details
  const messages = status?.sms ?? []
  const profiles = status?.profiles ?? []
  const active = profiles.find((profile) => profile.is_active)
  const configured = status?.notifications?.configured_count ?? 0
  const feishu = status?.feishu
  const feishuEnabled = Boolean(feishu?.configured && feishu.bound)
  const forwardingLabels = [...(feishuEnabled ? ["飞书机器人"] : []), ...(status?.notifications?.configured_labels ?? [])]
  const errors = [...new Set([...(status?.errors ?? []), ...(!busy && status?.status_message ? [status.status_message] : [])])]
  const registration = modem?.registration === "roaming" ? "漫游" : modem?.registration === "home" ? "已注册" : "未注册"
  const filterNames = [...new Set(messages.map((sms) => sms.imported ? "未知卡片" : sms.profile_name || "未知卡片"))]
  const filtered = messages.filter((sms) => {
    const name = sms.imported ? "未知卡片" : sms.profile_name || "未知卡片"
    return (cardFilter === "all" || cardFilter === name) && `${sms.number} ${sms.recipient_number || ""} ${sms.text} ${name}`.toLowerCase().includes(query.trim().toLowerCase())
  })

  function switchButton(profile: Profile) {
    const switching = busy && actionTarget === profile.iccid
    return profile.is_active ? <Badge variant="secondary" className="gap-1"><CheckIcon className="size-3" />使用中</Badge> : (
      <Button variant="outline" size="sm" disabled={busy || !online} aria-label={`切换到 ${profile.display_name}`} onClick={() => onSwitch(profile)}>
        {switching ? <LoaderCircleIcon className="animate-spin" /> : <ArrowRightIcon />}{switching ? "切换中" : "切换"}
      </Button>
    )
  }

  return (
    <div className="min-h-dvh bg-muted/30 text-foreground">
      <header className="border-b bg-background">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-4 px-4 py-4 sm:px-6 lg:px-8">
          <div className="flex items-center gap-3">
            <div className="flex size-9 items-center justify-center rounded-lg bg-primary text-primary-foreground"><CardSimIcon className="size-5" /></div>
            <div><h1 className="text-base font-semibold tracking-tight">eSIM SMS Forwarder</h1><p className="text-xs text-muted-foreground">ML307C · 9eSIM</p></div>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2"><Switch id="auto-refresh" aria-label="自动刷新" checked={autoRefresh} onCheckedChange={onAutoRefreshChange} /><Label htmlFor="auto-refresh" className="text-xs">自动刷新</Label></div>
            <Select value={String(refreshSeconds)} onValueChange={(value) => onRefreshSecondsChange(Number(value))} disabled={!autoRefresh}>
              <SelectTrigger size="sm" aria-label="刷新间隔" className="w-20 bg-background"><SelectValue>{refreshSeconds} 秒</SelectValue></SelectTrigger>
              <SelectContent>{[5, 10, 30, 60].map((seconds) => <SelectItem key={seconds} value={String(seconds)}>{seconds} 秒</SelectItem>)}</SelectContent>
            </Select>
            <Button variant="outline" size="sm" disabled={refreshing || busy} onClick={onRefresh}><RefreshCwIcon className={cn(refreshing && "animate-spin")} />{refreshing ? "刷新中" : "刷新"}</Button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl space-y-6 px-4 py-6 sm:px-6 lg:px-8">
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
          <div className="flex items-center gap-2"><span className={cn("size-1.5 rounded-full", online ? "bg-emerald-600" : "bg-muted-foreground")} />{loading ? "连接中" : online ? "设备已连接" : "设备未连接"}</div>
          <span>更新于 {status?.timestamp || "读取中…"}</span>
        </div>

        <Card className="gap-0 py-0">
          <CardContent className="grid divide-y px-0 sm:grid-cols-3 sm:divide-x sm:divide-y-0">
            <div className="space-y-2 p-5">
              <div className="flex items-center gap-2 text-xs text-muted-foreground"><CardSimIcon className="size-4" />当前卡片</div>
              <div className="text-xl font-semibold tracking-tight">{active?.display_name || (loading ? "读取中…" : "未读取到卡片")}</div>
              {active ? <PhoneNumber number={active.phone_number} label={active.display_name} /> : null}
              <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground"><span>{active ? `ICCID 尾号 ${active.iccid.slice(-6)}` : "设备一次启用一张卡"}</span><Button size="xs" variant="ghost" onClick={() => setTab("profiles")}>管理卡片<ArrowRightIcon /></Button></div>
            </div>
            <div className="space-y-2 p-5">
              <div className="flex items-center gap-2 text-xs text-muted-foreground"><RadioTowerIcon className="size-4" />驻网运营商</div>
              <div className="flex flex-wrap items-center gap-2"><span className="text-xl font-semibold tracking-tight">{online ? modem?.operator_name : "未连接"}</span>{online ? <Badge variant="outline">{registration}</Badge> : null}</div>
              <p className="text-xs text-muted-foreground">{online ? `PLMN ${modem?.operator_code} · ${modem?.access_tech}` : "连接后读取网络信息"}</p>
            </div>
            <div className="space-y-2 p-5">
              <div className="flex items-center gap-2 text-xs text-muted-foreground"><SignalIcon className="size-4" />信号强度 · {signal?.rsrp_text ? "RSRP" : "RSSI"}</div>
              <div className="text-xl font-semibold tracking-tight tabular-nums">{online ? signal?.rsrp_text || signal?.rssi_text || "未测得" : "未测得"}</div>
              <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground"><span>RSRQ {online ? signal?.rsrq_text || "未测得" : "未测得"}</span><Button size="xs" variant="ghost" onClick={() => setTab("device")}>详情<ArrowRightIcon /></Button></div>
            </div>
          </CardContent>
        </Card>

        {errors.length > 0 ? <Alert variant="destructive"><AlertCircleIcon /><AlertDescription>{errors.join("；")}</AlertDescription></Alert> : null}
        {busy ? <div role="status" className="flex items-center gap-2 rounded-lg border bg-background px-4 py-3 text-sm"><LoaderCircleIcon className="size-4 animate-spin" />{actionLabel}…<Button variant="link" size="sm" className="ml-auto" onClick={() => setTab("device")}>查看日志</Button></div> : null}

        <Tabs value={tab} onValueChange={(value) => setTab(String(value))} className="gap-5">
          <div className="-mx-1 overflow-x-auto px-1 pb-1"><TabsList className="h-10 gap-1" aria-label="功能导航">
            <TabsTrigger value="inbox" className="px-3"><InboxIcon />收件箱<span className="ml-1 text-xs text-muted-foreground">{messages.length}</span></TabsTrigger>
            <TabsTrigger value="profiles" className="px-3"><CardSimIcon />SIM 卡</TabsTrigger>
            <TabsTrigger value="keepalive" className="px-3"><CalendarClockIcon />保号任务</TabsTrigger>
            <TabsTrigger value="notifications" className="px-3"><BellIcon />通知渠道</TabsTrigger>
            <TabsTrigger value="device" className="px-3"><Settings2Icon />设备与日志</TabsTrigger>
          </TabsList></div>

          <TabsContent value="inbox">
            <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_280px]">
              <Card className="min-w-0">
                <CardHeader><CardTitle>收件箱</CardTitle><CardDescription>查看所有卡片已接收的短信</CardDescription></CardHeader>
                <CardContent className="flex flex-col gap-3 sm:flex-row">
                  <div className="relative min-w-0 flex-1"><SearchIcon className="absolute left-2.5 top-2 size-4 text-muted-foreground" /><Input aria-label="搜索短信" placeholder="搜索号码或短信内容" value={query} onChange={(event) => setQuery(event.target.value)} className="pl-9" /></div>
                  <Select value={cardFilter} onValueChange={(value) => setCardFilter(String(value))}><SelectTrigger aria-label="筛选收件卡" className="w-full sm:w-36"><SelectValue>{cardFilter === "all" ? "所有卡片" : cardFilter}</SelectValue></SelectTrigger><SelectContent><SelectItem value="all">所有卡片</SelectItem>{filterNames.map((name) => <SelectItem key={name} value={name}>{name}</SelectItem>)}</SelectContent></Select>
                </CardContent>
                <div className="divide-y border-t">
                  {filtered.map((sms) => <article key={sms.id} className="group px-4 py-5">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex flex-wrap items-center gap-2"><span className="text-sm font-medium tabular-nums">{sms.number || "未知号码"}</span><Badge variant="outline">{sms.imported ? "未知卡片" : sms.profile_name || "未知卡片"}</Badge></div>
                      <div className="flex items-center gap-2"><time className="text-xs text-muted-foreground" title={sms.imported ? "短信网络时间" : "本机接收时间"}>{messageTime(sms)}{sms.imported ? " · 网络时间" : ""}</time><Button variant="ghost" size="icon-xs" aria-label={`复制短信 ${sms.id}`} title="复制短信" onClick={() => void copyText(sms.text)}><CopyIcon /></Button></div>
                    </div>
                    {sms.recipient_number ? <p className="mt-2 text-xs text-muted-foreground tabular-nums">收件号码 {sms.recipient_number}</p> : null}
                    <p className="mt-3 whitespace-pre-wrap break-words text-sm leading-6">{sms.text || "（空短信）"}</p>
                  </article>)}
                  {!filtered.length ? <Empty>{loading ? "读取中…" : query || cardFilter !== "all" ? "没有匹配的短信" : "暂无短信"}</Empty> : null}
                </div>
                <CardFooter className="justify-between gap-3 text-xs text-muted-foreground"><span>{filtered.length} / {messages.length} 条短信</span><span>{autoRefresh ? `每 ${refreshSeconds} 秒自动刷新` : "页面自动刷新已暂停"}</span></CardFooter>
              </Card>
              <div className="space-y-5">
                <Card><CardHeader><CardTitle>快速切卡</CardTitle></CardHeader><CardContent className="space-y-4">{profiles.map((profile) => <div key={profile.iccid} className="flex items-center justify-between gap-3"><div><p className="text-sm font-medium">{profile.display_name}</p><div className="mt-1 text-muted-foreground"><PhoneNumber number={profile.phone_number} label={profile.display_name} /></div></div>{switchButton(profile)}</div>)}{!profiles.length ? <p className="text-sm text-muted-foreground">未读取到卡片</p> : null}</CardContent></Card>
                <Card><CardHeader><CardTitle>短信转发</CardTitle><CardDescription>{forwardingLabels.length ? `已启用：${forwardingLabels.join("、")}` : "尚未配置通知渠道"}</CardDescription></CardHeader><CardContent><Button variant="outline" className="w-full" onClick={() => setTab("notifications")}><BellIcon />查看通知渠道</Button></CardContent></Card>
              </div>
            </div>
          </TabsContent>

          <TabsContent value="profiles">
            <div className="mb-4 flex items-baseline gap-3"><h2 className="text-base font-medium">SIM 卡</h2><p className="text-sm text-muted-foreground">{profiles.length} 张卡片 · 同时启用一张</p></div>
            <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">{profiles.map((profile) => <Card key={profile.iccid}><CardHeader><CardTitle className="flex items-center gap-2"><CardSimIcon className="size-4 text-muted-foreground" />{profile.display_name}</CardTitle><CardAction>{profile.is_active ? <Badge variant="secondary">使用中</Badge> : <Badge variant="outline">未启用</Badge>}</CardAction></CardHeader><CardContent className="space-y-4"><div><Label className="text-xs text-muted-foreground">手机号</Label><div className="mt-1"><PhoneNumber number={profile.phone_number} label={profile.display_name} /></div></div><div><Label className="text-xs text-muted-foreground">ICCID</Label><div className="mt-1 flex items-center gap-2"><code className="break-all text-xs">{profile.iccid}</code><Button size="icon-xs" variant="ghost" aria-label={`复制 ${profile.display_name} ICCID`} onClick={() => void copyText(profile.iccid)}><CopyIcon /></Button></div></div>{profile.provider_name ? <div><p className="text-xs text-muted-foreground">服务商</p><p className="mt-1 text-sm">{profile.provider_name}</p></div> : null}</CardContent><CardFooter className="justify-end">{profile.is_active ? <span className="text-xs text-muted-foreground">当前接收短信的卡片</span> : switchButton(profile)}</CardFooter></Card>)}</div>
            {!profiles.length ? <Card><Empty>{loading ? "读取中…" : "未读取到卡片"}</Empty></Card> : null}
          </TabsContent>

          <TabsContent value="keepalive">{keepaliveEditor}</TabsContent>

          <TabsContent value="notifications" className="space-y-5">
            {feishu?.configured ? <Card><CardHeader><CardTitle>飞书机器人</CardTitle><CardDescription>{feishuEnabled ? "新短信自动发送到已绑定的飞书私聊" : "在飞书私聊中发送“绑定 一次性绑定码”完成设置"}</CardDescription><CardAction><Badge variant={feishu.connected ? "secondary" : "outline"}>{feishu.connected ? "已连接" : "连接中"}</Badge></CardAction></CardHeader><CardContent><p className="text-sm text-muted-foreground">{feishuEnabled ? "已绑定 · 可在飞书中查看状态、切卡和恢复设备" : "尚未绑定"}</p></CardContent></Card> : null}
            <Card><CardHeader><CardTitle>{feishu?.configured ? "其他通知渠道" : "通知渠道"}</CardTitle><CardDescription>按需添加额外的短信转发渠道</CardDescription></CardHeader><CardContent>{notificationEditor}</CardContent><CardFooter className="flex-wrap justify-between gap-3"><span className="text-xs text-muted-foreground">{configured ? `已启用：${status?.notifications?.configured_labels.join("、")}` : "未启用其他渠道"}</span><Button variant="outline" size="sm" disabled={busy || !configured || !messages.length} onClick={onResendNotification}><RefreshCwIcon />重新推送到这些渠道</Button></CardFooter></Card>
          </TabsContent>

          <TabsContent value="device" className="space-y-5">
            <div className="grid items-start gap-5 md:grid-cols-2">
              <Card><CardHeader><CardTitle>设备状态</CardTitle></CardHeader><CardContent><dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-4 text-sm"><dt className="text-muted-foreground">模组连接</dt><dd className="text-right">{online ? "已连接" : "未连接"}</dd><dt className="text-muted-foreground">后台收信</dt><dd className="text-right">{status?.services.sms_forwarder === "active" ? "运行中 · 每 5 秒读取" : "未运行"}</dd><dt className="text-muted-foreground">已保存短信</dt><dd className="text-right">{messages.length} 条</dd><dt className="text-muted-foreground">网页刷新</dt><dd className="text-right">{autoRefresh ? `每 ${refreshSeconds} 秒` : "已暂停"}</dd></dl></CardContent><CardFooter className="gap-2"><Button variant="outline" disabled={busy || !status} onClick={onRefreshInbox}><RefreshCwIcon />重新读取设备</Button><Button variant="outline" disabled={busy || !status?.capabilities.modem_recovery_enabled} onClick={onRecover} title="重新初始化模组，连接会短暂中断；保留当前卡片">恢复设备</Button></CardFooter></Card>
              <Card><CardHeader><CardTitle>网络与信号</CardTitle></CardHeader><CardContent><dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-3 text-sm">{[["PLMN", modem?.operator_code], ["注册状态", online ? registration : "未注册"], ["网络制式", modem?.access_tech], ["RSRP", signal?.rsrp_text], ["RSRQ", signal?.rsrq_text], ["RSSI", signal?.rssi_text], ["CSQ", signal?.csq == null ? null : `${signal.csq} / 31`]].map(([label, value]) => <div key={label} className="contents"><dt className="text-muted-foreground">{label}</dt><dd className="text-right tabular-nums">{value || "未测得"}</dd></div>)}</dl></CardContent><CardFooter><p className="text-xs text-muted-foreground">手动选网、APN 和短信中心写入尚未适配当前设备。</p></CardFooter></Card>
            </div>
            <Card><CardHeader><CardTitle className="flex items-center gap-2"><TerminalIcon className="size-4" />操作日志</CardTitle><CardDescription>{logSource === "device" ? "最近的切卡和恢复记录，刷新页面后保留" : "本次打开页面后的操作记录"}</CardDescription><CardAction className="flex gap-2"><Select value={logSource} onValueChange={(value) => setLogSource(value ?? "device")}><SelectTrigger aria-label="日志范围" className="w-32"><SelectValue>{logSource === "device" ? "设备记录" : "本页操作"}</SelectValue></SelectTrigger><SelectContent><SelectItem value="device">设备记录</SelectItem><SelectItem value="page">本页操作</SelectItem></SelectContent></Select>{logSource === "page" ? <Button variant="outline" size="sm" disabled={!logs.length || busy} onClick={onClearLogs}>清空显示</Button> : null}</CardAction></CardHeader><CardContent>{visibleLogs.length ? <div className="max-h-80 space-y-3 overflow-auto rounded-lg bg-muted/50 p-4 font-mono text-xs">{visibleLogs.map((line, index) => <div key={index} className="grid grid-cols-[4.5rem_minmax(0,1fr)] gap-3"><span className="text-muted-foreground">{line.time}</span><span className={cn("whitespace-pre-wrap break-words", line.level === "error" && "text-destructive")}>{line.message}</span></div>)}</div> : <p className="py-6 text-center text-sm text-muted-foreground">暂无操作记录</p>}</CardContent></Card>
          </TabsContent>
        </Tabs>
      </main>
      <Toaster position="top-right" />
    </div>
  )
}
