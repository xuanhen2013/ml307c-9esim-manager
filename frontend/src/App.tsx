import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react"
import {
  AlertCircleIcon,
  ArrowRightIcon,
  BadgeCheckIcon,
  ChevronDownIcon,
  LoaderCircleIcon,
  MessageSquareTextIcon,
  RadioTowerIcon,
  RefreshCwIcon,
  RouterIcon,
  SendIcon,
  Settings2Icon,
  ShieldAlertIcon,
  SignalIcon,
  CardSimIcon,
  TerminalSquareIcon,
  WifiIcon,
} from "lucide-react"
import { Toaster, toast } from "sonner"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { cn } from "@/lib/utils"

type Profile = {
  iccid: string
  display_name: string
  provider_name?: string
  is_active?: boolean
  iccid_short?: string
  state?: string
}

type SmsItem = {
  id: string
  number: string
  text: string
  timestamp: string
  state: string
  state_label: string
}

type StatusData = {
  profiles: Profile[]
  modem_available: boolean
  status_message: string
  errors: string[]
  modem: {
    number: string
    operator_code: string
    operator_name: string
    registration: string
    state: string
    signal: string
    access_tech: string
    current_modes: string
    apn: string
    ip_type: string
  }
  connection: {
    apn: string
    username: string
    password?: string
    ip_type: string
    network_id: string
  }
  services: {
    modemmanager: string
    sms_forwarder: string
    web_admin: string
  }
  bark: {
    base_url: string
    device_key: string
    group: string
    level: string
  }
  sms: SmsItem[]
  timestamp: string
}

type ActionLevel = "info" | "warning" | "error" | "command"

type ActionEvent = {
  time: string
  level: ActionLevel
  message: string
}

type ActionState = "queued" | "running" | "done" | "error"

type ActionName =
  | "switch_profile"
  | "recover_modem"
  | "restart_sms"
  | "resend_last_sms"
  | "save_apn"
  | "save_bark"
  | "apply_radio_mode"
  | "apply_network_selection"

type ActionSnapshot = {
  ok: boolean
  id: string
  action: ActionName
  state: ActionState
  events: ActionEvent[]
  cursor: number
  message: string
  error: string
  status?: StatusData
}

type PersistedAction = {
  id: string
  action: ActionName
  label: string
  cursor: number
  target?: string
}

type BarkFormState = {
  base_url: string
  device_key: string
  group: string
  level: string
}

type ApnFormState = {
  apn: string
  username: string
  password: string
  ip_type: string
}

const ACTIVE_ACTION_KEY = "ess-active-action"

async function requestJson<T>(input: string, init?: RequestInit): Promise<T> {
  const response = await fetch(input, {
    headers: { "Content-Type": "application/json" },
    ...init,
  })
  const payload = (await response.json().catch(() => ({}))) as Record<string, unknown>
  if (!response.ok) {
    throw new Error(String(payload.error ?? `请求失败：${response.status}`))
  }
  return payload as T
}

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}

function inferRadioMode(currentModes: string) {
  const normalized = currentModes.toLowerCase()
  if (normalized.includes("4g") && !normalized.includes("3g")) return "4g_only"
  if (normalized.includes("3g") && !normalized.includes("4g")) return "3g_only"
  return "3g4g_prefer4g"
}

function formatRegistrationState(registration: string) {
  const normalized = registration.trim().toLowerCase()
  const labels: Record<string, string> = {
    home: "本地网络",
    roaming: "漫游",
    searching: "搜索中",
    denied: "被拒绝",
    unknown: "未知",
    registered: "已注册",
    idle: "空闲",
  }
  return labels[normalized] || registration || "--"
}

function formatAccessTech(accessTech: string) {
  const normalized = accessTech.trim().toLowerCase()
  const labels: Record<string, string> = {
    lte: "LTE",
    nr5g: "5G NR",
    "5gnr": "5G NR",
    gsm: "GSM",
    umts: "UMTS",
    edge: "EDGE",
    gprs: "GPRS",
  }
  return labels[normalized] || accessTech.toUpperCase() || "--"
}

function formatCurrentModes(currentModes: string) {
  const normalized = currentModes.trim()
  if (!normalized || normalized === "--") {
    return "允许制式：--\n首选制式：--"
  }
  const allowedMatch = normalized.match(/allowed:\s*([^;]+)/i)
  const preferredMatch = normalized.match(/preferred:\s*([^;]+)/i)
  const allowed = allowedMatch?.[1]?.trim() || normalized
  const preferred = preferredMatch?.[1]?.trim() || "none"
  const formatMode = (value: string) => {
    const lower = value.toLowerCase()
    if (lower === "none") return "无"
    return value.toUpperCase()
  }
  return `允许制式：${formatMode(allowed)}\n首选制式：${formatMode(preferred)}`
}

function serviceVariant(state: string) {
  if (state === "active") return "default" as const
  if (state === "activating") return "secondary" as const
  return "destructive" as const
}

function signalVariant(signalValue: string) {
  const signal = Number.parseInt(signalValue, 10)
  if (Number.isNaN(signal)) return "outline" as const
  if (signal >= 60) return "default" as const
  if (signal >= 30) return "secondary" as const
  return "destructive" as const
}

function maskDeviceKey(deviceKey: string) {
  if (!deviceKey) return "未配置"
  if (deviceKey.length <= 8) return deviceKey
  return `${deviceKey.slice(0, 4)}...${deviceKey.slice(-4)}`
}

function getActiveProfile(profiles: Profile[]) {
  return profiles.find((profile) => profile.is_active) ?? null
}

function levelClassName(level: ActionLevel) {
  if (level === "error") return "text-rose-300"
  if (level === "warning") return "text-amber-300"
  if (level === "command") return "text-cyan-300"
  return "text-slate-100"
}

function friendlyActionName(action: ActionName) {
  switch (action) {
    case "switch_profile":
      return "切换 eSIM"
    case "recover_modem":
      return "重启基带"
    case "restart_sms":
      return "重启短信转发"
    case "resend_last_sms":
      return "重发最后一条短信"
    case "save_apn":
      return "保存 APN"
    case "save_bark":
      return "保存 Bark"
    case "apply_radio_mode":
      return "应用网络制式"
    case "apply_network_selection":
      return "应用选网设置"
  }
}

function App() {
  const [status, setStatus] = useState<StatusData | null>(null)
  const [logs, setLogs] = useState<ActionEvent[]>([])
  const [isLoadingStatus, setIsLoadingStatus] = useState(true)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [activeAction, setActiveAction] = useState<PersistedAction | null>(null)
  const [submittingActionLabel, setSubmittingActionLabel] = useState<string | null>(null)
  const [barkForm, setBarkForm] = useState<BarkFormState>({
    base_url: "",
    device_key: "",
    group: "sms",
    level: "active",
  })
  const [apnForm, setApnForm] = useState<ApnFormState>({
    apn: "",
    username: "",
    password: "",
    ip_type: "ipv4v6",
  })
  const [networkCode, setNetworkCode] = useState("")
  const [radioMode, setRadioMode] = useState("3g4g_prefer4g")
  const [advancedOpen, setAdvancedOpen] = useState(false)

  const barkDirtyRef = useRef(false)
  const apnDirtyRef = useRef(false)
  const networkDirtyRef = useRef(false)
  const radioModeDirtyRef = useRef(false)
  const pollTokenRef = useRef(0)

  const appendLog = useCallback((event: ActionEvent) => {
    setLogs((current) => [...current.slice(-199), event])
  }, [])

  const syncFormsFromStatus = useCallback((snapshot: StatusData) => {
    if (!barkDirtyRef.current) {
      setBarkForm({
        base_url: snapshot.bark.base_url,
        device_key: snapshot.bark.device_key,
        group: snapshot.bark.group,
        level: snapshot.bark.level,
      })
    }
    if (!apnDirtyRef.current) {
      setApnForm({
        apn: snapshot.connection.apn,
        username: snapshot.connection.username,
        password: snapshot.connection.password ?? "",
        ip_type: snapshot.connection.ip_type || "ipv4v6",
      })
    }
    if (!networkDirtyRef.current) setNetworkCode(snapshot.connection.network_id)
    if (!radioModeDirtyRef.current) setRadioMode(inferRadioMode(snapshot.modem.current_modes))
  }, [])

  const refreshStatus = useCallback(async (silent = false, refreshProfiles = false) => {
    if (!silent) setIsRefreshing(true)
    try {
      const snapshot = await requestJson<StatusData>(
        refreshProfiles ? "/api/status?refresh_profiles=1" : "/api/status",
      )
      setStatus(snapshot)
      syncFormsFromStatus(snapshot)
    } catch (error) {
      if (!silent) toast.error(error instanceof Error ? error.message : "刷新状态失败")
    } finally {
      setIsLoadingStatus(false)
      if (!silent) setIsRefreshing(false)
    }
  }, [syncFormsFromStatus])

  const finishAction = useCallback((snapshot: ActionSnapshot, currentAction: PersistedAction) => {
    if (snapshot.status) {
      setStatus(snapshot.status!)
      syncFormsFromStatus(snapshot.status)
    }
    window.localStorage.removeItem(ACTIVE_ACTION_KEY)
    setActiveAction(null)
    setSubmittingActionLabel(null)
    barkDirtyRef.current = false
    apnDirtyRef.current = false
    networkDirtyRef.current = false
    radioModeDirtyRef.current = false
    if (snapshot.state === "done") {
      toast.success(`${currentAction.label}已完成`)
      return
    }
    toast.error(snapshot.error || `${currentAction.label}失败`)
  }, [syncFormsFromStatus])

  const pollAction = useCallback(async (persisted: PersistedAction) => {
    const token = ++pollTokenRef.current
    let cursor = persisted.cursor
    while (pollTokenRef.current === token) {
      try {
        const snapshot = await requestJson<ActionSnapshot>(`/api/action/${persisted.id}?cursor=${cursor}`)
        if (snapshot.events.length > 0) {
          for (const event of snapshot.events) appendLog(event)
          cursor = snapshot.cursor
          const nextAction = { ...persisted, cursor }
          setActiveAction(nextAction)
          window.localStorage.setItem(ACTIVE_ACTION_KEY, JSON.stringify(nextAction))
          persisted = nextAction
        }
        if (snapshot.state === "done" || snapshot.state === "error") {
          finishAction(snapshot, persisted)
          return
        }
      } catch (error) {
        appendLog({
          time: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
          level: "error",
          message: error instanceof Error ? error.message : "轮询任务状态失败",
        })
        toast.error("任务状态同步失败")
        return
      }
      await sleep(700)
    }
  }, [appendLog, finishAction])

  const runAction = useCallback(async (action: ActionName, payload: Record<string, string>, label: string) => {
    if (activeAction || submittingActionLabel) {
      toast.info("当前已有任务在执行，请稍等")
      return
    }
    setSubmittingActionLabel(label)
    appendLog({
      time: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
      level: "info",
      message: `准备执行：${label}`,
    })
    try {
      const response = await requestJson<{ ok: true; id: string }>("/api/action/start", {
        method: "POST",
        body: JSON.stringify({ action, payload }),
      })
      const persisted: PersistedAction = {
        id: response.id,
        action,
        label,
        cursor: 0,
        target: payload.iccid || payload.operator_code || "",
      }
      setActiveAction(persisted)
      setSubmittingActionLabel(null)
      window.localStorage.setItem(ACTIVE_ACTION_KEY, JSON.stringify(persisted))
      appendLog({
        time: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
        level: "info",
        message: `任务已提交：${label}（${response.id}）`,
      })
      void pollAction(persisted)
    } catch (error) {
      setSubmittingActionLabel(null)
      const message = error instanceof Error ? error.message : "提交任务失败"
      appendLog({
        time: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
        level: "error",
        message,
      })
      toast.error(message)
    }
  }, [activeAction, appendLog, pollAction, submittingActionLabel])

  useEffect(() => {
    void refreshStatus(true)
    const persistedRaw = window.localStorage.getItem(ACTIVE_ACTION_KEY)
    if (!persistedRaw) return
    try {
      const persisted = JSON.parse(persistedRaw) as PersistedAction
      setActiveAction(persisted)
      appendLog({
        time: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
        level: "info",
        message: `已恢复任务追踪：${persisted.label}`,
      })
      void pollAction(persisted)
    } catch {
      window.localStorage.removeItem(ACTIVE_ACTION_KEY)
    }
  }, [appendLog, pollAction, refreshStatus])

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      if (!autoRefresh || activeAction) return
      void refreshStatus(true)
    }, 10000)
    return () => {
      window.clearInterval(intervalId)
    }
  }, [activeAction, autoRefresh, refreshStatus])

  useEffect(() => {
    return () => {
      pollTokenRef.current += 1
    }
  }, [])

  const activeProfile = getActiveProfile(status?.profiles ?? [])
  const actionBusy = Boolean(activeAction || submittingActionLabel)
  const shellActionLabel = activeAction?.label || submittingActionLabel

  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top,_rgba(14,165,233,0.18),_transparent_30%),linear-gradient(180deg,_#f7f9fc_0%,_#eef3f7_100%)]">
      <div className="mx-auto flex min-h-screen w-full max-w-7xl flex-col gap-4 px-4 py-4 sm:px-6 lg:px-8">
        <Card className="border-white/60 bg-white/85 backdrop-blur">
          <CardHeader className="gap-3">
            <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
              <div className="flex flex-col gap-2">
                <div className="flex items-center gap-2">
                  <Badge variant="secondary">ESMF 控制台</Badge>
                  <Badge variant={status?.modem_available ? "default" : "destructive"}>
                    {status?.modem_available ? "基带在线" : "基带离线"}
                  </Badge>
                  {activeAction ? (
                    <Badge variant="outline">{friendlyActionName(activeAction.action)}</Badge>
                  ) : null}
                </div>
                <CardTitle className="text-2xl sm:text-3xl">eSIM SMS Forwarder</CardTitle>
                <CardDescription className="max-w-3xl">
                  eSIM 管理与短信转发
                </CardDescription>
              </div>
              <div className="flex flex-col gap-3 sm:items-end">
                <div className="flex items-center gap-3 rounded-xl border border-border/70 bg-background/80 px-3 py-2">
                  <div className="flex items-center gap-2">
                    <Switch checked={autoRefresh} onCheckedChange={setAutoRefresh} aria-label="自动刷新" />
                    <Label>自动刷新</Label>
                  </div>
                  <Separator orientation="vertical" className="h-5" />
                  <span className="text-sm text-muted-foreground">
                    {status?.timestamp ? `最后刷新 ${status.timestamp}` : "等待首次刷新"}
                  </span>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => {
                      void refreshStatus(false, true)
                    }}
                    disabled={isRefreshing}
                  >
                    <RefreshCwIcon data-icon="inline-start" className={cn(isRefreshing && "animate-spin")} />
                    {isRefreshing ? "刷新中" : "刷新状态"}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    disabled={actionBusy}
                    onClick={() => {
                      void runAction("recover_modem", {}, "重启基带")
                    }}
                  >
                    <RouterIcon data-icon="inline-start" />
                    重启基带
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    disabled={actionBusy}
                    onClick={() => {
                      void runAction("restart_sms", {}, "重启短信转发")
                    }}
                  >
                    <SendIcon data-icon="inline-start" />
                    重启转发
                  </Button>
                </div>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
              <OverviewTile
                icon={CardSimIcon}
                label="当前 Profile"
                value={activeProfile?.display_name || "未检测到"}
                hint={`手机号：${status?.modem.number || "--"}`}
              />
              <OverviewTile
                icon={RadioTowerIcon}
                label="运营商"
                value={status?.modem.operator_name || "--"}
                hint={`${status?.modem.operator_code || "--"} · ${formatRegistrationState(status?.modem.registration || "--")}`}
              />
              <OverviewTile
                icon={SignalIcon}
                label="信号与制式"
                value={`${status?.modem.signal || "--"}%`}
                hint={`${formatAccessTech(status?.modem.access_tech || "--")}\n${formatCurrentModes(status?.modem.current_modes || "--")}`}
                badgeVariant={signalVariant(status?.modem.signal || "--")}
              />
              <OverviewTile
                icon={WifiIcon}
                label="短信转发"
                value={status?.services.sms_forwarder || "--"}
                hint={`Bark ${maskDeviceKey(status?.bark.device_key || "")}`}
                badgeVariant={serviceVariant(status?.services.sms_forwarder || "")}
              />
            </div>
          </CardContent>
        </Card>

        {status?.status_message || (status?.errors?.length ?? 0) > 0 ? (
          <Alert variant={status?.modem_available ? "default" : "destructive"} className="bg-white/85 backdrop-blur">
            {status?.modem_available ? <AlertCircleIcon /> : <ShieldAlertIcon />}
            <AlertTitle>{status?.status_message || "当前设备有告警信息"}</AlertTitle>
            <AlertDescription>
              {(status?.errors ?? []).length > 0
                ? status?.errors.join("；")
                : "状态接口已返回，但部分子模块可能还在重连。"}
            </AlertDescription>
          </Alert>
        ) : null}

        <div className="grid gap-4 xl:grid-cols-2">
          <Card className="h-[30rem] border-white/60 bg-white/85 backdrop-blur">
            <CardHeader>
              <div className="flex items-start justify-between gap-3">
                <div className="flex flex-col gap-1">
                  <CardTitle className="flex items-center gap-2">
                    <CardSimIcon />
                    eSIM Profiles
                  </CardTitle>
                  <CardDescription>自动跟随状态刷新，新增或变更 Profile 后会自动显示。</CardDescription>
                </div>
                <CardAction>
                  <Badge variant="outline">{status?.profiles.length ?? 0} 个</Badge>
                </CardAction>
              </div>
            </CardHeader>
            <CardContent className="h-full pb-4">
              <ScrollArea className="h-[22.5rem] rounded-xl border border-border/70 bg-background/70">
                <div className="flex flex-col gap-3 p-3">
                  {isLoadingStatus ? (
                    <EmptyState
                      icon={LoaderCircleIcon}
                      title="正在读取设备状态"
                      description="首次加载会顺带读取 eSIM、短信和基带信息。"
                      spinning
                    />
                  ) : status?.profiles.length ? (
                    status.profiles.map((profile) => {
                      const isCurrent = Boolean(profile.is_active)
                      const isSwitching =
                        activeAction?.action === "switch_profile" && activeAction.target === profile.iccid
                      return (
                        <div
                          key={profile.iccid}
                          className={cn(
                            "rounded-2xl border p-4 shadow-sm transition-colors",
                            isCurrent ? "border-sky-300 bg-sky-50/80" : "border-border/70 bg-white/90",
                          )}
                        >
                          <div className="flex flex-col gap-3">
                            <div className="flex items-start justify-between gap-3">
                              <div className="flex flex-col gap-1">
                                <div className="flex items-center gap-2">
                                  <h3 className="text-base font-medium">{profile.display_name}</h3>
                                  {isCurrent ? (
                                    <Badge>
                                      <BadgeCheckIcon data-icon="inline-start" />
                                      当前使用
                                    </Badge>
                                  ) : (
                                    <Badge variant="outline">待机</Badge>
                                  )}
                                </div>
                                <p className="text-sm text-muted-foreground">
                                  手机号：{isCurrent ? status?.modem.number || "--" : "--"}
                                </p>
                              </div>
                              <Button
                                type="button"
                                size="sm"
                                variant={isCurrent ? "secondary" : "default"}
                                disabled={actionBusy || isCurrent}
                                onClick={() => {
                                  void runAction(
                                    "switch_profile",
                                    { iccid: profile.iccid },
                                    `切换到 ${profile.display_name}`,
                                  )
                                }}
                              >
                                {isSwitching ? (
                                  <LoaderCircleIcon data-icon="inline-start" className="animate-spin" />
                                ) : (
                                  <ArrowRightIcon data-icon="inline-start" />
                                )}
                                {isCurrent ? "当前使用中" : "切换到此卡"}
                              </Button>
                            </div>
                            <div className="grid gap-2 text-sm text-muted-foreground sm:grid-cols-2">
                              <span>ICCID：{profile.iccid || "--"}</span>
                              <span>状态：{profile.state || (isCurrent ? "enabled" : "--")}</span>
                            </div>
                          </div>
                        </div>
                      )
                    })
                  ) : (
                    <EmptyState
                      icon={CardSimIcon}
                      title="还没有读到 Profile"
                      description="检查 lpac-switch 是否可用，或者先点一次刷新状态。"
                    />
                  )}
                </div>
              </ScrollArea>
            </CardContent>
          </Card>

          <Card className="h-[30rem] border-white/60 bg-white/85 backdrop-blur">
            <CardHeader>
              <div className="flex items-start justify-between gap-3">
                <div className="flex flex-col gap-1">
                  <CardTitle className="flex items-center gap-2">
                    <MessageSquareTextIcon />
                    最近短信
                  </CardTitle>
                  <CardDescription>按最近收到的顺序显示，支持中文转义和 Base64 文本自动还原。</CardDescription>
                </div>
                <CardAction>
                  <div className="flex flex-col items-end gap-2">
                    <Badge variant="outline">{status?.sms.length ?? 0} 条</Badge>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={actionBusy || !(status?.sms.length ?? 0)}
                      onClick={() => {
                        void runAction("resend_last_sms", {}, "重发最后一条短信")
                      }}
                    >
                      <SendIcon data-icon="inline-start" />
                      重发最后一条短信
                    </Button>
                  </div>
                </CardAction>
              </div>
            </CardHeader>
            <CardContent className="h-full pb-4">
              <ScrollArea className="h-[22.5rem] rounded-xl border border-border/70 bg-background/70">
                <div className="flex flex-col gap-3 p-3">
                  {status?.sms.length ? (
                    status.sms.map((sms) => (
                      <div
                        key={`${sms.id}-${sms.timestamp}`}
                        className="rounded-2xl border border-border/70 bg-white/90 p-4 shadow-sm"
                      >
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-medium">{sms.number || "未知号码"}</span>
                          <Badge variant="secondary">{sms.state_label}</Badge>
                          <Badge variant="outline">{sms.timestamp}</Badge>
                        </div>
                        <p className="mt-3 whitespace-pre-wrap break-words text-sm leading-6 text-foreground/90">
                          {sms.text || "短信正文为空"}
                        </p>
                      </div>
                    ))
                  ) : (
                    <EmptyState
                      icon={MessageSquareTextIcon}
                      title="最近还没有短信"
                      description="收到短信后会自动出现在这里，并参与 Bark 转发。"
                    />
                  )}
                </div>
              </ScrollArea>
            </CardContent>
          </Card>
        </div>

        <Card className="border-white/60 bg-slate-950 text-slate-100 shadow-xl">
          <CardHeader>
            <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
              <div className="flex flex-col gap-1">
                <CardTitle className="flex items-center gap-2 text-slate-50">
                  <TerminalSquareIcon />
                  Shell 执行面板
                </CardTitle>
                <CardDescription className="text-slate-400">
                  每个任务都会把当前步骤同步到这里，页面关闭后重新打开也会尝试恢复追踪。
                </CardDescription>
              </div>
              <div className="flex items-center gap-2">
                {shellActionLabel ? (
                  <Badge variant="outline" className="border-sky-400/40 text-sky-200">
                    <LoaderCircleIcon data-icon="inline-start" className="animate-spin" />
                    {shellActionLabel}
                  </Badge>
                ) : (
                  <Badge variant="outline" className="border-slate-700 text-slate-300">
                    空闲
                  </Badge>
                )}
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="text-slate-100 hover:bg-slate-800 hover:text-white"
                  onClick={() => {
                    setLogs([])
                  }}
                >
                  清空日志
                </Button>
              </div>
            </div>
          </CardHeader>
          <CardContent className="pb-4">
            <ScrollArea className="h-[19rem] rounded-xl border border-slate-800 bg-slate-950/80">
              <div className="flex min-h-full flex-col gap-2 p-3 font-mono text-sm">
                {logs.length ? (
                  logs.map((line, index) => (
                    <div key={`${line.time}-${index}`} className="grid grid-cols-[80px_1fr] gap-3">
                      <span className="text-slate-400">{line.time}</span>
                      <span className={cn("whitespace-pre-wrap break-words", levelClassName(line.level))}>
                        {line.message}
                      </span>
                    </div>
                  ))
                ) : (
                  <div className="flex h-full min-h-[14rem] items-center justify-center text-slate-500">
                    暂时还没有任务日志，点任意操作后这里会实时显示执行进度。
                  </div>
                )}
              </div>
            </ScrollArea>
          </CardContent>
        </Card>

        <Card className="border-white/60 bg-white/85 backdrop-blur">
          <CardHeader>
            <div className="flex items-start justify-between gap-3">
              <div className="flex flex-col gap-1">
                <CardTitle className="flex items-center gap-2">
                  <Settings2Icon />
                  高级设置
                </CardTitle>
                <CardDescription>APN、选网、网络制式和 Bark 都放在这里，避免主界面出现空白区。</CardDescription>
              </div>
              <CardAction>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => {
                    setAdvancedOpen((current) => !current)
                  }}
                >
                  <ChevronDownIcon
                    data-icon="inline-end"
                    className={cn("transition-transform", advancedOpen && "rotate-180")}
                  />
                  {advancedOpen ? "收起" : "展开"}
                </Button>
              </CardAction>
            </div>
          </CardHeader>
          {advancedOpen ? (
          <CardContent>
            <Tabs defaultValue="network" className="gap-4">
              <TabsList variant="line">
                <TabsTrigger value="network">网络</TabsTrigger>
                <TabsTrigger value="forwarder">转发</TabsTrigger>
              </TabsList>

              <TabsContent value="network" className="flex flex-col gap-5">
                <div className="grid gap-5 xl:grid-cols-[1.2fr_0.8fr]">
                  <div className="rounded-2xl border border-border/70 bg-background/70 p-4">
                    <div className="mb-4 flex items-center gap-2">
                      <RadioTowerIcon className="text-muted-foreground" />
                      <div>
                        <h3 className="font-medium">APN 与承载参数</h3>
                        <p className="text-sm text-muted-foreground">
                          不预置 giffgaff 或 T-Mobile 模板，直接按当前卡需要填写。
                        </p>
                      </div>
                    </div>
                    <div className="grid gap-4">
                      <div className="grid gap-2">
                        <Label htmlFor="apn">APN</Label>
                        <Input
                          id="apn"
                          value={apnForm.apn}
                          onChange={(event) => {
                            apnDirtyRef.current = true
                            setApnForm((current) => ({ ...current, apn: event.target.value }))
                          }}
                          placeholder="例如 fast.t-mobile.com"
                        />
                      </div>
                      <div className="grid gap-4 md:grid-cols-2">
                        <div className="grid gap-2">
                          <Label htmlFor="apn-username">用户名</Label>
                          <Input
                            id="apn-username"
                            value={apnForm.username}
                            onChange={(event) => {
                              apnDirtyRef.current = true
                              setApnForm((current) => ({ ...current, username: event.target.value }))
                            }}
                            placeholder="可留空"
                          />
                        </div>
                        <div className="grid gap-2">
                          <Label htmlFor="apn-password">密码</Label>
                          <Input
                            id="apn-password"
                            type="password"
                            value={apnForm.password}
                            onChange={(event) => {
                              apnDirtyRef.current = true
                              setApnForm((current) => ({ ...current, password: event.target.value }))
                            }}
                            placeholder="可留空"
                          />
                        </div>
                      </div>
                      <div className="grid gap-2 md:max-w-xs">
                        <Label>IP 类型</Label>
                        <Select
                          value={apnForm.ip_type}
                          onValueChange={(value) => {
                            apnDirtyRef.current = true
                            setApnForm((current) => ({ ...current, ip_type: value ?? current.ip_type }))
                          }}
                        >
                          <SelectTrigger className="w-full">
                            <SelectValue placeholder="选择 IP 类型" />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectGroup>
                              <SelectLabel>承载模式</SelectLabel>
                              <SelectItem value="ipv4">IPv4</SelectItem>
                              <SelectItem value="ipv6">IPv6</SelectItem>
                              <SelectItem value="ipv4v6">IPv4 / IPv6</SelectItem>
                            </SelectGroup>
                          </SelectContent>
                        </Select>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <Button
                          type="button"
                          disabled={actionBusy}
                          onClick={() => {
                            void runAction("save_apn", apnForm, "保存 APN 配置")
                          }}
                        >
                          <SendIcon data-icon="inline-start" />
                          套用并保存
                        </Button>
                        <Button
                          type="button"
                          variant="outline"
                          disabled={!status}
                          onClick={() => {
                            if (!status) return
                            apnDirtyRef.current = false
                            syncFormsFromStatus(status)
                          }}
                        >
                          恢复当前状态
                        </Button>
                      </div>
                    </div>
                  </div>

                  <div className="flex flex-col gap-5">
                    <div className="rounded-2xl border border-border/70 bg-background/70 p-4">
                      <div className="mb-4 flex items-center gap-2">
                        <SignalIcon className="text-muted-foreground" />
                        <div>
                          <h3 className="font-medium">网络制式</h3>
                          <p className="whitespace-pre-line text-sm text-muted-foreground">
                            {`${formatAccessTech(status?.modem.access_tech || "--")}\n${formatCurrentModes(status?.modem.current_modes || "--")}`}
                          </p>
                        </div>
                      </div>
                      <div className="grid gap-3">
                        <Select
                          value={radioMode}
                          onValueChange={(value) => {
                            radioModeDirtyRef.current = true
                            setRadioMode(value ?? "3g4g_prefer4g")
                          }}
                        >
                          <SelectTrigger className="w-full">
                            <SelectValue placeholder="选择网络制式" />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectGroup>
                              <SelectLabel>网络制式</SelectLabel>
                              <SelectItem value="4g_only">仅 4G</SelectItem>
                              <SelectItem value="3g4g_prefer4g">3G / 4G，优先 4G</SelectItem>
                              <SelectItem value="3g_only">仅 3G</SelectItem>
                            </SelectGroup>
                          </SelectContent>
                        </Select>
                        <Button
                          type="button"
                          variant="outline"
                          disabled={actionBusy}
                          onClick={() => {
                            void runAction("apply_radio_mode", { mode: radioMode }, "应用网络制式")
                          }}
                        >
                          应用制式
                        </Button>
                      </div>
                    </div>

                    <div className="rounded-2xl border border-border/70 bg-background/70 p-4">
                      <div className="mb-4 flex items-center gap-2">
                        <RouterIcon className="text-muted-foreground" />
                        <div>
                          <h3 className="font-medium">选网与注册</h3>
                          <p className="text-sm text-muted-foreground">
                            当前网络代码：{status?.connection.network_id || "自动"}
                          </p>
                        </div>
                      </div>
                      <div className="grid gap-3">
                        <Input
                          value={networkCode}
                          onChange={(event) => {
                            networkDirtyRef.current = true
                            setNetworkCode(event.target.value)
                          }}
                          placeholder="例如 46000"
                        />
                        <div className="flex flex-wrap gap-2">
                          <Button
                            type="button"
                            variant="outline"
                            disabled={actionBusy || !networkCode.trim()}
                            onClick={() => {
                              void runAction(
                                "apply_network_selection",
                                { operator_code: networkCode.trim() },
                                `手动选网 ${networkCode.trim()}`,
                              )
                            }}
                          >
                            手动选网
                          </Button>
                          <Button
                            type="button"
                            variant="outline"
                            disabled={actionBusy}
                            onClick={() => {
                              void runAction("apply_network_selection", { operator_code: "" }, "恢复自动选网")
                            }}
                          >
                            恢复自动选网
                          </Button>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </TabsContent>

              <TabsContent value="forwarder" className="flex flex-col gap-5">
                <div className="grid gap-5 xl:grid-cols-[1.1fr_0.9fr]">
                  <div className="rounded-2xl border border-border/70 bg-background/70 p-4">
                    <div className="mb-4 flex items-center gap-2">
                      <SendIcon className="text-muted-foreground" />
                      <div>
                        <h3 className="font-medium">Bark 推送配置</h3>
                        <p className="text-sm text-muted-foreground">保存后会自动重启短信转发服务。</p>
                      </div>
                    </div>
                    <div className="grid gap-4">
                      <div className="grid gap-2">
                        <Label htmlFor="bark-url">Bark 地址</Label>
                        <Input
                          id="bark-url"
                          value={barkForm.base_url}
                          onChange={(event) => {
                            barkDirtyRef.current = true
                            setBarkForm((current) => ({ ...current, base_url: event.target.value }))
                          }}
                          placeholder="https://example.com"
                        />
                      </div>
                      <div className="grid gap-2">
                        <Label htmlFor="bark-key">Device Key</Label>
                        <Input
                          id="bark-key"
                          value={barkForm.device_key}
                          onChange={(event) => {
                            barkDirtyRef.current = true
                            setBarkForm((current) => ({ ...current, device_key: event.target.value }))
                          }}
                          placeholder="输入 Bark 的 key"
                        />
                      </div>
                      <div className="grid gap-4 md:grid-cols-2">
                        <div className="grid gap-2">
                          <Label htmlFor="bark-group">分组</Label>
                          <Input
                            id="bark-group"
                            value={barkForm.group}
                            onChange={(event) => {
                              barkDirtyRef.current = true
                              setBarkForm((current) => ({ ...current, group: event.target.value }))
                            }}
                            placeholder="sms"
                          />
                        </div>
                        <div className="grid gap-2">
                          <Label>推送级别</Label>
                          <Select
                            value={barkForm.level}
                            onValueChange={(value) => {
                              barkDirtyRef.current = true
                              setBarkForm((current) => ({ ...current, level: value ?? current.level }))
                            }}
                          >
                            <SelectTrigger className="w-full">
                              <SelectValue placeholder="选择 Bark 级别" />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectGroup>
                                <SelectLabel>Bark 级别</SelectLabel>
                                <SelectItem value="active">active</SelectItem>
                                <SelectItem value="timeSensitive">timeSensitive</SelectItem>
                                <SelectItem value="passive">passive</SelectItem>
                              </SelectGroup>
                            </SelectContent>
                          </Select>
                        </div>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <Button
                          type="button"
                          disabled={actionBusy}
                          onClick={() => {
                            void runAction("save_bark", barkForm, "保存 Bark 配置")
                          }}
                        >
                          <SendIcon data-icon="inline-start" />
                          保存 Bark
                        </Button>
                        <Button
                          type="button"
                          variant="outline"
                          disabled={!status}
                          onClick={() => {
                            if (!status) return
                            barkDirtyRef.current = false
                            syncFormsFromStatus(status)
                          }}
                        >
                          恢复当前状态
                        </Button>
                      </div>
                    </div>
                  </div>

                  <div className="rounded-2xl border border-border/70 bg-background/70 p-4">
                    <div className="mb-4 flex items-center gap-2">
                      <WifiIcon className="text-muted-foreground" />
                      <div>
                        <h3 className="font-medium">服务状态</h3>
                        <p className="text-sm text-muted-foreground">方便确认 Bark 配置和后台服务是否都在线。</p>
                      </div>
                    </div>
                    <div className="grid gap-3">
                      <ServiceLine name="ModemManager" state={status?.services.modemmanager || "--"} />
                      <ServiceLine name="短信转发" state={status?.services.sms_forwarder || "--"} />
                      <ServiceLine name="管理页面" state={status?.services.web_admin || "--"} />
                      <Separator />
                      <div className="grid gap-1 text-sm text-muted-foreground">
                        <span>Bark 地址：{status?.bark.base_url || "未配置"}</span>
                        <span>Device Key：{maskDeviceKey(status?.bark.device_key || "")}</span>
                        <span>分组：{status?.bark.group || "sms"}</span>
                      </div>
                      <Button
                        type="button"
                        variant="outline"
                        disabled={actionBusy}
                        onClick={() => {
                          void runAction("restart_sms", {}, "重启短信转发")
                        }}
                      >
                        <RefreshCwIcon data-icon="inline-start" />
                        重启短信转发
                      </Button>
                    </div>
                  </div>
                </div>
              </TabsContent>
            </Tabs>
          </CardContent>
          ) : null}
        </Card>

        <Card className="border-white/60 bg-white/85 backdrop-blur">
          <CardHeader>
            <CardTitle>快捷说明</CardTitle>
            <CardDescription>放在最底部，作为操作时的随手参考。</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-3 text-sm text-muted-foreground md:grid-cols-3">
            <div className="rounded-2xl border border-border/70 bg-background/70 p-4">
              切卡后会自动触发重启基带，Shell 会显示停 ModemManager、SIM 断电、SIM 上电和重新注册的每一步。
            </div>
            <div className="rounded-2xl border border-border/70 bg-background/70 p-4">
              最近短信和 eSIM Profiles 都是滚动区域，会随着状态刷新自动更新，不需要手动刷新整个页面。
            </div>
            <div className="rounded-2xl border border-border/70 bg-background/70 p-4">
              APN 和 Bark 都放进高级设置里，主界面只保留状态、切卡、短信和实时日志，减少操作分心。
            </div>
          </CardContent>
        </Card>
      </div>
      <Toaster richColors position="top-right" />
    </div>
  )
}

function OverviewTile({
  icon: Icon,
  label,
  value,
  hint,
  badgeVariant,
}: {
  icon: typeof SignalIcon
  label: string
  value: string
  hint: string
  badgeVariant?: "default" | "secondary" | "destructive" | "outline"
}) {
  return (
    <div className="rounded-2xl border border-border/70 bg-background/70 p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Icon className="size-4" />
          <span>{label}</span>
        </div>
        {badgeVariant ? <Badge variant={badgeVariant}>{value}</Badge> : null}
      </div>
      {!badgeVariant ? <div className="text-lg font-semibold tracking-tight">{value}</div> : null}
      <p className="mt-2 whitespace-pre-line text-sm text-muted-foreground">{hint}</p>
    </div>
  )
}

function ServiceLine({ name, state }: { name: string; state: string }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-xl border border-border/70 bg-white/80 px-3 py-2 text-sm">
      <span>{name}</span>
      <Badge variant={serviceVariant(state)}>{state}</Badge>
    </div>
  )
}

function EmptyState({
  icon: Icon,
  title,
  description,
  spinning = false,
}: {
  icon: typeof LoaderCircleIcon
  title: string
  description: string
  spinning?: boolean
}) {
  return (
    <div className="flex min-h-[18rem] flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-border/70 bg-white/80 px-6 text-center">
      <Icon className={cn("size-8 text-muted-foreground", spinning && "animate-spin")} />
      <div className="flex max-w-sm flex-col gap-1">
        <h3 className="font-medium">{title}</h3>
        <p className="text-sm text-muted-foreground">{description}</p>
      </div>
    </div>
  )
}

export default App
