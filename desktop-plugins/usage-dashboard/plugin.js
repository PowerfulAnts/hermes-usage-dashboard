/**
 * usage-dashboard — Hermes desktop plugin UI
 * ---------------------------------------------------------------------------
 * Token tracking is Hermes-only (state.db / session_model_usage). Independently
 * trackable provider limits, credits and funds remain visible. Do not conflate
 * those two scopes or remove either view.
 *
 * /summary?days=N -> {
 *   available, days, totals, daily, providers, // Hermes-only token buckets
 *   limits:{providers:{...}}, portal:{...}     // account status, not tokens
 * }
 *
 * Bucket: input/output/cached/cache_write/total/api_calls/hit_rate_pct.
 * Cache math is backend-owned: cached / (input + cached + cache_write).
 * Validate this file as ESM via a .mjs copy before shipping.
 */

import {
  Badge,
  Button,
  Codicon,
  EmptyState,
  ErrorState,
  GlyphSpinner,
  StatusDot,
  host,
  SegmentedControl,
  Skeleton,
  cn,
  haptic,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA
} from '@hermes/plugin-sdk';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Fragment, jsx, jsxs } from 'react/jsx-runtime';

let pluginCtx = null;

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const PROVIDER_LABELS = {
  'command-code-go': 'Command Code Go',
  custom: 'Custom provider',
  nous: 'Nous',
  'openai-codex': 'OpenAI Codex',
  openrouter: 'OpenRouter',
  unknown: 'Unknown / legacy'
};

function trimNum(x) {
  const s = Math.abs(x) >= 100 ? x.toFixed(0) : x.toFixed(1);
  return s.endsWith('.0') ? s.slice(0, -2) : s;
}

function fmt(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return '—';
  const a = Math.abs(v);
  if (a < 1000) return String(Math.round(v));
  if (a < 1e6) return trimNum(v / 1e3) + 'k';
  if (a < 1e9) return trimNum(v / 1e6) + 'M';
  return trimNum(v / 1e9) + 'B';
}

function fmtPct(p) {
  if (p === null || p === undefined || p === '') return '—';
  const v = Number(p);
  if (!Number.isFinite(v)) return '—';
  const c = Math.max(0, v);
  return (c >= 10 ? String(Math.round(c)) : (Math.round(c * 10) / 10).toFixed(1)) + '%';
}

function money(n) {
  if (n === null || n === undefined || n === '') return null;
  const v = Number(n);
  return Number.isFinite(v) ? '$' + v.toFixed(2) : null;
}

function clampPct(p) {
  return Math.max(0, Math.min(100, Number.isFinite(Number(p)) ? Number(p) : 0));
}

function dayLabel(day) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(day || ''));
  if (!m) return String(day || '');
  return MONTHS[Number(m[2]) - 1] + ' ' + String(Number(m[3]));
}

function localToday() {
  const d = new Date();
  const p = (x) => String(x).padStart(2, '0');
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
}

function fmtReset(ts) {
  const v = Number(ts);
  if (!Number.isFinite(v) || v <= 0) return null;
  const ms = v * (v > 1e12 ? 1 : 1000);
  const d = new Date(ms);
  if (isNaN(d.getTime())) return null;
  const when = MONTHS[d.getMonth()] + ' ' + d.getDate() + ', ' +
    String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
  const diffH = (ms - Date.now()) / 3600000;
  let rel;
  if (diffH <= 0) rel = 'now';
  else if (diffH < 1) rel = Math.max(1, Math.round(diffH * 60)) + 'm left';
  else if (diffH < 48) rel = Math.round(diffH) + 'h left';
  else rel = Math.round(diffH / 24) + 'd left';
  return when + ' (' + rel + ')';
}

function providerLabel(name) {
  return PROVIDER_LABELS[name] || name;
}

function FillBar({ pct, className, muted }) {
  return jsx('div', {
    className: cn('h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-(--ui-bg-tertiary)', className),
    children: jsx('div', {
      className: 'h-full rounded-full transition-[width] duration-500',
      style: {
        width: clampPct(pct) + '%',
        background: muted ? 'var(--ui-text-quaternary)' : 'var(--ui-accent)'
      }
    })
  });
}

function TokenMixBar({ input, output }) {
  const inp = Number(input) || 0;
  const out = Number(output) || 0;
  const total = inp + out;
  const inputPct = total > 0 ? inp / total * 100 : 0;
  return jsxs('div', {
    className: 'flex h-2 w-full overflow-hidden rounded-full bg-(--ui-bg-tertiary)',
    title: fmtPct(inputPct) + ' input · ' + fmtPct(100 - inputPct) + ' output',
    children: [
      jsx('div', {
        className: 'h-full transition-[width] duration-500',
        style: { width: inputPct + '%', background: 'var(--ui-accent)' }
      }),
      jsx('div', {
        className: 'h-full transition-[width] duration-500',
        style: { width: (100 - inputPct) + '%', background: 'var(--ui-text-quaternary)' }
      })
    ]
  });
}

function SectionHeading({ children, right }) {
  return jsxs('div', {
    className: 'flex items-center justify-between gap-3',
    children: [jsx('h2', { className: 'text-sm font-semibold tracking-tight', children }), right || null]
  });
}

function Card({ children, className }) {
  return jsx('div', {
    className: cn('flex min-w-0 flex-col gap-2.5 rounded-xl border border-(--ui-stroke-secondary) p-4', className),
    children
  });
}

function Caption({ children }) {
  return jsx('div', {
    className: 'text-[0.6875rem] uppercase tracking-wider text-(--ui-text-quaternary)',
    children
  });
}

function Metric({ label, value, detail }) {
  return jsxs('div', {
    className: 'min-w-0 rounded-lg bg-(--ui-bg-secondary) px-3 py-2.5',
    children: [
      jsx('div', { className: 'text-[0.625rem] uppercase tracking-wider text-(--ui-text-quaternary)', children: label }),
      jsx('div', { className: 'mt-0.5 text-base font-semibold tabular-nums tracking-tight', children: value }),
      detail ? jsx('div', { className: 'truncate text-[0.625rem] text-(--ui-text-quaternary)', children: detail }) : null
    ]
  });
}

// ── Hermes-only usage summary + restored daily bars ─────────────────────────

function TotalsSection({ totals, days }) {
  const t = totals || {};
  const hit = t.hit_rate_pct;
  const prompt = Number(t.input) + Number(t.cached) + Number(t.cache_write);
  const cost = money(t.cost_usd);
  return jsxs('section', {
    className: 'flex flex-col gap-3',
    children: [
      jsx(SectionHeading, { children: 'Tokens used inside Hermes' }),
      jsxs('div', {
        className: 'grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5',
        children: [
          Card({ children: [
            jsx(Caption, { children: 'Total · last ' + days + 'd' }),
            jsx('div', { className: 'text-3xl font-semibold tabular-nums tracking-tight', children: fmt(t.total) }),
            jsx('div', { className: 'text-xs text-(--ui-text-tertiary)', children: fmt(t.api_calls) + ' API calls' })
          ] }),
          Card({ children: [
            jsx(Caption, { children: 'Cost (Hermes-priced)' }),
            jsx('div', { className: 'text-2xl font-semibold tabular-nums tracking-tight', children: cost || '$0.00' }),
            jsx('div', {
              className: 'text-xs text-(--ui-text-tertiary)',
              children: 'provider-reported where available, otherwise estimated from token prices'
            })
          ] }),
          Card({ children: [
            jsx(Caption, { children: 'Input' }),
            jsx('div', { className: 'text-2xl font-semibold tabular-nums tracking-tight', children: fmt(t.input) }),
            jsx('div', { className: 'text-xs text-(--ui-text-tertiary)', children: 'uncached input tokens' })
          ] }),
          Card({ children: [
            jsx(Caption, { children: 'Output' }),
            jsx('div', { className: 'text-2xl font-semibold tabular-nums tracking-tight', children: fmt(t.output) }),
            jsx('div', { className: 'text-xs text-(--ui-text-tertiary)', children: 'includes reasoning output' })
          ] }),
          Card({ children: [
            jsx(Caption, { children: 'Total cache hit rate' }),
            jsxs('div', { className: 'flex items-baseline gap-2', children: [
              jsx('span', {
                className: 'text-3xl font-semibold tabular-nums tracking-tight',
                style: { color: 'var(--ui-accent)' },
                children: fmtPct(hit)
              }),
              jsx('span', { className: 'text-xs text-(--ui-text-tertiary)', children: hit == null ? '' : 'of prompt' })
            ] }),
            FillBar({ pct: Number(hit) || 0 }),
            jsx('div', {
              className: 'text-xs text-(--ui-text-tertiary)',
              children: hit == null ? 'No cache metadata reported.' : fmt(t.cached) + ' of ' + fmt(prompt) + ' prompt tokens cached'
            })
          ] })
        ]
      })
    ]
  });
}

function DailyActivitySection({ daily }) {
  const rows = Object.entries(daily || {})
    .map(([day, d]) => ({ day, tokens: Number(d.total) || 0, cached: Number(d.cached) || 0, cost: Number(d.cost_usd) || 0 }))
    .sort((a, b) => a.day.localeCompare(b.day))
    .slice(-14);
  const max = rows.reduce((m, row) => Math.max(m, row.tokens), 0);
  const today = localToday();

  return jsxs('section', {
    className: 'flex flex-col gap-3',
    children: [
      jsx(SectionHeading, { children: 'Daily activity' }),
      Card({ children: [
        jsx(Caption, { children: 'Hermes token usage · latest 14 active days' }),
        rows.length
          ? jsx('div', {
              className: 'flex items-end gap-1.5 px-0.5 pt-3',
              children: rows.map((row) => {
                const height = max > 0 ? Math.max(4, Math.round(row.tokens / max * 100)) : 0;
                const isToday = row.day === today;
                return jsxs('div', {
                  className: 'flex min-w-0 flex-1 flex-col items-center gap-1.5',
                  title: dayLabel(row.day) + ' · ' + fmt(row.tokens) + ' tokens · ' + fmt(row.cached) + ' cached · ' + (money(row.cost) || '$0.00'),
                  children: [
                    jsx('div', { className: 'flex h-24 w-full items-end justify-center', children:
                      jsx('div', {
                        className: 'w-full max-w-7 rounded-t-sm transition-[height] duration-500' + (isToday ? '' : ' opacity-75'),
                        style: { height: height + '%', background: isToday ? 'var(--ui-accent)' : 'var(--ui-stroke-secondary)' }
                      })
                    }),
                    jsx('div', {
                      className: cn('text-[0.5625rem] tabular-nums', !isToday && 'text-(--ui-text-quaternary)'),
                      style: isToday ? { color: 'var(--ui-accent)' } : undefined,
                      children: dayLabel(row.day).split(' ')[1]
                    })
                  ]
                }, row.day);
              })
            })
          : jsx('div', { className: 'py-4 text-xs text-(--ui-text-tertiary)', children: 'No activity recorded in this period.' })
      ] })
    ]
  });
}

// ── richer provider cards: all current fields + visual bars ─────────────────

function ProviderCard({ name, bucket, grandTotal }) {
  const b = bucket || {};
  const share = Number(grandTotal) > 0 ? Number(b.total) / Number(grandTotal) * 100 : 0;
  const prompt = Number(b.input) + Number(b.cached) + Number(b.cache_write);
  return Card({
    className: 'gap-3',
    children: [
      jsxs('div', { className: 'flex items-start justify-between gap-3', children: [
        jsxs('div', { className: 'min-w-0', children: [
          jsx('div', { className: 'truncate text-sm font-semibold', children: providerLabel(name) }),
          jsx('div', { className: 'text-[0.6875rem] text-(--ui-text-quaternary)', children: name })
        ] }),
        jsxs('div', { className: 'shrink-0 text-right', children: [
          jsx('div', { className: 'text-xl font-semibold tabular-nums tracking-tight', children: fmt(b.total) }),
          jsx('div', { className: 'text-[0.625rem] text-(--ui-text-quaternary)', children: fmtPct(share) + ' of Hermes usage' })
        ] })
      ] }),
      TokenMixBar({ input: b.input, output: b.output }),
      jsxs('div', { className: 'grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-2 xl:grid-cols-4', children: [
        Metric({ label: 'In', value: fmt(b.input) }),
        Metric({ label: 'Out', value: fmt(b.output) }),
        Metric({ label: 'Cached', value: fmt(b.cached) }),
        Metric({ label: 'Cache write', value: fmt(b.cache_write) }),
        Metric({ label: 'Cost', value: money(b.cost_usd) || '$0.00', detail: b.cost_usd > 0 ? undefined : 'no billable spend recorded' })
      ] }),
      jsxs('div', { className: 'flex items-center gap-3 border-t border-(--ui-stroke-secondary) pt-2.5', children: [
        jsxs('div', { className: 'w-24 shrink-0', children: [
          jsx('div', { className: 'text-[0.625rem] uppercase tracking-wider text-(--ui-text-quaternary)', children: 'Cache hit' }),
          jsx('div', { className: 'text-sm font-semibold tabular-nums', children: fmtPct(b.hit_rate_pct) })
        ] }),
        FillBar({ pct: Number(b.hit_rate_pct) || 0 }),
        jsx('div', {
          className: 'w-28 shrink-0 text-right text-[0.625rem] tabular-nums text-(--ui-text-quaternary)',
          children: b.hit_rate_pct == null ? 'not reported' : fmt(b.cached) + ' / ' + fmt(prompt)
        })
      ] }),
      jsx('div', { className: 'text-[0.625rem] text-(--ui-text-quaternary)', children: fmt(b.api_calls) + ' API calls' })
    ]
  });
}

function ProvidersSection({ providers, total }) {
  const entries = Object.entries(providers || {}).sort((a, b) => Number(b[1].total || 0) - Number(a[1].total || 0));
  return jsxs('section', {
    className: 'flex flex-col gap-3',
    children: [
      jsx(SectionHeading, { children: 'By provider' }),
      entries.length
        ? jsx('div', {
            className: 'grid grid-cols-1 gap-3 lg:grid-cols-2',
            children: entries.map(([name, bucket]) => jsx(ProviderCard, { name, bucket, grandTotal: total }, name))
          })
        : Card({ children: jsx('div', { className: 'text-xs text-(--ui-text-tertiary)', children: 'No provider usage in this period.' }) }),
      jsx('div', {
        className: 'text-[0.6875rem] text-(--ui-text-quaternary)',
        children: 'Cache hit = cached ÷ (in + cached + cache-written). “—” means that provider reported no cache metadata.'
      })
    ]
  });
}

// ── restored usage limits / credits / spend views ───────────────────────────

function accountExtras(lim) {
  const rows = [];
  const push = (label, value) => {
    if (value !== null && value !== undefined && value !== '') rows.push({ label, value });
  };
  push('Credit balance', money(lim.credit_balance));
  push('Monthly credits left', money(lim.monthly_credits_remaining));
  push('Purchased credits', money(lim.purchased_credits));
  push('Free credits', money(lim.free_credits));
  push('Key credit limit', money(lim.credit_limit));
  push('Key credits remaining', money(lim.credit_remaining));
  push('Today', money(lim.usage_daily));
  push('This week', money(lim.usage_weekly));
  push('This month', money(lim.usage_monthly));
  push('All-time key spend', money(lim.usage_all_time));
  push('Spendable now', lim.total_spendable_display);
  return rows;
}

function LimitCard({ name, lim }) {
  const windows = (lim && lim.windows) || [];
  const extras = accountExtras(lim || {});
  const hot = windows.some((w) => Number(w.used_pct) >= 80) || lim.status === 'low' || lim.status === 'depleted';
  return Card({
    className: hot ? 'border-amber-500/40' : undefined,
    children: [
      jsxs('div', { className: 'flex items-center justify-between gap-2', children: [
        jsx('div', { className: 'text-sm font-semibold', children: lim.label || providerLabel(name) }),
        lim.badge ? jsx(Badge, { variant: 'outline', size: 'xs', children: String(lim.badge) }) : null
      ] }),
      ...windows.map((w, index) =>
        jsxs('div', {
          className: 'flex flex-col gap-1.5' + (index > 0 ? ' border-t border-(--ui-stroke-secondary) pt-2.5' : ''),
          children: [
            jsxs('div', { className: 'flex items-baseline justify-between gap-2', children: [
              jsx('span', { className: 'text-xs font-medium text-(--ui-text-secondary)', children: w.name || 'Window' }),
              jsxs('span', { className: 'flex items-baseline gap-1.5', children: [
                jsx('span', {
                  className: 'text-lg font-semibold tabular-nums tracking-tight' +
                    (Number(w.used_pct) >= 80 ? ' text-amber-600 dark:text-amber-300' : ''),
                  children: fmtPct(w.used_pct)
                }),
                jsx('span', { className: 'text-[0.6875rem] text-(--ui-text-quaternary)', children: 'used' })
              ] })
            ] }),
            FillBar({ pct: Number(w.used_pct) || 0 }),
            jsx('div', {
              className: 'text-[0.6875rem] text-(--ui-text-quaternary)',
              children:
                (w.cap != null ? fmt(w.used) + ' of ' + fmt(w.cap) + ' credits' :
                  w.spent_display ? w.spent_display + ' of ' + (w.total_display || '—') + ' spent' : '') +
                (fmtReset(w.resets_at) ? (w.cap != null || w.spent_display ? ' · resets ' : 'resets ') + fmtReset(w.resets_at) : '') +
                (w.resets_display ? (w.cap != null || w.spent_display ? ' · resets ' : 'resets ') + w.resets_display : '')
            })
          ]
        }, (w.name || 'window') + index)
      ),
      extras.length
        ? jsx('div', {
            className: 'grid grid-cols-2 gap-x-4 gap-y-2 border-t border-(--ui-stroke-secondary) pt-2.5',
            children: extras.map((row) => jsxs('div', { className: 'min-w-0', children: [
              jsx('div', { className: 'text-[0.625rem] uppercase tracking-wider text-(--ui-text-quaternary)', children: row.label }),
              jsx('div', { className: 'truncate text-xs font-medium tabular-nums', children: row.value })
            ] }, row.label))
          })
        : null,
      windows.length === 0 && extras.length === 0
        ? jsx('div', { className: 'text-xs text-(--ui-text-quaternary)', children: 'Account status connected; no bounded window reported.' })
        : null,
      lim.renews_display
        ? jsx('div', { className: 'text-[0.6875rem] text-(--ui-text-quaternary)', children: 'Renews ' + lim.renews_display })
        : null
    ]
  });
}

function LimitsSection({ limits }) {
  const providers = (limits && limits.providers) || {};
  const entries = Object.entries(providers).filter(([, lim]) => lim && lim.available);
  if (!entries.length) return null;
  return jsxs('section', {
    className: 'flex flex-col gap-3',
    children: [
      jsx(SectionHeading, {
        children: 'Usage limits, credits & funds',
        right: jsx('span', { className: 'text-[0.6875rem] text-(--ui-text-quaternary)', children: 'account status · cached 5 min' })
      }),
      jsx('div', {
        className: 'grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3',
        children: entries.map(([name, lim]) => jsx(LimitCard, { name, lim }, name))
      })
    ]
  });
}

const PORTAL_STATUS = {
  healthy: { tone: 'good', label: 'Portal connected' },
  low: { tone: 'warn', label: 'Low balance' },
  depleted: { tone: 'warn', label: 'Balance depleted' },
  free: { tone: 'muted', label: 'Free plan' }
};

function CreditsSection({ portal }) {
  const status = PORTAL_STATUS[portal && portal.status] || PORTAL_STATUS.free;
  const plan = portal && portal.plan_bar;
  const topup = portal && portal.topup_bar;
  const planPct = plan && plan.pct_used != null ? Number(plan.pct_used) : null;
  const warnish = portal && (portal.status === 'low' || portal.status === 'depleted');
  return jsxs('section', {
    className: 'flex flex-col gap-3',
    children: [
      jsxs('div', { className: 'flex items-center gap-2', children: [
        jsx(SectionHeading, { children: 'Nous Portal funds' }),
        jsxs('span', {
          className: cn('flex items-center gap-1.5 text-xs', warnish ? 'font-medium text-amber-600 dark:text-amber-300' : 'text-(--ui-text-tertiary)'),
          children: [jsx(StatusDot, { tone: status.tone }), status.label]
        })
      ] }),
      Card({ className: warnish ? 'border-amber-500/40' : undefined, children: [
        jsxs('div', { className: 'flex items-center justify-between gap-2', children: [
          jsx(Caption, { children: 'Total spendable' }),
          portal.plan_name ? jsx(Badge, { variant: 'muted', size: 'xs', children: String(portal.plan_name) }) : null
        ] }),
        jsx('div', {
          className: 'text-3xl font-semibold tabular-nums tracking-tight',
          children: portal.total_spendable_display || '—'
        }),
        plan
          ? jsxs(Fragment, { children: [
              jsx('div', {
                className: 'text-xs text-(--ui-text-secondary)',
                children: (plan.spent_display || '—') + ' of ' + (plan.total_display || '—') + ' spent · ' + (plan.remaining_display || '—') + ' remaining'
              }),
              jsxs('div', { className: 'flex items-center gap-2.5', children: [
                FillBar({ pct: planPct == null ? 0 : planPct, className: 'max-w-md' }),
                jsx('span', { className: 'text-xs font-semibold tabular-nums', children: fmtPct(planPct) + ' used' })
              ] })
            ] })
          : jsx('div', {
              className: 'text-xs text-(--ui-text-tertiary)',
              children: 'No separate monthly allowance breakdown is available for this plan.'
            }),
        jsxs('div', { className: 'grid grid-cols-1 gap-3 border-t border-(--ui-stroke-secondary) pt-3 sm:grid-cols-2', children: [
          Metric({ label: 'Top-up balance', value: topup && topup.remaining_display ? topup.remaining_display : '$0.00', detail: portal.has_topup ? 'Purchased credits on file' : 'No top-up purchased' }),
          Metric({ label: 'Renews', value: portal.renews_display || '—', detail: 'plan allowance renewal' })
        ] })
      ] })
    ]
  });
}

// ── page ────────────────────────────────────────────────────────────────────

const RANGES = [
  { id: '7', label: '7d' },
  { id: '30', label: '30d' },
  { id: '90', label: '90d' }
];
const POLL_MS = 20000;

function UsagePage() {
  const [range, setRange] = useState('30');
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(null);
  const mountedRef = useRef(true);
  const rangeRef = useRef(range);

  const fetchData = useCallback(async (days, opts) => {
    const silent = Boolean(opts && opts.silent);
    const ctx = pluginCtx;
    if (!ctx || typeof ctx.rest !== 'function') return;
    if (!silent) setLoading(true);
    setRefreshing(true);
    try {
      const json = await ctx.rest('/summary?days=' + encodeURIComponent(days));
      if (!mountedRef.current) return;
      setData(json);
      setError(null);
    } catch (err) {
      if (!mountedRef.current) return;
      setError(err);
    } finally {
      if (mountedRef.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    fetchData(rangeRef.current, { silent: true });
    const timer = setInterval(() => fetchData(rangeRef.current, { silent: true }), POLL_MS);
    return () => {
      mountedRef.current = false;
      clearInterval(timer);
    };
  }, [fetchData]);

  const onRangeChange = useCallback((id) => {
    if (id === rangeRef.current) return;
    rangeRef.current = id;
    setRange(id);
    try { haptic(); } catch { /* optional */ }
    fetchData(id);
  }, [fetchData]);

  const onRefresh = useCallback(() => {
    fetchData(rangeRef.current, { silent: data !== null });
  }, [fetchData, data]);

  const onClose = useCallback(() => {
    try { haptic(); } catch { /* optional */ }
    const prev = host.state && host.state.focusedStoredSessionId && typeof host.state.focusedStoredSessionId.get === 'function'
      ? host.state.focusedStoredSessionId.get()
      : null;
    host.navigate(prev ? '/' + encodeURIComponent(String(prev)) : '/');
  }, []);

  const ok = data && data.available !== false;
  const totals = ok ? data.totals : null;
  const providers = ok ? data.providers : {};
  const portal = data && data.portal;

  return jsxs('div', {
    className: 'mx-auto flex w-full max-w-5xl flex-col gap-7 px-6 pb-14 pt-6',
    children: [
      jsxs('div', { className: 'flex flex-wrap items-end justify-between gap-4', children: [
        jsxs('div', { className: 'flex min-w-0 flex-col gap-1', children: [
          jsx('h1', { className: 'text-xl font-semibold tracking-tight', children: 'Usage' }),
          jsx('p', {
            className: 'text-xs text-(--ui-text-tertiary)',
            children: 'Hermes token usage, cache efficiency, provider limits and funds.'
          })
        ] }),
        jsxs('div', { className: 'flex items-center gap-2.5', children: [
          refreshing ? jsx(GlyphSpinner, { className: 'text-(--ui-text-quaternary)', ariaLabel: 'Refreshing usage' }) : null,
          jsx(SegmentedControl, { options: RANGES, value: range, onChange: onRangeChange }),
          jsx(Button, {
            variant: 'ghost', size: 'icon-sm', onClick: onClose,
            'aria-label': 'Close Usage page', title: 'Close',
            children: jsx(Codicon, { name: 'chrome-close', size: '1rem' })
          })
        ] })
      ] }, 'page-header'),

      error ? jsxs('div', { className: 'rounded-xl border border-(--ui-stroke-secondary) p-5', children: [
        jsx(ErrorState, { title: 'Usage data unavailable', description: 'Usage backend not reachable yet — restart Hermes once to activate it.' }),
        jsx('div', { className: 'mt-4 flex justify-center', children: jsx(Button, { size: 'xs', onClick: onRefresh, children: 'Retry now' }) })
      ] }, 'error') : null,

      loading && !data ? jsxs(Fragment, { children: [
        jsx('div', { className: 'grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4', children: [
          jsx(Skeleton, { className: 'h-32 w-full rounded-xl' }), jsx(Skeleton, { className: 'h-32 w-full rounded-xl' }),
          jsx(Skeleton, { className: 'h-32 w-full rounded-xl' }), jsx(Skeleton, { className: 'h-32 w-full rounded-xl' })
        ] }),
        jsx(Skeleton, { className: 'h-40 w-full rounded-xl' }),
        jsx('div', { className: 'grid grid-cols-1 gap-3 lg:grid-cols-2', children: [
          jsx(Skeleton, { className: 'h-64 w-full rounded-xl' }), jsx(Skeleton, { className: 'h-64 w-full rounded-xl' })
        ] })
      ] }, 'loading') : null,

      !error && !loading && !ok ? jsx(Fragment, { children: Card({ children: jsx('div', {
        className: 'text-xs text-(--ui-text-tertiary)',
        children: data && data.error ? String(data.error) : 'No usage data available for this period.'
      }) }) }, 'empty') : null,

      ok && totals ? jsx(TotalsSection, { totals, days: range }, 'totals') : null,
      ok ? jsx(DailyActivitySection, { daily: data.daily }, 'daily') : null,
      ok ? jsx(ProvidersSection, { providers, total: totals && totals.total }, 'providers') : null,
      data && data.limits ? jsx(LimitsSection, { limits: data.limits }, 'limits') : null,
      portal && portal.available ? jsx(CreditsSection, { portal }, 'portal') :
        portal && portal.available === false ? jsx('section', { className: 'flex flex-col gap-3', children:
          Card({ children: jsx(EmptyState, {
            title: 'Sign in to Nous Portal to see funds.',
            description: 'Hermes token usage and other provider limits remain available.',
            className: 'min-h-0 py-2'
          }) })
        }, 'portal-unavailable') : null,

      jsxs('div', { className: 'flex items-center justify-between gap-3 border-t border-(--ui-stroke-secondary) pt-4', children: [
        jsx('p', {
          className: 'text-[0.6875rem] leading-relaxed text-(--ui-text-quaternary)',
          children: 'Token counts come only from Hermes. Limits/funds are provider account status. Tokens refresh every 20s; account status is cached 5 minutes.'
        }),
        jsxs('div', { className: 'flex shrink-0 items-center gap-2', children: [
          refreshing ? jsx(GlyphSpinner, { className: 'text-(--ui-text-quaternary)', ariaLabel: 'Refreshing' }) : null,
          jsx(Button, { size: 'xs', onClick: onRefresh, children: 'Refresh' })
        ] })
      ] }, 'footer')
    ]
  });
}

export default {
  id: 'usage-dashboard',
  name: 'Usage Dashboard',
  register(ctx) {
    pluginCtx = ctx;
    ctx.register({ id: 'page', area: ROUTES_AREA, title: 'Usage', data: { path: '/usage-dashboard' }, render: () => jsx(UsagePage, {}) });
    ctx.register({ id: 'nav', area: SIDEBAR_NAV_AREA, data: { codicon: 'graph', label: 'Usage', path: '/usage-dashboard' } });
  }
};
