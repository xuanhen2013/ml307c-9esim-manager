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
  CalendarDaysIcon,
  ChevronDownIcon,
  Clock3Icon,
  LoaderCircleIcon,
  MessageSquareTextIcon,
  PlusIcon,
  RadioTowerIcon,
  RefreshCwIcon,
  RouterIcon,
  SendIcon,
  Settings2Icon,
  ShieldAlertIcon,
  SignalIcon,
  CardSimIcon,
  TerminalSquareIcon,
  Trash2Icon,
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
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/lib/utils"

type Profile = {
  iccid: string
  display_name: string
  provider_name?: string
  is_active?: boolean
  iccid_short?: string
  state?: string
  smsc_address?: string
  smsc_type?: string
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
  capabilities: {
    sim_type: string
    esim_management_enabled: boolean
    lpac_installed: boolean
  }
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
  notifications?: {
    configured_count: number
    configured_labels: string[]
    targets: NotificationTarget[]
  }
  keepalive?: {
    settings: KeepaliveSettings
    tasks: KeepaliveTask[]
    active_run: KeepaliveRun | null
    queued_runs: KeepaliveRun[]
    recent_runs: KeepaliveRun[]
    next_allowed_at: string
  }
  sms: SmsItem[]
  timestamp: string
}

type KeepaliveSettings = {
  queue_gap_seconds: number
}

type KeepaliveTask = {
  id: string
  label: string
  enabled: boolean
  profile_iccid: string
  profile_name: string
  target_number: string
  message: string
  cron_expression: string
  schedule_label: string
  next_run: string
  next_run_label: string
}

type KeepaliveRun = {
  id: string
  task_id: string
  label: string
  trigger: string
  scheduled_for: string
  scheduled_for_label: string
  profile_iccid: string
  profile_name: string
  target_number: string
  state: "queued" | "running" | "done" | "error" | string
  error: string
  last_message: string
  created_at: string
  updated_at: string
}

type NotificationTarget = {
  id: string
  label: string
  url: string
  enabled: boolean
  type: string
}

type ChannelKind = "bark" | "telegram" | "gotify" | "ntfy" | "discord" | "custom"

type NotificationChannelField = {
  key: string
  label: string
  placeholder: string
  required?: boolean
  inputType?: "text" | "password" | "url"
  options?: Array<{ label: string; value: string }>
}

type NotificationChannelDefinition = {
  type: ChannelKind
  label: string
  description: string
  fields: NotificationChannelField[]
  createValues: () => Record<string, string>
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
  | "send_test_sms"
  | "save_profile_smsc"
  | "run_keepalive_task"
  | "save_apn"
  | "save_notifications"
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

type NotificationFormTarget = {
  id: string
  type: ChannelKind
  enabled: boolean
  values: Record<string, string>
}

type ApnFormState = {
  apn: string
  username: string
  password: string
  ip_type: string
}

type ProfileSmscFormState = {
  address: string
  type: string
}

type KeepaliveFormTask = {
  id: string
  label: string
  enabled: boolean
  profile_iccid: string
  target_number: string
  message: string
  cron_expression: string
}

const ACTIVE_ACTION_KEY = "ess-active-action"
const EMPTY_NOTIFICATIONS = {
  configured_count: 0,
  configured_labels: [],
  targets: [],
} satisfies NonNullable<StatusData["notifications"]>
const EMPTY_KEEPALIVE = {
  settings: { queue_gap_seconds: 180 },
  tasks: [],
  active_run: null,
  queued_runs: [],
  recent_runs: [],
  next_allowed_at: "",
} satisfies NonNullable<StatusData["keepalive"]>

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

const NOTIFICATION_CHANNEL_DEFINITIONS: Record<ChannelKind, NotificationChannelDefinition> = {
  bark: {
    type: "bark",
    label: "Bark",
    description: "适合 iPhone 和 Apple 设备，填写服务器地址与设备 Key。",
    fields: [
      { key: "server_url", label: "服务器地址", placeholder: "https://api.day.app", required: true, inputType: "url" },
      { key: "device_key", label: "Device Key", placeholder: "输入 Bark 的 Device Key", required: true },
      { key: "group", label: "分组", placeholder: "sms" },
      {
        key: "level",
        label: "推送级别",
        placeholder: "选择推送级别",
        options: [
          { label: "active", value: "active" },
          { label: "timeSensitive", value: "timeSensitive" },
          { label: "passive", value: "passive" },
        ],
      },
    ],
    createValues: () => ({
      server_url: "https://api.day.app",
      device_key: "",
      group: "sms",
      level: "active",
    }),
  },
  telegram: {
    type: "telegram",
    label: "Telegram",
    description: "通过 Telegram Bot 推送，填写 Bot Token 和 Chat ID。",
    fields: [
      { key: "bot_token", label: "Bot Token", placeholder: "123456:ABCDEF...", required: true, inputType: "password" },
      { key: "chat_id", label: "Chat ID", placeholder: "例如 123456789", required: true },
    ],
    createValues: () => ({
      bot_token: "",
      chat_id: "",
    }),
  },
  gotify: {
    type: "gotify",
    label: "Gotify",
    description: "适合自建 Gotify 服务，填写服务器地址和应用 Token。",
    fields: [
      { key: "server_url", label: "服务器地址", placeholder: "https://push.example.com", required: true, inputType: "url" },
      { key: "token", label: "应用 Token", placeholder: "输入 Gotify Token", required: true, inputType: "password" },
      { key: "priority", label: "优先级", placeholder: "可留空，例如 5" },
    ],
    createValues: () => ({
      server_url: "",
      token: "",
      priority: "",
    }),
  },
  ntfy: {
    type: "ntfy",
    label: "ntfy",
    description: "适合 ntfy.sh 或自建 ntfy，填写服务器地址与主题名。",
    fields: [
      { key: "server_url", label: "服务器地址", placeholder: "https://ntfy.sh", required: true, inputType: "url" },
      { key: "topic", label: "主题", placeholder: "例如 esim-sms", required: true },
      { key: "token", label: "访问 Token", placeholder: "需要鉴权时填写", inputType: "password" },
    ],
    createValues: () => ({
      server_url: "https://ntfy.sh",
      topic: "",
      token: "",
    }),
  },
  discord: {
    type: "discord",
    label: "Discord",
    description: "填写 Discord Webhook ID 与 Token。",
    fields: [
      { key: "webhook_id", label: "Webhook ID", placeholder: "输入 Discord Webhook ID", required: true },
      { key: "webhook_token", label: "Webhook Token", placeholder: "输入 Discord Webhook Token", required: true, inputType: "password" },
    ],
    createValues: () => ({
      webhook_id: "",
      webhook_token: "",
    }),
  },
  custom: {
    type: "custom",
    label: "自定义",
    description: "高级模式，直接保存一条完整的 Apprise URL。",
    fields: [
      { key: "custom_label", label: "显示名称", placeholder: "例如 Webhook", required: true },
      { key: "url", label: "Apprise URL", placeholder: "输入完整的 Apprise URL", required: true },
    ],
    createValues: () => ({
      custom_label: "",
      url: "",
    }),
  },
}

const NOTIFICATION_CHANNEL_ORDER: ChannelKind[] = ["bark", "telegram", "gotify", "ntfy", "discord", "custom"]

const ICON_VERSION = "20260312-2"
const DEFAULT_BARK_ICON_URL =
  `https://raw.githubusercontent.com/cyDione/eSIM-SMS-Forwarder/main/frontend/public/app-icon.png?v=${ICON_VERSION}`

const NOTIFICATION_CHANNEL_ALIASES: Record<string, ChannelKind> = {
  bark: "bark",
  barks: "bark",
  telegram: "telegram",
  tgram: "telegram",
  gotify: "gotify",
  gotifys: "gotify",
  ntfy: "ntfy",
  ntfys: "ntfy",
  discord: "discord",
  custom: "custom",
}

function inferNotificationType(url: string, fallback = "apprise") {
  const match = url.trim().match(/^([a-z0-9+.-]+):\/\//i)
  return match?.[1]?.toLowerCase() || fallback
}

function normalizeServerUrl(value: string) {
  const trimmed = value.trim()
  if (!trimmed) return null
  const normalized = /^[a-z][a-z0-9+.-]*:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`
  try {
    return new URL(normalized)
  } catch {
    return null
  }
}

function convertCustomSchemeUrl(url: string, secureScheme: string, insecureScheme: string) {
  if (url.startsWith(`${secureScheme}://`)) return new URL(url.replace(`${secureScheme}://`, "https://"))
  if (url.startsWith(`${insecureScheme}://`)) return new URL(url.replace(`${insecureScheme}://`, "http://"))
  return null
}

function notificationChannelType(rawType: string, url: string): ChannelKind {
  const direct = NOTIFICATION_CHANNEL_ALIASES[rawType.trim().toLowerCase()]
  if (direct) return direct

  const inferred = inferNotificationType(url, "").toLowerCase()
  if (NOTIFICATION_CHANNEL_ALIASES[inferred]) return NOTIFICATION_CHANNEL_ALIASES[inferred]

  if (/^https:\/\/discord(?:app)?\.com\/api\/webhooks\//i.test(url.trim())) return "discord"
  return "custom"
}

function createNotificationTarget(type: ChannelKind, overrides: Partial<NotificationFormTarget> = {}): NotificationFormTarget {
  const randomId = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
  const definition = NOTIFICATION_CHANNEL_DEFINITIONS[type]
  return {
    id: overrides.id ?? `notification-${randomId}`,
    type,
    enabled: overrides.enabled ?? true,
    values: {
      ...definition.createValues(),
      ...(overrides.values ?? {}),
    },
  }
}

function buildNotificationUrl(target: NotificationFormTarget) {
  const values = target.values
  switch (target.type) {
    case "bark": {
      const server = normalizeServerUrl(values.server_url ?? "")
      if (!server) return ""
      const scheme = server.protocol === "http:" ? "bark" : "barks"
      const pathSegments = server.pathname.split("/").filter(Boolean)
      const deviceKey = values.device_key?.trim() ?? ""
      const query = new URLSearchParams()
      if (values.group?.trim()) query.set("group", values.group.trim())
      if (values.level?.trim()) query.set("level", values.level.trim())
      query.set("icon", DEFAULT_BARK_ICON_URL)
      const nextPath = [...pathSegments, deviceKey].filter(Boolean).join("/")
      const queryText = query.toString()
      return `${scheme}://${server.host}${nextPath ? `/${nextPath}` : ""}${queryText ? `?${queryText}` : ""}`
    }
    case "telegram": {
      const botToken = values.bot_token?.trim() ?? ""
      const chatId = values.chat_id?.trim() ?? ""
      return botToken && chatId ? `tgram://${botToken}/${chatId}` : ""
    }
    case "gotify": {
      const server = normalizeServerUrl(values.server_url ?? "")
      if (!server) return ""
      const scheme = server.protocol === "http:" ? "gotify" : "gotifys"
      const pathSegments = server.pathname.split("/").filter(Boolean)
      const token = values.token?.trim() ?? ""
      const priority = values.priority?.trim() ?? ""
      const query = new URLSearchParams()
      if (priority) query.set("priority", priority)
      const nextPath = [...pathSegments, token].filter(Boolean).join("/")
      const queryText = query.toString()
      return `${scheme}://${server.host}${nextPath ? `/${nextPath}` : ""}${queryText ? `?${queryText}` : ""}`
    }
    case "ntfy": {
      const server = normalizeServerUrl(values.server_url ?? "")
      if (!server) return ""
      const scheme = server.protocol === "http:" ? "ntfy" : "ntfys"
      const pathSegments = server.pathname.split("/").filter(Boolean)
      const topic = values.topic?.trim() ?? ""
      const token = values.token?.trim() ?? ""
      const authPrefix = token ? `${encodeURIComponent(token)}@` : ""
      const nextPath = [...pathSegments, topic].filter(Boolean).join("/")
      return `${scheme}://${authPrefix}${server.host}${nextPath ? `/${nextPath}` : ""}`
    }
    case "discord": {
      const webhookId = values.webhook_id?.trim() ?? ""
      const webhookToken = values.webhook_token?.trim() ?? ""
      return webhookId && webhookToken ? `discord://${webhookId}/${webhookToken}` : ""
    }
    case "custom":
      return values.url?.trim() ?? ""
  }
}

function parseNotificationTarget(target: NotificationTarget): NotificationFormTarget {
  const type = notificationChannelType(target.type ?? "", target.url ?? "")
  const enabled = target.enabled ?? true
  const id = target.id
  const url = target.url ?? ""

  if (type === "bark") {
    const parsed = convertCustomSchemeUrl(url, "barks", "bark")
    if (!parsed) return createNotificationTarget("bark", { id, enabled })
    const segments = parsed.pathname.split("/").filter(Boolean)
    const deviceKey = decodeURIComponent(segments.pop() ?? "")
    const serverUrl = `${parsed.protocol}//${parsed.host}${segments.length ? `/${segments.join("/")}` : ""}`
    return createNotificationTarget("bark", {
      id,
      enabled,
      values: {
        server_url: serverUrl,
        device_key: deviceKey,
        group: parsed.searchParams.get("group") ?? "sms",
        level: parsed.searchParams.get("level") ?? "active",
      },
    })
  }

  if (type === "telegram") {
    const match = url.trim().match(/^tgram:\/\/([^/]+)\/([^/?#]+)/i)
    return createNotificationTarget("telegram", {
      id,
      enabled,
      values: {
        bot_token: decodeURIComponent(match?.[1] ?? ""),
        chat_id: decodeURIComponent(match?.[2] ?? ""),
      },
    })
  }

  if (type === "gotify") {
    const parsed = convertCustomSchemeUrl(url, "gotifys", "gotify")
    if (!parsed) return createNotificationTarget("gotify", { id, enabled })
    const segments = parsed.pathname.split("/").filter(Boolean)
    const token = decodeURIComponent(segments.pop() ?? "")
    const serverUrl = `${parsed.protocol}//${parsed.host}${segments.length ? `/${segments.join("/")}` : ""}`
    return createNotificationTarget("gotify", {
      id,
      enabled,
      values: {
        server_url: serverUrl,
        token,
        priority: parsed.searchParams.get("priority") ?? "",
      },
    })
  }

  if (type === "ntfy") {
    const parsed = convertCustomSchemeUrl(url, "ntfys", "ntfy")
    if (!parsed) return createNotificationTarget("ntfy", { id, enabled })
    const segments = parsed.pathname.split("/").filter(Boolean)
    const topic = decodeURIComponent(segments.pop() ?? "")
    const serverUrl = `${parsed.protocol}//${parsed.host}${segments.length ? `/${segments.join("/")}` : ""}`
    return createNotificationTarget("ntfy", {
      id,
      enabled,
      values: {
        server_url: serverUrl,
        topic,
        token: decodeURIComponent(parsed.username ?? ""),
      },
    })
  }

  if (type === "discord") {
    if (/^discord:\/\//i.test(url.trim())) {
      const match = url.trim().match(/^discord:\/\/([^/]+)\/([^/?#]+)/i)
      return createNotificationTarget("discord", {
        id,
        enabled,
        values: {
          webhook_id: decodeURIComponent(match?.[1] ?? ""),
          webhook_token: decodeURIComponent(match?.[2] ?? ""),
        },
      })
    }

    const parsed = normalizeServerUrl(url)
    const segments = parsed?.pathname.split("/").filter(Boolean) ?? []
    const webhookIndex = segments.findIndex((segment) => segment === "webhooks")
    return createNotificationTarget("discord", {
      id,
      enabled,
      values: {
        webhook_id: webhookIndex >= 0 ? decodeURIComponent(segments[webhookIndex + 1] ?? "") : "",
        webhook_token: webhookIndex >= 0 ? decodeURIComponent(segments[webhookIndex + 2] ?? "") : "",
      },
    })
  }

  return createNotificationTarget("custom", {
    id,
    enabled,
    values: {
      custom_label: target.label ?? "",
      url,
    },
  })
}

function normalizeNotificationTargets(targets: NotificationTarget[] = []) {
  const seenTypes = new Set<ChannelKind>()
  const normalized: NotificationFormTarget[] = []
  for (const target of targets) {
    const parsed = parseNotificationTarget(target)
    if (seenTypes.has(parsed.type)) continue
    seenTypes.add(parsed.type)
    normalized.push(parsed)
  }
  return normalized.sort(
    (left, right) =>
      NOTIFICATION_CHANNEL_ORDER.indexOf(left.type) - NOTIFICATION_CHANNEL_ORDER.indexOf(right.type),
  )
}

function notificationChannelLabel(target: NotificationFormTarget) {
  if (target.type === "custom") return target.values.custom_label?.trim() || "自定义"
  return NOTIFICATION_CHANNEL_DEFINITIONS[target.type].label
}

function notificationFieldValue(target: NotificationFormTarget, fieldKey: string) {
  return target.values[fieldKey] ?? ""
}

function getNotifications(status: StatusData | null | undefined) {
  return status?.notifications ?? EMPTY_NOTIFICATIONS
}

function getKeepalive(status: StatusData | null | undefined) {
  return status?.keepalive ?? EMPTY_KEEPALIVE
}

function getActiveProfile(profiles: Profile[]) {
  return profiles.find((profile) => profile.is_active) ?? null
}

function normalizeKeepaliveTasks(tasks: KeepaliveTask[] = []): KeepaliveFormTask[] {
  return tasks.map((task) => ({
    id: task.id,
    label: task.label,
    enabled: task.enabled,
    profile_iccid: task.profile_iccid,
    target_number: task.target_number,
    message: task.message,
    cron_expression: task.cron_expression,
  }))
}

function createKeepaliveTask(profiles: Profile[]): KeepaliveFormTask {
  const fallbackProfile = getActiveProfile(profiles) ?? profiles[0]
  const randomId = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
  const fallbackLabel = fallbackProfile?.display_name ? `${fallbackProfile.display_name} 保活` : "保活任务"
  return {
    id: `keepalive-${randomId}`,
    label: fallbackLabel,
    enabled: true,
    profile_iccid: fallbackProfile?.iccid ?? "",
    target_number: "",
    message: "KEEPALIVE",
    cron_expression: "0 9 * * *",
  }
}

function buildProfileSmscForms(profiles: Profile[] = []): Record<string, ProfileSmscFormState> {
  return Object.fromEntries(
    profiles.map((profile) => [
      profile.iccid,
      {
        address: profile.smsc_address || "",
        type: profile.smsc_type || "145",
      },
    ]),
  )
}

function keepaliveRunStateLabel(state: KeepaliveRun["state"]) {
  switch (state) {
    case "queued":
      return "排队中"
    case "running":
      return "执行中"
    case "done":
      return "已完成"
    case "error":
      return "失败"
    default:
      return state || "--"
  }
}

function keepaliveRunStateVariant(state: KeepaliveRun["state"]) {
  if (state === "done") return "default" as const
  if (state === "running" || state === "queued") return "secondary" as const
  return "destructive" as const
}

function keepaliveTriggerLabel(trigger: string) {
  return trigger === "schedule" ? "定时" : "手动"
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
    case "send_test_sms":
      return "发送测试短信"
    case "save_profile_smsc":
      return "保存短信中心"
    case "run_keepalive_task":
      return "执行保活任务"
    case "save_apn":
      return "保存 APN"
    case "save_notifications":
      return "保存通知渠道"
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
  const [notificationTargets, setNotificationTargets] = useState<NotificationFormTarget[]>([])
  const [newNotificationType, setNewNotificationType] = useState<ChannelKind>("bark")
  const [keepaliveSettings, setKeepaliveSettings] = useState<KeepaliveSettings>({ queue_gap_seconds: 180 })
  const [keepaliveTasks, setKeepaliveTasks] = useState<KeepaliveFormTask[]>([])
  const [expandedKeepaliveTaskId, setExpandedKeepaliveTaskId] = useState<string | null>(null)
  const [profileSmscForms, setProfileSmscForms] = useState<Record<string, ProfileSmscFormState>>({})
  const [expandedProfileIccid, setExpandedProfileIccid] = useState<string | null>(null)
  const [apnForm, setApnForm] = useState<ApnFormState>({
    apn: "",
    username: "",
    password: "",
    ip_type: "ipv4v6",
  })
  const [networkCode, setNetworkCode] = useState("")
  const [radioMode, setRadioMode] = useState("3g4g_prefer4g")
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [shellPanelOpen, setShellPanelOpen] = useState(false)

  const notificationsDirtyRef = useRef(false)
  const keepaliveDirtyRef = useRef(false)
  const profileSmscDirtyRef = useRef(false)
  const apnDirtyRef = useRef(false)
  const networkDirtyRef = useRef(false)
  const radioModeDirtyRef = useRef(false)
  const pollTokenRef = useRef(0)

  const appendLog = useCallback((event: ActionEvent) => {
    setLogs((current) => [...current.slice(-199), event])
  }, [])

  const syncFormsFromStatus = useCallback((snapshot: StatusData) => {
    if (!notificationsDirtyRef.current) {
      setNotificationTargets(normalizeNotificationTargets(getNotifications(snapshot).targets))
    }
    if (!keepaliveDirtyRef.current) {
      const keepalive = getKeepalive(snapshot)
      setKeepaliveSettings(keepalive.settings)
      setKeepaliveTasks(normalizeKeepaliveTasks(keepalive.tasks))
    }
    if (!profileSmscDirtyRef.current) {
      setProfileSmscForms(buildProfileSmscForms(snapshot.profiles))
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
    notificationsDirtyRef.current = false
    keepaliveDirtyRef.current = false
    profileSmscDirtyRef.current = false
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

  const runAction = useCallback(async (action: ActionName, payload: Record<string, unknown>, label: string) => {
    if (activeAction || submittingActionLabel) {
      toast.info("当前已有任务在执行，请稍等")
      return
    }
    if (action === "switch_profile" && !(status?.capabilities.esim_management_enabled ?? true)) {
      toast.info("当前为普通 SIM 模式，eSIM 管理功能已禁用")
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
        target:
          typeof payload.iccid === "string"
            ? payload.iccid
            : typeof payload.operator_code === "string"
              ? payload.operator_code
              : "",
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
  }, [activeAction, appendLog, pollAction, status?.capabilities.esim_management_enabled, submittingActionLabel])

  const saveNotifications = useCallback(async () => {
    if (activeAction || submittingActionLabel) {
      toast.info("当前已有任务在执行，请稍等")
      return
    }

    try {
      const payloadTargets = notificationTargets.map((target, index) => {
        const definition = NOTIFICATION_CHANNEL_DEFINITIONS[target.type]
        const missingField = definition.fields.find(
          (field) => field.required && !notificationFieldValue(target, field.key).trim(),
        )
        if (missingField) {
          throw new Error(`${definition.label} 还缺少 ${missingField.label}`)
        }

        const url = buildNotificationUrl(target)
        if (!url.trim()) {
          throw new Error(`${definition.label} 配置还不完整`)
        }

        return {
          id: target.id || `notification-${index + 1}`,
          label: notificationChannelLabel(target),
          url,
          enabled: target.enabled,
          type: target.type,
        }
      })

      setSubmittingActionLabel("保存通知渠道")
      appendLog({
        time: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
        level: "info",
        message: `准备执行：保存通知渠道（${payloadTargets.length} 条）`,
      })

      const response = await requestJson<{ ok: boolean; status?: StatusData }>("/api/notifications", {
        method: "POST",
        body: JSON.stringify({
          action: "save_notifications",
          targets: payloadTargets,
        }),
      })

      notificationsDirtyRef.current = false
      setSubmittingActionLabel(null)

      if (response.status) {
        setStatus(response.status)
        syncFormsFromStatus(response.status)
      } else {
        await refreshStatus(false)
      }

      appendLog({
        time: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
        level: "info",
        message: "通知渠道已保存",
      })
      toast.success("通知渠道配置已保存")
    } catch (error) {
      setSubmittingActionLabel(null)
      const message = error instanceof Error ? error.message : "保存通知渠道失败"
      appendLog({
        time: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
        level: "error",
        message,
      })
      toast.error(message)
    }
  }, [activeAction, appendLog, notificationTargets, refreshStatus, submittingActionLabel, syncFormsFromStatus])

  const saveKeepalive = useCallback(async () => {
    if (activeAction || submittingActionLabel) {
      toast.info("当前已有任务在执行，请稍等")
      return
    }

    try {
      const payloadTasks = keepaliveTasks.map((task, index) => {
        if (!task.label.trim()) {
          throw new Error(`第 ${index + 1} 条保活任务缺少名称`)
        }
        if (!task.profile_iccid.trim()) {
          throw new Error(`保活任务 ${task.label} 缺少 Profile`)
        }
        if (!task.target_number.trim()) {
          throw new Error(`保活任务 ${task.label} 缺少目标手机号`)
        }
        if (!task.message.trim()) {
          throw new Error(`保活任务 ${task.label} 缺少短信内容`)
        }
        if (task.cron_expression.trim().split(/\s+/).length !== 5) {
          throw new Error(`保活任务 ${task.label} 的 cron 表达式必须是 5 段`)
        }
        return {
          id: task.id,
          label: task.label.trim(),
          enabled: task.enabled,
          profile_iccid: task.profile_iccid.trim(),
          target_number: task.target_number.trim(),
          message: task.message,
          cron_expression: task.cron_expression.trim(),
        }
      })

      setSubmittingActionLabel("保存保活配置")
      appendLog({
        time: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
        level: "info",
        message: `准备执行：保存保活配置（${payloadTasks.length} 条）`,
      })

      const response = await requestJson<{ ok: boolean; status?: StatusData }>("/api/keepalive", {
        method: "POST",
        body: JSON.stringify({
          settings: keepaliveSettings,
          tasks: payloadTasks,
        }),
      })

      keepaliveDirtyRef.current = false
      setSubmittingActionLabel(null)

      if (response.status) {
        setStatus(response.status)
        syncFormsFromStatus(response.status)
      } else {
        await refreshStatus(false)
      }

      appendLog({
        time: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
        level: "info",
        message: "保活配置已保存",
      })
      toast.success("保活配置已保存")
    } catch (error) {
      setSubmittingActionLabel(null)
      const message = error instanceof Error ? error.message : "保存保活配置失败"
      appendLog({
        time: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
        level: "error",
        message,
      })
      toast.error(message)
    }
  }, [activeAction, appendLog, keepaliveSettings, keepaliveTasks, refreshStatus, submittingActionLabel, syncFormsFromStatus])

  const sendKeepaliveTestSms = useCallback(async (task: KeepaliveFormTask) => {
    const number = task.target_number.trim()
    const message = task.message.trim()
    const taskLabel = task.label.trim() || "保活任务"
    if (!number) {
      toast.error(`保活任务 ${taskLabel} 缺少目标手机号`)
      return
    }
    if (!message) {
      toast.error(`保活任务 ${taskLabel} 缺少短信内容`)
      return
    }
    await runAction("send_test_sms", { number, message }, `测试保活短信 ${taskLabel}`)
  }, [runAction])

  const saveProfileSmsc = useCallback(async (profile: Profile, preset?: ProfileSmscFormState) => {
    const currentValue = preset ?? profileSmscForms[profile.iccid] ?? { address: "", type: "145" }
    const address = currentValue.address.trim()
    const type = currentValue.type.trim() || "145"
    if (!address) {
      toast.error(`${profile.display_name} 缺少短信中心号码`)
      return
    }
    if (!/^\d{1,3}$/.test(type)) {
      toast.error(`${profile.display_name} 的短信中心类型必须是数字`)
      return
    }
    profileSmscDirtyRef.current = true
    if (preset) {
      setProfileSmscForms((current) => ({
        ...current,
        [profile.iccid]: preset,
      }))
    }
    await runAction(
      "save_profile_smsc",
      {
        iccid: profile.iccid,
        smsc_address: address,
        smsc_type: type,
        apply_now: Boolean(profile.is_active),
      },
      profile.is_active ? `保存并应用 ${profile.display_name} 的短信中心` : `保存 ${profile.display_name} 的短信中心`,
    )
  }, [profileSmscForms, runAction])

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

  useEffect(() => {
    const usedTypes = new Set(notificationTargets.map((target) => target.type))
    const nextType =
      NOTIFICATION_CHANNEL_ORDER.find((type) => !usedTypes.has(type) && type === newNotificationType) ??
      NOTIFICATION_CHANNEL_ORDER.find((type) => !usedTypes.has(type)) ??
      "custom"
    if (nextType !== newNotificationType) {
      setNewNotificationType(nextType)
    }
  }, [newNotificationType, notificationTargets])

  const activeProfile = getActiveProfile(status?.profiles ?? [])
  const esimEnabled = status?.capabilities.esim_management_enabled ?? true
  const activeProfileLabel = esimEnabled ? activeProfile?.display_name || "未检测到" : "普通 SIM"
  const activeProfileHint = esimEnabled
    ? `手机号：${status?.modem.number || "--"}`
    : `手机号：${status?.modem.number || "--"}`
  const profileCountLabel = esimEnabled ? `${status?.profiles.length ?? 0} 个` : "已禁用"
  const notifications = getNotifications(status)
  const keepalive = getKeepalive(status)
  const configuredLabels = notifications.configured_labels
  const configuredCount = notifications.configured_count
  const keepaliveEnabledCount = keepalive.tasks.filter((task) => task.enabled).length
  const actionBusy = Boolean(activeAction || submittingActionLabel)
  const shellActionLabel = activeAction?.label || submittingActionLabel
  const configuredNotificationTypes = new Set(notificationTargets.map((target) => target.type))
  const availableNotificationTypes = NOTIFICATION_CHANNEL_ORDER.filter((type) => !configuredNotificationTypes.has(type))

  useEffect(() => {
    if (shellActionLabel) {
      setShellPanelOpen(true)
    }
  }, [shellActionLabel])

  useEffect(() => {
    if (!expandedKeepaliveTaskId) return
    if (!keepaliveTasks.some((task) => task.id === expandedKeepaliveTaskId)) {
      setExpandedKeepaliveTaskId(null)
    }
  }, [expandedKeepaliveTaskId, keepaliveTasks])

  useEffect(() => {
    if (!expandedProfileIccid) return
    if (!(status?.profiles ?? []).some((profile) => profile.iccid === expandedProfileIccid)) {
      setExpandedProfileIccid(null)
    }
  }, [expandedProfileIccid, status?.profiles])

  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top,_rgba(14,165,233,0.18),_transparent_30%),linear-gradient(180deg,_#f7f9fc_0%,_#eef3f7_100%)] pb-24 sm:pb-28">
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
                label={esimEnabled ? "当前 Profile" : "当前 SIM"}
                value={activeProfileLabel}
                hint={activeProfileHint}
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
                hint={configuredCount ? `已配置 ${configuredCount} 个通知渠道` : "尚未配置通知渠道"}
                tags={configuredLabels}
                emptyTagLabel="未配置通知渠道"
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
                  <CardDescription>
                    {esimEnabled
                      ? "手动刷新状态后同步最新 Profile 列表。"
                      : "当前为普通 SIM 模式，eSIM 管理功能已禁用。"}
                  </CardDescription>
                </div>
                <CardAction>
                  <Badge variant="outline">{profileCountLabel}</Badge>
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
                  ) : !esimEnabled ? (
                    <EmptyState
                      icon={CardSimIcon}
                      title="普通 SIM 模式"
                      description="此模式只保留短信转发、基带状态和网络设置，eSIM Profiles 与切卡功能已禁用。"
                    />
                  ) : status?.profiles.length ? (
                    status.profiles.map((profile) => {
                      const isCurrent = Boolean(profile.is_active)
                      const isSwitching =
                        activeAction?.action === "switch_profile" && activeAction.target === profile.iccid
                      const isExpanded = expandedProfileIccid === profile.iccid
                      const smscForm = profileSmscForms[profile.iccid] ?? {
                        address: profile.smsc_address || "",
                        type: profile.smsc_type || "145",
                      }
                      const isGiffgaffProfile = `${profile.display_name} ${profile.provider_name || ""}`
                        .toLowerCase()
                        .includes("giffgaff")
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
                                <p className="text-sm text-muted-foreground">
                                  短信中心：{profile.smsc_address ? `${profile.smsc_address},${profile.smsc_type || "145"}` : "未配置"}
                                </p>
                              </div>
                              <div className="flex flex-wrap items-center justify-end gap-2">
                                <Button
                                  type="button"
                                  size="sm"
                                  variant="outline"
                                  onClick={() => {
                                    setExpandedProfileIccid((current) => (current === profile.iccid ? null : profile.iccid))
                                  }}
                                >
                                  <ChevronDownIcon
                                    data-icon="inline-start"
                                    className={cn("transition-transform", isExpanded && "rotate-180")}
                                  />
                                  {isExpanded ? "收起设置" : "展开设置"}
                                </Button>
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
                            </div>
                            <div className="grid gap-2 text-sm text-muted-foreground sm:grid-cols-2">
                              <span>ICCID：{profile.iccid || "--"}</span>
                              <span>状态：{profile.state || (isCurrent ? "enabled" : "--")}</span>
                            </div>
                            {isExpanded ? (
                              <div className="rounded-2xl border border-border/70 bg-background/70 p-3">
                                <div className="mb-3 flex flex-col gap-1">
                                  <h4 className="text-sm font-medium text-foreground">短信中心</h4>
                                  <p className="text-sm text-muted-foreground">
                                    为当前 Profile 绑定 SMSC。切换到这张卡后会自动重新应用；当前使用中的 Profile 可以立即写入基带。
                                  </p>
                                </div>
                                <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_8rem]">
                                  <div className="grid gap-2">
                                    <Label htmlFor={`profile-smsc-address-${profile.iccid}`}>SMSC 号码</Label>
                                    <Input
                                      id={`profile-smsc-address-${profile.iccid}`}
                                      value={smscForm.address}
                                      onChange={(event) => {
                                        profileSmscDirtyRef.current = true
                                        setProfileSmscForms((current) => ({
                                          ...current,
                                          [profile.iccid]: {
                                            ...(current[profile.iccid] ?? { address: "", type: "145" }),
                                            address: event.target.value,
                                          },
                                        }))
                                      }}
                                      placeholder={isGiffgaffProfile ? "+447802002606" : "例如 +447802002606"}
                                    />
                                  </div>
                                  <div className="grid gap-2">
                                    <Label htmlFor={`profile-smsc-type-${profile.iccid}`}>类型</Label>
                                    <Input
                                      id={`profile-smsc-type-${profile.iccid}`}
                                      value={smscForm.type}
                                      onChange={(event) => {
                                        profileSmscDirtyRef.current = true
                                        setProfileSmscForms((current) => ({
                                          ...current,
                                          [profile.iccid]: {
                                            ...(current[profile.iccid] ?? { address: "", type: "145" }),
                                            type: event.target.value,
                                          },
                                        }))
                                      }}
                                      placeholder="145"
                                    />
                                  </div>
                                </div>
                                <div className="mt-3 flex flex-wrap gap-2">
                                  <Button
                                    type="button"
                                    size="sm"
                                    variant="outline"
                                    disabled={actionBusy}
                                    onClick={() => {
                                      void saveProfileSmsc(profile)
                                    }}
                                  >
                                    <SendIcon data-icon="inline-start" />
                                    {isCurrent ? "保存并应用" : "保存关联"}
                                  </Button>
                                  {isGiffgaffProfile ? (
                                    <Button
                                      type="button"
                                      size="sm"
                                      variant="outline"
                                      disabled={actionBusy}
                                      onClick={() => {
                                        void saveProfileSmsc(profile, { address: "+447802002606", type: "145" })
                                      }}
                                    >
                                      套用 giffgaff SMSC
                                    </Button>
                                  ) : null}
                                </div>
                              </div>
                            ) : null}
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
            <CardContent className="flex h-full min-h-0 flex-col pb-4">
              <ScrollArea className="min-h-0 flex-1 rounded-xl border border-border/70 bg-background/70">
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
                      description="收到短信后会自动出现在这里，并按通知渠道配置转发。"
                    />
                  )}
                </div>
              </ScrollArea>
            </CardContent>
          </Card>
        </div>

        <Card className="border-white/60 bg-white/85 backdrop-blur">
          <CardHeader>
            <div className="flex items-start justify-between gap-3">
              <div className="flex flex-col gap-1">
                <CardTitle className="flex items-center gap-2">
                  <Settings2Icon />
                  高级设置
                </CardTitle>
                <CardDescription>APN、选网、网络制式、保活任务和通知渠道都放在这里，避免主界面出现空白区。</CardDescription>
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
                <TabsTrigger value="keepalive">保活</TabsTrigger>
                <TabsTrigger value="forwarder">通知</TabsTrigger>
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

              <TabsContent value="keepalive" className="flex flex-col gap-5">
                {!esimEnabled ? (
                  <div className="rounded-2xl border border-dashed border-border/70 bg-background/70 px-6 py-10 text-center">
                    <p className="text-sm text-muted-foreground">
                      当前为普通 SIM 模式，保活任务依赖 Profile 切换，当前页面只展示短信与网络相关功能。
                    </p>
                  </div>
                ) : (
                  <div className="grid gap-5 xl:grid-cols-[1.15fr_0.85fr]">
                    <div className="rounded-2xl border border-border/70 bg-background/70 p-4">
                      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                        <div className="flex items-center gap-2">
                          <Clock3Icon className="text-muted-foreground" />
                          <div>
                            <h3 className="font-medium">保活任务配置</h3>
                            <p className="text-sm text-muted-foreground">
                              到点后自动切换到指定 Profile，确认网络可用后发送短信，完成后再切回原 Profile。
                            </p>
                          </div>
                        </div>
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          disabled={actionBusy || !status?.profiles.length}
                          onClick={() => {
                            const nextTask = createKeepaliveTask(status?.profiles ?? [])
                            keepaliveDirtyRef.current = true
                            setKeepaliveTasks((current) => [...current, nextTask])
                            setExpandedKeepaliveTaskId(nextTask.id)
                          }}
                        >
                          <PlusIcon data-icon="inline-start" />
                          添加任务
                        </Button>
                      </div>

                      <div className="mb-4 rounded-2xl border border-border/70 bg-white/80 p-4 shadow-sm">
                        <div className="grid gap-4 md:grid-cols-[220px_1fr] md:items-end">
                          <div className="grid gap-2">
                            <Label htmlFor="keepalive-queue-gap">切卡缓冲时间</Label>
                            <Input
                              id="keepalive-queue-gap"
                              type="number"
                              min={30}
                              max={1800}
                              value={String(keepaliveSettings.queue_gap_seconds)}
                              onChange={(event) => {
                                keepaliveDirtyRef.current = true
                                const value = Number.parseInt(event.target.value, 10)
                                setKeepaliveSettings({
                                  queue_gap_seconds: Number.isNaN(value) ? 180 : value,
                                })
                              }}
                            />
                          </div>
                          <p className="text-sm text-muted-foreground">
                            多个保活任务同时到点时会自动排队，下一次切卡会等待这里设置的缓冲时间。
                          </p>
                        </div>
                      </div>

                      <div className="flex flex-col gap-4">
                        {keepaliveTasks.length ? (
                          keepaliveTasks.map((task) => {
                            const savedTask = keepalive.tasks.find((item) => item.id === task.id)
                            const profileName =
                              savedTask?.profile_name ||
                              (status?.profiles ?? []).find((profile) => profile.iccid === task.profile_iccid)?.display_name ||
                              "待选择 Profile"
                            const isExpanded = expandedKeepaliveTaskId === task.id
                            return (
                              <div key={task.id} className="rounded-2xl border border-border/70 bg-white/80 p-4 shadow-sm">
                                <div className="flex flex-col gap-4">
                                  <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                                    <div className="flex min-w-0 flex-1 flex-col gap-2">
                                      <div className="flex flex-wrap items-center gap-2">
                                        <span className="truncate text-sm font-medium text-foreground">{task.label || "未命名任务"}</span>
                                        <Badge variant="outline">{profileName}</Badge>
                                        <Badge variant="secondary">{task.enabled ? "已启用" : "已停用"}</Badge>
                                        <Badge variant="outline">{savedTask?.schedule_label || task.cron_expression || "--"}</Badge>
                                        {savedTask?.next_run_label ? (
                                          <Badge variant="outline">{savedTask.next_run_label}</Badge>
                                        ) : null}
                                        {task.target_number ? <Badge variant="outline">{task.target_number}</Badge> : null}
                                      </div>
                                    </div>
                                    <div className="flex flex-wrap items-center gap-2">
                                      <div className="flex items-center gap-2 text-sm text-muted-foreground">
                                        <span>启用任务</span>
                                        <Switch
                                          checked={task.enabled}
                                          onCheckedChange={(checked) => {
                                            keepaliveDirtyRef.current = true
                                            setKeepaliveTasks((current) =>
                                              current.map((item) => (item.id === task.id ? { ...item, enabled: checked } : item)),
                                            )
                                          }}
                                          aria-label={`切换 ${task.label} 启用状态`}
                                        />
                                      </div>
                                      <Button
                                        type="button"
                                        size="sm"
                                        variant="outline"
                                        onClick={() => {
                                          setExpandedKeepaliveTaskId((current) => (current === task.id ? null : task.id))
                                        }}
                                      >
                                        <ChevronDownIcon
                                          data-icon="inline-start"
                                          className={cn("transition-transform", isExpanded && "rotate-180")}
                                        />
                                        {isExpanded ? "收起设置" : "展开设置"}
                                      </Button>
                                    </div>
                                  </div>

                                  {isExpanded ? (
                                    <>
                                      <p className="text-sm text-muted-foreground">
                                        未保存的修改会在保存保活配置后参与调度与手动执行。
                                      </p>
                                      <div className="flex flex-wrap items-center gap-2">
                                        <Button
                                          type="button"
                                          size="sm"
                                          variant="outline"
                                          disabled={actionBusy}
                                          onClick={() => {
                                            void sendKeepaliveTestSms(task)
                                          }}
                                        >
                                          <SendIcon data-icon="inline-start" />
                                          测试短信
                                        </Button>
                                        <Button
                                          type="button"
                                          size="sm"
                                          variant="outline"
                                          disabled={actionBusy || !savedTask}
                                          onClick={() => {
                                            void runAction(
                                              "run_keepalive_task",
                                              { task_id: task.id, trigger: "manual" },
                                              `执行保活 ${task.label}`,
                                            )
                                          }}
                                        >
                                          <SendIcon data-icon="inline-start" />
                                          立即执行
                                        </Button>
                                        <Button
                                          type="button"
                                          size="sm"
                                          variant="outline"
                                          disabled={actionBusy}
                                          onClick={() => {
                                            keepaliveDirtyRef.current = true
                                            setKeepaliveTasks((current) => current.filter((item) => item.id !== task.id))
                                          }}
                                        >
                                          <Trash2Icon data-icon="inline-start" />
                                          删除
                                        </Button>
                                      </div>

                                      <div className="grid gap-4 md:grid-cols-2">
                                        <div className="grid gap-2">
                                          <Label htmlFor={`keepalive-label-${task.id}`}>任务名称</Label>
                                          <Input
                                            id={`keepalive-label-${task.id}`}
                                            value={task.label}
                                            onChange={(event) => {
                                              keepaliveDirtyRef.current = true
                                              setKeepaliveTasks((current) =>
                                                current.map((item) =>
                                                  item.id === task.id ? { ...item, label: event.target.value } : item,
                                                ),
                                              )
                                            }}
                                            placeholder="例如 EE 保活"
                                          />
                                        </div>
                                        <div className="grid gap-2 md:col-span-2">
                                          <Label>目标 Profile</Label>
                                          <Select
                                            value={task.profile_iccid}
                                            onValueChange={(value) => {
                                              keepaliveDirtyRef.current = true
                                              setKeepaliveTasks((current) =>
                                                current.map((item) =>
                                                  item.id === task.id ? { ...item, profile_iccid: value ?? "" } : item,
                                                ),
                                              )
                                            }}
                                          >
                                            <SelectTrigger className="w-full">
                                              <SelectValue placeholder="选择 Profile" />
                                            </SelectTrigger>
                                            <SelectContent>
                                              <SelectGroup>
                                                <SelectLabel>Profiles</SelectLabel>
                                                {(status?.profiles ?? []).map((profile) => (
                                                  <SelectItem key={profile.iccid} value={profile.iccid}>
                                                    {profile.display_name}
                                                  </SelectItem>
                                                ))}
                                              </SelectGroup>
                                            </SelectContent>
                                          </Select>
                                        </div>
                                        <div className="grid gap-2 md:col-span-2">
                                          <Label htmlFor={`keepalive-cron-${task.id}`}>cron 表达式</Label>
                                          <Input
                                            id={`keepalive-cron-${task.id}`}
                                            value={task.cron_expression}
                                            onChange={(event) => {
                                              keepaliveDirtyRef.current = true
                                              setKeepaliveTasks((current) =>
                                                current.map((item) =>
                                                  item.id === task.id ? { ...item, cron_expression: event.target.value } : item,
                                                ),
                                              )
                                            }}
                                            placeholder="例如 0 9 1 * *"
                                          />
                                          <p className="text-sm text-muted-foreground">
                                            采用 5 段 cron：分钟 小时 日 月 星期。示例：`0 9 * * *` 表示每天 09:00，`0 9 1 * *` 表示每月 1 日 09:00。
                                          </p>
                                        </div>
                                        <div className="grid gap-2">
                                          <Label htmlFor={`keepalive-number-${task.id}`}>短信目标号码</Label>
                                          <Input
                                            id={`keepalive-number-${task.id}`}
                                            value={task.target_number}
                                            onChange={(event) => {
                                              keepaliveDirtyRef.current = true
                                              setKeepaliveTasks((current) =>
                                                current.map((item) =>
                                                  item.id === task.id ? { ...item, target_number: event.target.value } : item,
                                                ),
                                              )
                                            }}
                                            placeholder="例如 +447000000000"
                                          />
                                        </div>
                                      </div>

                                      <div className="grid gap-2">
                                        <Label htmlFor={`keepalive-message-${task.id}`}>短信内容</Label>
                                        <Textarea
                                          id={`keepalive-message-${task.id}`}
                                          value={task.message}
                                          onChange={(event) => {
                                            keepaliveDirtyRef.current = true
                                            setKeepaliveTasks((current) =>
                                              current.map((item) =>
                                                item.id === task.id ? { ...item, message: event.target.value } : item,
                                              ),
                                            )
                                          }}
                                          rows={4}
                                          placeholder="输入用于保活的短信内容"
                                        />
                                        <p className="text-sm text-muted-foreground">
                                          “测试短信”会使用当前填写的目标号码与短信内容立即发送一条短信，用于确认保活参数是否可用。
                                        </p>
                                      </div>
                                    </>
                                  ) : null}
                                </div>
                              </div>
                            )
                          })
                        ) : (
                          <div className="flex min-h-48 flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-border/70 bg-white/80 px-6 text-center">
                            <p className="max-w-md text-sm text-muted-foreground">
                              当前还没有保活任务。添加任务后即可按 cron 表达式自动切卡、发短信、通知并回切。
                            </p>
                          </div>
                        )}

                        <div className="flex flex-wrap gap-2">
                          <Button
                            type="button"
                            disabled={actionBusy}
                            onClick={() => {
                              void saveKeepalive()
                            }}
                          >
                            <SendIcon data-icon="inline-start" />
                            保存保活配置
                          </Button>
                          <Button
                            type="button"
                            variant="outline"
                            disabled={!status}
                            onClick={() => {
                              if (!status) return
                              keepaliveDirtyRef.current = false
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
                          <CalendarDaysIcon className="text-muted-foreground" />
                          <div>
                            <h3 className="font-medium">调度状态</h3>
                            <p className="text-sm text-muted-foreground">
                              展示保活队列、当前执行项与最近记录，便于确认排队与回切情况。
                            </p>
                          </div>
                        </div>
                        <div className="grid gap-3">
                          <div className="grid gap-3 sm:grid-cols-2">
                            <div className="rounded-2xl border border-border/70 bg-white/80 p-4">
                              <div className="text-sm text-muted-foreground">已启用任务</div>
                              <div className="mt-2 text-2xl font-semibold">{keepaliveEnabledCount}</div>
                            </div>
                            <div className="rounded-2xl border border-border/70 bg-white/80 p-4">
                              <div className="text-sm text-muted-foreground">下一次可切卡</div>
                              <div className="mt-2 text-sm font-medium">
                                {keepalive.next_allowed_at || "当前可执行"}
                              </div>
                            </div>
                          </div>

                          <div className="rounded-2xl border border-border/70 bg-white/80 p-4">
                            <div className="mb-3 flex items-center justify-between gap-3">
                              <span className="text-sm font-medium">当前执行</span>
                              {keepalive.active_run ? (
                                <Badge variant={keepaliveRunStateVariant(keepalive.active_run.state)}>
                                  {keepaliveRunStateLabel(keepalive.active_run.state)}
                                </Badge>
                              ) : (
                                <Badge variant="outline">空闲</Badge>
                              )}
                            </div>
                            {keepalive.active_run ? (
                              <div className="space-y-2 text-sm text-muted-foreground">
                                <div>{keepalive.active_run.label}</div>
                                <div>触发方式：{keepaliveTriggerLabel(keepalive.active_run.trigger)}</div>
                                <div>目标 Profile：{keepalive.active_run.profile_name || "--"}</div>
                                <div>计划时间：{keepalive.active_run.scheduled_for_label || "--"}</div>
                                <div className="whitespace-pre-wrap break-words text-foreground/80">
                                  {keepalive.active_run.last_message || "任务已经启动，等待下一条日志。"}
                                </div>
                              </div>
                            ) : (
                              <p className="text-sm text-muted-foreground">当前没有保活任务正在执行。</p>
                            )}
                          </div>

                          <div className="rounded-2xl border border-border/70 bg-white/80 p-4">
                            <div className="mb-3 flex items-center justify-between gap-3">
                              <span className="text-sm font-medium">排队任务</span>
                              <Badge variant="secondary">{keepalive.queued_runs.length} 条</Badge>
                            </div>
                            <div className="space-y-3">
                              {keepalive.queued_runs.length ? (
                                keepalive.queued_runs.map((run) => (
                                  <div key={run.id} className="rounded-xl border border-border/70 bg-background/70 p-3 text-sm">
                                    <div className="flex flex-wrap items-center gap-2">
                                      <span className="font-medium">{run.label}</span>
                                      <Badge variant="outline">{keepaliveTriggerLabel(run.trigger)}</Badge>
                                    </div>
                                    <div className="mt-2 text-muted-foreground">
                                      {run.scheduled_for_label || "等待调度"} · {run.profile_name || "--"}
                                    </div>
                                  </div>
                                ))
                              ) : (
                                <p className="text-sm text-muted-foreground">当前没有排队中的保活任务。</p>
                              )}
                            </div>
                          </div>

                          <div className="rounded-2xl border border-border/70 bg-white/80 p-4">
                            <div className="mb-3 flex items-center justify-between gap-3">
                              <span className="text-sm font-medium">最近记录</span>
                              <Badge variant="secondary">{keepalive.recent_runs.length} 条</Badge>
                            </div>
                            <div className="space-y-3">
                              {keepalive.recent_runs.length ? (
                                keepalive.recent_runs.map((run) => (
                                  <div key={run.id} className="rounded-xl border border-border/70 bg-background/70 p-3 text-sm">
                                    <div className="flex flex-wrap items-center gap-2">
                                      <span className="font-medium">{run.label}</span>
                                      <Badge variant={keepaliveRunStateVariant(run.state)}>
                                        {keepaliveRunStateLabel(run.state)}
                                      </Badge>
                                    </div>
                                    <div className="mt-2 text-muted-foreground">
                                      {run.updated_at || run.scheduled_for_label || "--"} · {run.profile_name || "--"}
                                    </div>
                                    <div className="mt-2 whitespace-pre-wrap break-words text-foreground/80">
                                      {run.error || run.last_message || "暂无更多信息"}
                                    </div>
                                  </div>
                                ))
                              ) : (
                                <p className="text-sm text-muted-foreground">保活任务执行后会在这里保留最近记录。</p>
                              )}
                            </div>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                )}
              </TabsContent>

              <TabsContent value="forwarder" className="flex flex-col gap-5">
                <div className="grid gap-5 xl:grid-cols-[1.1fr_0.9fr]">
                  <div className="rounded-2xl border border-border/70 bg-background/70 p-4">
                    <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                      <div className="flex items-center gap-2">
                        <SendIcon className="text-muted-foreground" />
                        <div>
                          <h3 className="font-medium">通知渠道配置</h3>
                          <p className="text-sm text-muted-foreground">
                            先选择渠道类型再添加，每种渠道只保留一份，表单会按渠道类型显示对应字段。
                          </p>
                        </div>
                      </div>
                      <div className="flex w-full flex-col gap-2 sm:w-auto sm:min-w-72">
                        <Select
                          value={newNotificationType}
                          onValueChange={(value) => {
                            setNewNotificationType(value as ChannelKind)
                          }}
                          disabled={actionBusy || !availableNotificationTypes.length}
                        >
                          <SelectTrigger className="w-full">
                            <SelectValue placeholder="选择通知渠道" />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectGroup>
                              <SelectLabel>可添加渠道</SelectLabel>
                              {availableNotificationTypes.map((type) => (
                                <SelectItem key={type} value={type}>
                                  {NOTIFICATION_CHANNEL_DEFINITIONS[type].label}
                                </SelectItem>
                              ))}
                            </SelectGroup>
                          </SelectContent>
                        </Select>
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          disabled={actionBusy || !availableNotificationTypes.length}
                          onClick={() => {
                            notificationsDirtyRef.current = true
                            setNotificationTargets((current) => {
                              if (current.some((item) => item.type === newNotificationType)) return current
                              const next = [...current, createNotificationTarget(newNotificationType)]
                              return next.sort(
                                (left, right) =>
                                  NOTIFICATION_CHANNEL_ORDER.indexOf(left.type) -
                                  NOTIFICATION_CHANNEL_ORDER.indexOf(right.type),
                              )
                            })
                          }}
                        >
                          <PlusIcon data-icon="inline-start" />
                          添加渠道
                        </Button>
                      </div>
                    </div>
                    <div className="flex flex-col gap-4">
                      {notificationTargets.length ? (
                        notificationTargets.map((target) => {
                          const definition = NOTIFICATION_CHANNEL_DEFINITIONS[target.type]
                          return (
                          <div key={target.id} className="rounded-2xl border border-border/70 bg-white/80 p-4 shadow-sm">
                            <div className="flex flex-col gap-4">
                              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                                <div className="flex flex-col gap-2">
                                  <div className="flex items-center gap-2">
                                    <Badge variant="outline">{definition.label}</Badge>
                                    <Badge variant="secondary">{target.enabled ? "已启用" : "已停用"}</Badge>
                                  </div>
                                  <p className="text-sm text-muted-foreground">{definition.description}</p>
                                </div>
                                <div className="flex items-center gap-3">
                                  <div className="flex items-center gap-2 text-sm text-muted-foreground">
                                    <span>启用转发</span>
                                    <Switch
                                      checked={target.enabled}
                                      onCheckedChange={(checked) => {
                                        notificationsDirtyRef.current = true
                                        setNotificationTargets((current) =>
                                          current.map((item) => (item.id === target.id ? { ...item, enabled: checked } : item)),
                                        )
                                      }}
                                      aria-label={`切换 ${definition.label} 启用状态`}
                                    />
                                  </div>
                                  <Button
                                    type="button"
                                    size="sm"
                                    variant="outline"
                                    disabled={actionBusy}
                                    onClick={() => {
                                      notificationsDirtyRef.current = true
                                      setNotificationTargets((current) =>
                                        current.filter((item) => item.id !== target.id),
                                      )
                                    }}
                                  >
                                    <Trash2Icon data-icon="inline-start" />
                                    删除
                                  </Button>
                                </div>
                              </div>
                              <div className="grid gap-4 md:grid-cols-2">
                                {definition.fields.map((field) => (
                                  <div key={`${target.id}-${field.key}`} className="grid gap-2">
                                    <Label htmlFor={`notification-${target.id}-${field.key}`}>
                                      {field.label}
                                    </Label>
                                    {field.options ? (
                                      <Select
                                        value={notificationFieldValue(target, field.key)}
                                        onValueChange={(value) => {
                                          const nextValue = value ?? ""
                                          notificationsDirtyRef.current = true
                                          setNotificationTargets((current) =>
                                            current.map((item) =>
                                              item.id === target.id
                                                ? {
                                                    ...item,
                                                    values: {
                                                      ...item.values,
                                                      [field.key]: nextValue,
                                                    },
                                                  }
                                                : item,
                                            ),
                                          )
                                        }}
                                      >
                                        <SelectTrigger id={`notification-${target.id}-${field.key}`} className="w-full">
                                          <SelectValue placeholder={field.placeholder} />
                                        </SelectTrigger>
                                        <SelectContent>
                                          <SelectGroup>
                                            {field.options.map((option) => (
                                              <SelectItem key={option.value} value={option.value}>
                                                {option.label}
                                              </SelectItem>
                                            ))}
                                          </SelectGroup>
                                        </SelectContent>
                                      </Select>
                                    ) : (
                                      <Input
                                        id={`notification-${target.id}-${field.key}`}
                                        type={field.inputType ?? "text"}
                                        value={notificationFieldValue(target, field.key)}
                                        onChange={(event) => {
                                          notificationsDirtyRef.current = true
                                          setNotificationTargets((current) =>
                                            current.map((item) =>
                                              item.id === target.id
                                                ? {
                                                    ...item,
                                                    values: {
                                                      ...item.values,
                                                      [field.key]: event.target.value,
                                                    },
                                                  }
                                                : item,
                                            ),
                                          )
                                        }}
                                        placeholder={field.placeholder}
                                      />
                                    )}
                                  </div>
                                ))}
                              </div>
                            </div>
                          </div>
                        )})
                      ) : (
                        <div className="flex min-h-48 flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-border/70 bg-white/80 px-6 text-center">
                          <p className="max-w-md text-sm text-muted-foreground">
                            当前还没有配置通知渠道。先从上方选择一个渠道类型，再添加到列表里继续填写。
                          </p>
                        </div>
                      )}
                      <div className="flex flex-wrap gap-2">
                        <Button
                          type="button"
                          disabled={actionBusy}
                          onClick={() => {
                            void saveNotifications()
                          }}
                        >
                          <SendIcon data-icon="inline-start" />
                          保存通知渠道
                        </Button>
                        <Button
                          type="button"
                          variant="outline"
                          disabled={!status}
                          onClick={() => {
                            if (!status) return
                            notificationsDirtyRef.current = false
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
                        <p className="text-sm text-muted-foreground">这里只展示服务状态和已配置渠道标签，不显示具体 URL 或密钥。</p>
                      </div>
                    </div>
                    <div className="grid gap-3">
                      <ServiceLine name="ModemManager" state={status?.services.modemmanager || "--"} />
                      <ServiceLine name="短信转发" state={status?.services.sms_forwarder || "--"} />
                      <ServiceLine name="管理页面" state={status?.services.web_admin || "--"} />
                      <Separator />
                      <div className="rounded-2xl border border-border/70 bg-white/80 p-4">
                        <div className="flex items-center justify-between gap-3">
                          <span className="text-sm font-medium">已配置渠道标签</span>
                          <Badge variant="secondary">{configuredCount} 个</Badge>
                        </div>
                        <div className="mt-3">
                          <ChannelBadgeList labels={configuredLabels} emptyLabel="当前还没有已配置渠道" />
                        </div>
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
              APN 和通知渠道都放进高级设置里，主界面只保留状态、切卡、短信和实时日志，减少操作分心。
            </div>
          </CardContent>
        </Card>
      </div>

      {shellPanelOpen ? (
        <div className="fixed inset-x-0 bottom-0 z-40 px-4 pb-4 sm:px-6 lg:px-8">
          <div className="mx-auto w-full max-w-7xl">
            <Card className="relative border-slate-800 bg-slate-950/95 text-slate-100 shadow-2xl backdrop-blur">
              <Button
                type="button"
                size="icon"
                variant="secondary"
                aria-label="收起日志面板"
                className="absolute left-1/2 top-0 h-10 w-14 -translate-x-1/2 -translate-y-1/2 rounded-t-full rounded-b-none border border-slate-700 bg-slate-900 text-slate-100 shadow-lg hover:bg-slate-800"
                onClick={() => {
                  setShellPanelOpen(false)
                }}
              >
                <ChevronDownIcon className="size-5" />
              </Button>

              <CardHeader className="pb-3 pt-5">
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
                <ScrollArea className="h-[18rem] rounded-xl border border-slate-800 bg-slate-950/80">
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
                      <div className="flex h-full min-h-[12rem] items-center justify-center text-slate-500">
                        暂时还没有任务日志，点任意操作后这里会实时显示执行进度。
                      </div>
                    )}
                  </div>
                </ScrollArea>
              </CardContent>
            </Card>
          </div>
        </div>
      ) : (
        <div className="pointer-events-none fixed inset-x-0 bottom-0 z-40 flex justify-center">
          <Button
            type="button"
            size="icon"
            variant="secondary"
            aria-label="展开日志面板"
            className="pointer-events-auto h-10 w-14 translate-y-1/2 rounded-t-full rounded-b-none border border-slate-700 bg-slate-900 text-slate-100 shadow-lg hover:bg-slate-800"
            onClick={() => {
              setShellPanelOpen(true)
            }}
          >
            <ChevronDownIcon className="size-5 rotate-180" />
          </Button>
        </div>
      )}
      <Toaster richColors position="top-right" />
    </div>
  )
}

function OverviewTile({
  icon: Icon,
  label,
  value,
  hint,
  tags,
  emptyTagLabel,
  badgeVariant,
}: {
  icon: typeof SignalIcon
  label: string
  value: string
  hint: string
  tags?: string[]
  emptyTagLabel?: string
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
      {tags ? (
        <div className="mt-3">
          <ChannelBadgeList labels={tags} emptyLabel={emptyTagLabel || "暂无标签"} />
        </div>
      ) : null}
    </div>
  )
}

function ChannelBadgeList({
  labels,
  emptyLabel,
}: {
  labels: string[]
  emptyLabel: string
}) {
  if (!labels.length) {
    return <span className="text-sm text-muted-foreground">{emptyLabel}</span>
  }

  return (
    <div className="flex flex-wrap gap-2">
      {labels.map((label) => (
        <Badge key={label} variant="secondary" className="max-w-full truncate">
          {label}
        </Badge>
      ))}
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
