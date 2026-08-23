/**
 * usage-dashboard — Hermes desktop plugin UI (universal tracker)
 * ---------------------------------------------------------------------------
 * Page: /usage-dashboard ("Usage" in the sidebar). One scrollable dashboard
 * showing token usage from EVERY local AI provider discovered by the backend
 * adapter registry — plus live quota windows and the Nous Portal credit
 * balance when signed in.
 *
 * DYNAMIC BY DESIGN: this file contains NO hardcoded provider lists. Labels,
 * badges and ordering come from each adapter's meta (backend/sources.py embeds
 * them into /summary). Adding a provider = dropping one adapter file into
 * backend/adapters/ — the UI picks it up automatically.
 *
 * ── Notes from previous codex agents (agent-to-agent) ─────────────────────
 * - DATA SOURCE: GET /api/plugins/usage-dashboard/summary?days=N (7/30/90)
 *   via pluginCtx.rest('/summary?days=N'). Response contract:
 *     sources:  { name: { meta:{label,badge,homepage,order}, available,
 *                         totals{input,output,cached,total}, daily{}, models{},
 *                         error? } }
 *     combined: { totals, daily, per_source_totals, source_share_pct }
 *     limits:   { providers: { name: { label, badge?, available,
 *                                      windows:[{name,used_pct,used,cap,
 *                                                resets_at,exceeded}],
 *                                      …extras } } }
 *     portal:   Nous Portal credit model (available:false when signed out)
 * - RESTART: if the endpoint errors/404s the backend predates the plugin —
 *   restart Hermes once; the page shows an ErrorState saying exactly that.
 * - PORTAL CONTRACT: dollar figures are PRE-FORMATTED strings (*_display);
 *   plan_bar.pct_used = integer % USED; fill_fraction = bar fraction REMAINING;
 *   top-up has NO denominator → dollars only, never a top-up percentage.
 * - Sources may be available:false with an error string — render a muted
 *   note, never fake zeros.
 * - Numbers are large (billions): fmt() scales k/M/B.
 * - VALIDATE ESM: `node --check` on a .mjs COPY of this file — plain .js check
 *   misses ASI traps that break the app's real loader.
 * ---------------------------------------------------------------------------
 */

import {
  Badge,
  Button,
  Codicon,
  EmptyState,
  ErrorState,
  GlyphSpinner,
  host,
  SegmentedControl,
  Skeleton,
  StatusDot,
  cn,
  haptic,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA
} from '@hermes/plugin-sdk';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Fragment, jsx, jsxs } from 'react/jsx-runtime';

let pluginCtx = null;

// ── formatting ──────────────────────────────────────────────────────────────

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function trimNum(x) {
  const s = Math.abs(x) >= 100 ? x.toFixed(0) : x.toFixed(1);
  return s.endsWith('.0') ? s.slice(0, -2) : s;
}

/** 940 → "940", 12_400 → "12.4k", 846_000_000 → "846M", 5_130_000_000 → "5.13B". */
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
  const v = Number(p);
  if (!Number.isFinite(v)) return '—';
  const c = Math.max(0, v);
  return (c >= 10 ? String(Math.round(c)) : (Math.round(c * 10) / 10).toFixed(1)) + '%';
}

function clampPct(p) {
  return Math.max(0, Math.min(100, Number.isFinite(Number(p)) ? Number(p) : 0));
}

/** 'YYYY-MM-DD' → "Aug 22". */
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

/** unix seconds (or ms) → "Aug 29, 14:00 (+ rel)". */
function fmtReset(ts) {
  const v = Number(ts);
  if (!Number.isFinite(v) || v <= 0) return null;
  const ms = v * (v > 1e12 ? 1 : 1000); // tolerate s or ms
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

/** Model keys look like "<adapter>/<model>" or "model [provider]" — strip routing prefixes for display. */
function shortModel(key) {
  const s = String(key || '');
  const slash = s.indexOf('/');
  return slash > 0 ? s.slice(slash + 1) : s;
}

const README_ANCHOR = '#adding-a-provider';

// ── tiny shared pieces ──────────────────────────────────────────────────────

/** Slim horizontal bar. pct = fill 0–100. */
function FillBar({ pct, className }) {
  return jsx('div', {
    className: cn('h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-(--ui-bg-tertiary)', className),
    children: jsx('div', {
      className: 'h-full rounded-full transition-[width] duration-500',
      style: { width: clampPct(pct) + '%', background: 'var(--ui-accent)' }
    })
  });
}

function SectionHeading({ children, right }) {
  return jsxs('div', {
    className: 'flex items-center justify-between gap-3',
    children: [
      jsx('h2', { className: 'text-sm font-semibold tracking-tight', children }),
      right || null
    ]
  });
}

function Card({ children, className }) {
  return jsx('div', {
    className: cn(
      'flex min-w-0 flex-col gap-2.5 rounded-xl border border-(--ui-stroke-secondary) p-4',
      className
    ),
    children
  });
}

function Caption({ children }) {
  return jsx('div', {
    className: 'text-[0.6875rem] uppercase tracking-wider text-(--ui-text-quaternary)',
    children
  });
}

// ── combined hero ───────────────────────────────────────────────────────────

function CombinedSection({ combined }) {
  const t = (combined && combined.totals) || {};
  const shares = (combined && combined.source_share_pct) || {};
  const perSource = (combined && combined.per_source_totals) || {};
  const labels = (combined && combined.source_labels) || {};
  const sources = Object.keys(shares).sort((a, b) => (perSource[b] || 0) - (perSource[a] || 0));
  const daily = Object.entries((combined && combined.daily) || {})
    .map(([day, d]) => ({ day, tokens: Number(d.total) || 0 }))
    .sort((a, b) => (a.day < b.day ? -1 : 1))
    .slice(-14);
  const max = daily.reduce((acc, d) => Math.max(acc, d.tokens), 0);
  const today = localToday();

  const chart = daily.length
    ? jsxs('div', {
        className: 'flex items-end gap-1.5 px-0.5 pt-2',
        children: daily.map((d) => {
          const h = max > 0 ? Math.max(3, Math.round((d.tokens / max) * 100)) : 0;
          const isToday = d.day === today;
          return jsxs(
            'div',
            {
              className: 'flex min-w-0 flex-1 flex-col items-center gap-1.5',
              title: dayLabel(d.day) + ' · ' + fmt(d.tokens) + ' tokens',
              children: [
                jsx('div', {
                  className: 'flex h-20 w-full items-end justify-center',
                  children: jsx('div', {
                    className: 'w-full max-w-6 rounded-t-sm transition-[height] duration-500' + (isToday ? '' : ' opacity-80'),
                    style: { height: h + '%', background: isToday ? 'var(--ui-accent)' : 'var(--ui-stroke-secondary)' }
                  })
                }),
                jsx('div', {
                  className: cn('text-[0.5625rem] tabular-nums', !isToday && 'text-(--ui-text-quaternary)'),
                  style: isToday ? { color: 'var(--ui-accent)' } : undefined,
                  children: dayLabel(d.day).split(' ')[1]
                })
              ]
            },
            d.day
          );
        })
      })
    : jsx('div', { className: 'pt-2 text-xs text-(--ui-text-tertiary)', children: 'No activity recorded in this period.' });

  return jsxs('section', {
    className: 'flex flex-col gap-3',
    children: [
      jsx(SectionHeading, { children: 'All providers' }),
      jsxs('div', {
        className: 'grid grid-cols-1 gap-3 sm:grid-cols-3',
        children: [
          Card({
            className: 'sm:col-span-1',
            children: [
              jsx(Caption, { children: 'Total tokens' }),
              jsx('div', {
                className: 'text-3xl font-semibold tabular-nums tracking-tight',
                children: fmt(t.total)
              }),
              jsx('div', { className: 'text-xs text-(--ui-text-tertiary)', children: 'all sources, this period' })
            ]
          }),
          Card({
            children: [
              jsx(Caption, { children: 'Input' }),
              jsx('div', { className: 'text-2xl font-semibold tabular-nums tracking-tight', children: fmt(t.input) }),
              jsx('div', { className: 'text-xs text-(--ui-text-tertiary)', children: fmt(t.cached) + ' cached' })
            ]
          }),
          Card({
            children: [
              jsx(Caption, { children: 'Output' }),
              jsx('div', { className: 'text-2xl font-semibold tabular-nums tracking-tight', children: fmt(t.output) }),
              jsx('div', {
                className: 'text-xs text-(--ui-text-tertiary)',
                children: t.total ? fmtPct((Number(t.output) / Number(t.total)) * 100) + ' of total' : '—'
              })
            ]
          })
        ]
      }),
      Card({
        children: [
          jsx(Caption, { children: 'Share by source' }),
          jsxs('div', {
            className: 'flex flex-col gap-2.5',
            children:
              sources.length === 0
                ? jsx('div', { className: 'text-xs text-(--ui-text-tertiary)', children: 'No source reported usage.' })
                : sources.map((name) =>
                    jsxs(
                      'div',
                      {
                        className: 'flex items-center gap-3',
                        children: [
                          jsx('div', {
                            className: 'w-32 shrink-0 truncate text-xs font-medium',
                            title: labels[name] || name,
                            children: labels[name] || name
                          }),
                          FillBar({ pct: Number(shares[name]) || 0 }),
                          jsx('span', {
                            className: 'w-14 shrink-0 text-right text-xs font-semibold tabular-nums',
                            children: fmtPct(shares[name])
                          }),
                          jsx('span', {
                            className: 'w-16 shrink-0 text-right text-xs tabular-nums text-(--ui-text-tertiary)',
                            children: fmt(perSource[name])
                          })
                        ]
                      },
                      name
                    )
                  )
          })
        ]
      }),
      Card({ children: [jsx(Caption, { children: 'Daily activity (combined)' }), chart] })
    ]
  });
}

// ── per-source cards ────────────────────────────────────────────────────────

function SourceCard({ name, src }) {
  const meta = (src && src.meta) || {};
  const label = meta.label || name;
  const totals = (src && src.totals) || {};
  const models = Object.entries((src && src.models) || {})
    .map(([key, m]) => ({ key, total: Number(m.total) || 0 }))
    .sort((a, b) => b.total - a.total)
    .slice(0, 5);
  const denom = Number(totals.total) || 1;

  return Card({
    children: [
      jsxs('div', {
        className: 'flex items-center justify-between gap-2',
        children: [
          jsx('div', { className: 'text-sm font-semibold', children: label }),
          meta.badge ? jsx(Badge, { variant: 'outline', size: 'xs', children: String(meta.badge) }) : null
        ]
      }),
      jsxs('div', {
        className: 'flex items-baseline gap-2',
        children: [
          jsx('span', { className: 'text-xl font-semibold tabular-nums tracking-tight', children: fmt(totals.total) }),
          jsx('span', { className: 'text-xs text-(--ui-text-tertiary)', children: 'tokens' })
        ]
      }),
      jsxs('div', {
        className: 'text-xs text-(--ui-text-tertiary)',
        children: [
          fmt(totals.input) + ' in · ' + fmt(totals.output) + ' out · ' + fmt(totals.cached) + ' cached'
        ]
      }),
      jsxs('div', {
        className: 'flex flex-col gap-2 border-t border-(--ui-stroke-secondary) pt-2.5',
        children:
          models.length === 0
            ? jsx('div', { className: 'text-xs text-(--ui-text-quaternary)', children: 'No model usage in this period.' })
            : models.map((m) =>
                jsxs(
                  'div',
                  {
                    className: 'flex items-center gap-2.5',
                    children: [
                      jsx('div', {
                        className: 'w-40 shrink-0 truncate text-xs',
                        title: m.key,
                        children: shortModel(m.key)
                      }),
                      FillBar({ pct: (m.total / denom) * 100 }),
                      jsx('span', {
                        className: 'w-12 shrink-0 text-right text-xs font-semibold tabular-nums',
                        children: fmtPct((m.total / denom) * 100)
                      }),
                      jsx('span', {
                        className: 'w-16 shrink-0 text-right text-xs tabular-nums text-(--ui-text-tertiary)',
                        children: fmt(m.total)
                      })
                    ]
                  },
                  m.key
                )
              )
      })
    ]
  });
}

function ProvidersSection({ sources }) {
  const entries = Object.entries(sources || {});
  const shown = entries
    .filter(([, s]) => s && (s.available || s.error))
    .sort((a, b) => {
      const oa = Number(a[1].meta && a[1].meta.order) || 100;
      const ob = Number(b[1].meta && b[1].meta.order) || 100;
      return oa === ob ? a[0].localeCompare(b[0]) : oa - ob;
    });
  const available = shown.filter(([, s]) => s.available);
  const unavailable = shown.filter(([, s]) => !s.available);

  return jsxs('section', {
    className: 'flex flex-col gap-3',
    children: [
      jsx(SectionHeading, { children: 'By provider' }),
      jsxs('div', {
        className: 'grid grid-cols-1 gap-3 lg:grid-cols-2',
        children: available.map(([n, s]) => jsx(SourceCard, { name: n, src: s, key: n }, n))
      }),
      unavailable.length
        ? jsx('div', {
            className: 'text-xs text-(--ui-text-quaternary)',
            children:
              'Not detected in this period: ' +
              unavailable.map(([, s]) => (s.meta && s.meta.label) || '').join(', ') +
              '.'
          })
        : null
    ]
  });
}

// ── usage limits (adapter-provided quota windows) ───────────────────────────

function LimitCard({ name, lim }) {
  const label = (lim && lim.label) || name;
  const badge = (lim && lim.badge) || null;
  const windows = (lim && lim.windows) || [];
  const hot = windows.some((w) => Number(w.used_pct) >= 80);
  const extras = [];
  if (lim) {
    if (Number(lim.credit_balance) > 0) extras.push('$' + Number(lim.credit_balance).toFixed(2) + ' credit balance');
    if (Number(lim.monthly_credits_remaining) > 0)
      extras.push('$' + Number(lim.monthly_credits_remaining).toFixed(2) + ' monthly credits left');
    if (lim.windows && lim.windows[0]) {
      const w0 = lim.windows[0];
      if (w0.spent_display) extras.push((w0.spent_display || '?') + ' of ' + (w0.total_display || '?') + ' spent · ' + (w0.remaining_display || '') + ' remaining');
    }
  }
  return Card({
    className: hot ? 'border-amber-500/40' : undefined,
    children: [
      jsxs('div', {
        className: 'flex items-center justify-between gap-2',
        children: [
          jsx('div', { className: 'text-sm font-semibold', children: label }),
          badge ? jsx(Badge, { variant: 'outline', size: 'xs', children: String(badge) }) : null
        ]
      }),
      ...windows.map((w, i) =>
        jsxs(
          'div',
          {
            className: 'flex flex-col gap-1.5' + (i > 0 ? ' border-t border-(--ui-stroke-secondary) pt-2.5' : ''),
            children: [
              jsxs('div', {
                className: 'flex items-baseline justify-between gap-2',
                children: [
                  jsx('span', { className: 'text-xs font-medium text-(--ui-text-secondary)', children: w.name || 'Window' }),
                  jsxs('span', {
                    className: 'flex items-baseline gap-1.5',
                    children: [
                      jsx('span', {
                        className: 'text-lg font-semibold tabular-nums tracking-tight' +
                          (Number(w.used_pct) >= 80 ? ' text-amber-600 dark:text-amber-300' : ''),
                        children: fmtPct(Number(w.used_pct) || 0)
                      }),
                      jsx('span', { className: 'text-[0.6875rem] text-(--ui-text-quaternary)', children: 'used' })
                    ]
                  })
                ]
              }),
              FillBar({ pct: Number(w.used_pct) || 0 }),
              jsx('div', {
                className: 'text-[0.6875rem] text-(--ui-text-quaternary)',
                children:
                  (w.cap != null ? fmt(w.used) + ' of ' + fmt(w.cap) + ' credits' : '') +
                  (fmtReset(w.resets_at) ? (w.cap != null ? ' · resets ' : 'resets ') + fmtReset(w.resets_at) : '')
              })
            ]
          },
          (w.name || 'w') + i
        )
      ),
      ...extras.map((e, i) => jsx('div', { className: 'text-xs text-(--ui-text-tertiary)', children: e }, 'extra' + i)),
      windows.length === 0
        ? jsx('div', { className: 'text-xs text-(--ui-text-quaternary)', children: 'No window data available.' })
        : null
    ]
  });
}

function LimitsSection({ limits }) {
  const providers = (limits && limits.providers) || {};
  const names = Object.entries(providers)
    .filter(([, lim]) => lim && lim.available)
    .map(([n]) => n);
  if (!names.length) return null;
  return jsxs('section', {
    className: 'flex flex-col gap-3',
    children: [
      jsx(SectionHeading, { children: 'Usage limits' }),
      jsxs('div', {
        className: 'grid grid-cols-1 gap-3 lg:grid-cols-3',
        children: names.map((n) => jsx(LimitCard, { name: n, lim: providers[n], key: n }, n))
      })
    ]
  });
}

// ── portal credits ──────────────────────────────────────────────────────────

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
  const planPctUsed =
    plan && plan.pct_used !== null && Number.isFinite(Number(plan.pct_used)) ? Number(plan.pct_used) : null;
  const planFill = Number(plan && plan.fill_fraction);
  const warnish = portal && (portal.status === 'low' || portal.status === 'depleted');

  return jsxs('section', {
    className: 'flex flex-col gap-3',
    children: [
      jsxs('div', {
        className: 'flex items-center gap-2',
        children: [
          jsx(SectionHeading, { children: 'Nous Portal credits' }),
          jsxs('span', {
            className: cn(
              'flex items-center gap-1.5 text-xs',
              warnish ? 'font-medium text-amber-600 dark:text-amber-300' : 'text-(--ui-text-tertiary)'
            ),
            children: [jsx(StatusDot, { tone: status.tone }), status.label]
          })
        ]
      }),
      warnish
        ? jsx('div', {
            className:
              'rounded-xl border border-(--ui-stroke-secondary) px-4 py-2.5 text-xs font-medium text-amber-600 dark:text-amber-300',
            children:
              portal.status === 'depleted'
                ? 'Your plan balance is depleted — top up on Nous Portal to keep generating.'
                : 'Your plan balance is running low — consider topping up on Nous Portal.'
          })
        : null,
      Card({
        className: warnish ? 'border-amber-500/40' : undefined,
        children: [
          jsxs('div', {
            className: 'flex items-center justify-between gap-2',
            children: [
              jsx(Caption, { children: 'Plan allowance' }),
              portal && portal.plan_name
                ? jsx(Badge, { variant: 'muted', size: 'xs', children: String(portal.plan_name) })
                : null
            ]
          }),
          jsxs('div', {
            className: 'flex items-baseline gap-2',
            children: [
              jsx('span', {
                className: 'text-3xl font-semibold tabular-nums tracking-tight',
                children: planPctUsed === null ? '—' : fmtPct(planPctUsed)
              }),
              jsx('span', { className: 'text-sm text-(--ui-text-tertiary)', children: 'used' })
            ]
          }),
          jsx('div', {
            className: 'text-xs text-(--ui-text-secondary)',
            children: plan
              ? (plan.spent_display || '—') + ' of ' + (plan.total_display || '—') +
                ' spent · ' + (plan.remaining_display || '$0.00') + ' remaining'
              : 'Allowance details unavailable for this plan.'
          }),
          jsxs('div', {
            className: 'flex items-center gap-2.5',
            children: [
              FillBar({ pct: Number.isFinite(planFill) ? planFill * 100 : 0, className: 'max-w-md' }),
              jsx('span', {
                className: 'w-24 shrink-0 text-right text-[0.6875rem] tabular-nums text-(--ui-text-quaternary)',
                children: 'bar = remaining'
              })
            ]
          })
        ]
      }),
      jsxs('div', {
        className: 'grid grid-cols-1 gap-3 sm:grid-cols-2',
        children: [
          Card({
            children: [
              jsx(Caption, { children: 'Top-up balance' }),
              jsx('div', {
                className: 'text-2xl font-semibold tabular-nums tracking-tight',
                children: (topup && topup.remaining_display) || '$0.00'
              }),
              jsx('div', {
                className: 'text-xs text-(--ui-text-secondary)',
                children: portal && portal.has_topup ? 'Purchased credits on file.' : 'No top-up purchased yet.'
              })
            ]
          }),
          Card({
            children: [
              jsx(Caption, { children: 'Total spendable' }),
              jsx('div', {
                className: 'text-2xl font-semibold tabular-nums tracking-tight',
                children: (portal && portal.total_spendable_display) || '—'
              }),
              portal && portal.renews_display
                ? jsx('div', {
                    className: 'text-xs text-(--ui-text-secondary)',
                    children: 'Renews ' + String(portal.renews_display)
                  })
                : jsx('div', { className: 'text-xs text-(--ui-text-quaternary)', children: 'Plan allowance + top-ups' })
            ]
          })
        ]
      })
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

  const onRangeChange = useCallback(
    (id) => {
      if (id === rangeRef.current) return;
      rangeRef.current = id;
      setRange(id);
      try {
        haptic();
      } catch {
        /* haptics optional */
      }
      fetchData(id);
    },
    [fetchData]
  );

  const onRefresh = useCallback(() => {
    fetchData(rangeRef.current, { silent: data !== null });
  }, [fetchData, data]);

  const onClose = useCallback(() => {
    try {
      haptic();
    } catch {
      /* haptics optional */
    }
    // Return to the chat that was open before this page. Navigating to '/'
    // would create a NEW-CHAT DRAFT and clobber the leftmost session pane
    // ('/' IS the new-chat route). The host keeps the last-focused session
    // selected while a plugin page is showing, so route back to it; fall
    // back to '/' only when no session is open at all.
    const prev =
      host.state && host.state.focusedStoredSessionId && typeof host.state.focusedStoredSessionId.get === 'function'
        ? host.state.focusedStoredSessionId.get()
        : null;
    if (prev) {
      host.navigate('/' + encodeURIComponent(String(prev)));
    } else {
      host.navigate('/');
    }
  }, []);

  const portal = data && data.portal;
  const combined = data && data.combined;
  const sources = data && data.sources;

  // Footer source list: whatever the backend actually discovered.
  const detectedLabels = sources
    ? Object.values(sources)
        .filter((s) => s && s.available)
        .map((s) => (s.meta && s.meta.label) || '')
        .filter(Boolean)
    : [];

  // True while the backend is still reading heavy providers off disk
  // (first open after a restart, or a fresh window). Not an error — the
  // numbers simply haven't landed yet; the poll picks them up.
  const anyScanning = sources
    ? Object.values(sources).some((s) => s && s.meta && s.meta.scanning)
    : false;

  return jsxs('div', {
    className: 'mx-auto flex w-full max-w-4xl flex-col gap-7 px-6 pb-14 pt-6',
    children: [
      jsxs('div', {
        className: 'flex flex-wrap items-end justify-between gap-4',
        children: [
          jsxs('div', {
            className: 'flex min-w-0 flex-col gap-1',
            children: [
              jsx('h1', { className: 'text-xl font-semibold tracking-tight', children: 'Usage' }),
              jsx('p', {
                className: 'text-xs text-(--ui-text-tertiary)',
                children: 'Every AI tool on this machine, in one place.'
              })
            ]
          }),
          jsxs('div', {
            className: 'flex items-center gap-2.5',
            children: [
              refreshing
                ? jsx(GlyphSpinner, { className: 'text-(--ui-text-quaternary)', ariaLabel: 'Refreshing usage' })
                : null,
              jsx(SegmentedControl, { options: RANGES, value: range, onChange: onRangeChange }),
              jsx(Button, {
                variant: 'ghost',
                size: 'icon-sm',
                onClick: onClose,
                'aria-label': 'Close Usage page',
                title: 'Close',
                children: jsx(Codicon, { name: 'chrome-close', size: '1rem' })
              })
            ]
          })
        ]
      }),

      error
        ? jsxs('div', {
            className: 'rounded-xl border border-(--ui-stroke-secondary) p-5',
            children: [
              jsx(ErrorState, {
                title: 'Usage data unavailable',
                description: 'Usage backend not reachable yet — restart Hermes once to activate it.'
              }),
              jsx('div', {
                className: 'mt-4 flex justify-center',
                children: jsx(Button, { size: 'xs', onClick: onRefresh, children: 'Retry now' })
              })
            ]
          })
        : null,

      // First-run scan in progress (backend answered fine — heavy providers
      // are still being read off disk). Show skeletons, not an error.
      !error && data && anyScanning
        ? jsxs('div', {
            className: 'flex items-center gap-2.5 rounded-xl border border-(--ui-stroke-secondary) px-4 py-3',
            children: [
              jsx(GlyphSpinner, { className: 'text-(--ui-text-quaternary)', ariaLabel: 'Scanning usage' }),
              jsx('div', {
                className: 'text-xs text-(--ui-text-tertiary)',
                children: 'Reading local usage history — first numbers land within ~30s.'
              })
            ]
          })
        : null,

      loading && !data
        ? jsxs(Fragment, {
            children: [
              jsx(Skeleton, { className: 'h-28 w-full rounded-xl' }),
              jsx('div', { className: 'grid grid-cols-1 gap-3 sm:grid-cols-3', children: [
                jsx(Skeleton, { className: 'h-24 w-full rounded-xl' }),
                jsx(Skeleton, { className: 'h-24 w-full rounded-xl' }),
                jsx(Skeleton, { className: 'h-24 w-full rounded-xl' })
              ] }),
              jsx(Skeleton, { className: 'h-40 w-full rounded-xl' }),
              jsx('div', { className: 'grid grid-cols-1 gap-3 lg:grid-cols-2', children: [
                jsx(Skeleton, { className: 'h-48 w-full rounded-xl' }),
                jsx(Skeleton, { className: 'h-48 w-full rounded-xl' })
              ] }),
              jsx(Skeleton, { className: 'h-36 w-full rounded-xl' })
            ]
          })
        : null,

      combined ? jsx(CombinedSection, { combined: combined }) : null,
      data && data.limits ? jsx(LimitsSection, { limits: data.limits }) : null,
      sources ? jsx(ProvidersSection, { sources: sources }) : null,
      portal && portal.available
        ? jsx(CreditsSection, { portal: portal })
        : portal && portal.available === false
          ? jsx('section', {
              className: 'flex flex-col gap-3',
              children: jsx('div', {
                className: 'rounded-xl border border-(--ui-stroke-secondary) px-4 py-2',
                children: jsx(EmptyState, {
                  title: 'Sign in to Nous Portal to see your credit balance.',
                  description: 'Provider usage above is tracked locally and always available.',
                  className: 'min-h-0 py-2'
                })
              })
            })
          : null,

      jsxs('div', {
        className: 'flex items-center justify-between gap-3 border-t border-(--ui-stroke-secondary) pt-4',
        children: [
          jsx('p', {
            className: 'text-[0.6875rem] leading-relaxed text-(--ui-text-quaternary)',
            children:
              (detectedLabels.length
                ? 'Sources: ' + detectedLabels.join(' · ') + ' — '
                : '') +
              'tokens from local history, limits live. Auto-refreshes every 20s. Add your own tools via adapters (' +
              README_ANCHOR +
              ').'
          }),
          jsxs('div', {
            className: 'flex shrink-0 items-center gap-2',
            children: [
              refreshing
                ? jsx(GlyphSpinner, { className: 'text-(--ui-text-quaternary)', ariaLabel: 'Refreshing' })
                : null,
              jsx(Button, { size: 'xs', onClick: onRefresh, children: 'Refresh' })
            ]
          })
        ]
      })
    ]
  });
}

// ── registration ────────────────────────────────────────────────────────────

export default {
  id: 'usage-dashboard',
  name: 'Usage Dashboard',
  register(ctx) {
    pluginCtx = ctx;
    ctx.register({
      id: 'page',
      area: ROUTES_AREA,
      title: 'Usage',
      data: { path: '/usage-dashboard' },
      render: () => jsx(UsagePage, {})
    });
    ctx.register({
      id: 'nav',
      area: SIDEBAR_NAV_AREA,
      data: { codicon: 'graph', label: 'Usage', path: '/usage-dashboard' }
    });
  }
};
