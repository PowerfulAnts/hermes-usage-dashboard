/**
 * usage-dashboard — Hermes desktop plugin UI (Hermes-only tracker)
 * ---------------------------------------------------------------------------
 * Page: /usage-dashboard ("Usage" in the sidebar). Token usage of everything
 * that ran INSIDE Hermes: in / out / cached / cache-write tokens per billing
 * provider, each provider's cache hit rate, and the total hit rate across
 * all providers.
 *
 * DATA SOURCE: GET /api/plugins/usage-dashboard/summary?days=N (7/30/90)
 * via pluginCtx.rest('/summary?days=N'). Response contract (see sources.py):
 *   { available, days, generated_at,
 *     totals:    bucket,
 *     daily:     { 'YYYY-MM-DD': bucket },
 *     providers: { '<billing_provider>': bucket } }
 *   bucket = { input, output, cached, cache_write, total, api_calls,
 *              hit_rate_pct }  // hit_rate_pct: null = unknown → "—"
 *
 * CACHE MATH (do not change here): input EXCLUDES cache; prompt =
 * input + cached + cache_write; hit rate = cached / prompt. Computed by the
 * backend — this file only renders it.
 *
 * RESTART: if the endpoint errors/404s the backend predates the plugin —
 * restart Hermes once; the page shows an ErrorState saying exactly that.
 *
 * VALIDATE ESM: `node --check` on a .mjs COPY of this file — plain .js check
 * misses ASI traps that break the app's real loader.
 * ---------------------------------------------------------------------------
 */

import {
  Button,
  Codicon,
  ErrorState,
  GlyphSpinner,
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

function SectionHeading({ children }) {
  return jsx('h2', { className: 'text-sm font-semibold tracking-tight', children });
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

// ── hero: total tokens + TOTAL cache hit rate ───────────────────────────────

function TotalsSection({ totals, days }) {
  const t = totals || {};
  const hitPct = t.hit_rate_pct === null || t.hit_rate_pct === undefined ? null : Number(t.hit_rate_pct);
  const cachedShareOfTotal = Number(t.total) > 0 ? (Number(t.cached) / Number(t.total)) * 100 : 0;

  return jsxs('section', {
    className: 'flex flex-col gap-3',
    children: [
      jsx(SectionHeading, { children: 'Tokens used inside Hermes' }),
      jsxs('div', {
        className: 'grid grid-cols-1 gap-3 sm:grid-cols-3',
        children: [
          Card({
            children: [
              jsx(Caption, { children: 'Total tokens · last ' + days + 'd' }),
              jsx('div', {
                className: 'text-3xl font-semibold tabular-nums tracking-tight',
                children: fmt(t.total)
              }),
              jsx('div', {
                className: 'text-xs text-(--ui-text-tertiary)',
                children: fmt(t.api_calls) + ' API calls'
              })
            ]
          }),
          Card({
            children: [
              jsx(Caption, { children: 'In / Out' }),
              jsxs('div', {
                className: 'flex items-baseline gap-2 text-2xl font-semibold tabular-nums tracking-tight',
                children: [fmt(t.input), jsx('span', { className: 'text-sm font-normal text-(--ui-text-quaternary)', children: '/' }), fmt(t.output)]
              }),
              jsx('div', {
                className: 'text-xs text-(--ui-text-tertiary)',
                children: 'input excludes cached tokens'
              })
            ]
          }),
          Card({
            children: [
              jsx(Caption, { children: 'Cache hit rate' }),
              jsxs('div', {
                className: 'flex items-baseline gap-2',
                children: [
                  jsx('span', {
                    className: 'text-3xl font-semibold tabular-nums tracking-tight',
                    style: { color: 'var(--ui-accent)' },
                    children: fmtPct(hitPct)
                  }),
                  jsx('span', {
                    className: 'text-xs text-(--ui-text-tertiary)',
                    children: hitPct === null ? '' : 'of all prompt tokens'
                  })
                ]
              }),
              FillBar({ pct: hitPct === null ? 0 : hitPct }),
              jsx('div', {
                className: 'text-xs text-(--ui-text-tertiary)',
                children:
                  hitPct === null
                    ? 'No cache data reported in this period.'
                    : fmt(t.cached) + ' of ' + fmt(Number(t.input) + Number(t.cached) + Number(t.cache_write)) + ' prompt tokens served from cache'
              })
            ]
          })
        ]
      })
    ]
  });
}

// ── per-provider table with per-provider hit rates ──────────────────────────

const COLS = [
  { key: 'total', label: 'Total', w: 'w-[7.5rem]' },
  { key: 'input', label: 'In', w: 'w-[6rem]' },
  { key: 'output', label: 'Out', w: 'w-[6rem]' },
  { key: 'cached', label: 'Cached', w: 'w-[7rem]' },
  { key: 'hit', label: 'Hit rate', w: 'w-[11rem]' }
];

function ProvidersTable({ providers }) {
  const rows = Object.entries(providers || {})
    .map(([name, b]) => ({ name, b: b || {} }))
    .sort((x, y) => (Number(y.b.total) || 0) - (Number(x.b.total) || 0));

  if (!rows.length) {
    return jsx('section', {
      className: 'flex flex-col gap-3',
      children: [
        jsx(SectionHeading, { children: 'By provider' }),
        jsx(Card, { children: jsx('div', { className: 'text-xs text-(--ui-text-tertiary)', children: 'No provider usage recorded in this period.' }) })
      ]
    });
  }

  return jsxs('section', {
    className: 'flex flex-col gap-3',
    children: [
      jsx(SectionHeading, { children: 'By provider' }),
      Card({ className: 'gap-3 overflow-x-auto', children: ProviderRows({ rows }) })
    ]
  });
}

function ProviderRows({ rows }) {
  return jsxs(Fragment, {
    children: [
      // header row
      jsxs('div', {
        className: 'flex items-center gap-4 border-b border-(--ui-stroke-secondary) pb-2',
        children: [
          jsx('div', { className: 'w-40 shrink-0 text-left text-[0.6875rem] uppercase tracking-wider text-(--ui-text-quaternary)', children: 'Provider' }),
          ...COLS.map((c) =>
            jsx(
              'div',
              {
                className: c.w + ' shrink-0 text-right text-[0.6875rem] uppercase tracking-wider text-(--ui-text-quaternary)',
                children: c.label
              },
              c.key
            )
          )
        ]
      }),
      ...rows.map(({ name, b }) =>
        jsxs(
          'div',
          {
            className: 'flex items-center gap-4',
            title: name === 'unknown' ? 'Legacy rows without a billing provider' : name,
            children: [
              jsx('div', { className: 'w-40 shrink-0 truncate text-xs font-medium', children: name }),
              jsx('span', { className: COLS[0].w + ' shrink-0 text-right text-xs font-semibold tabular-nums', children: fmt(b.total) }),
              jsx('span', { className: COLS[1].w + ' shrink-0 text-right text-xs tabular-nums text-(--ui-text-secondary)', children: fmt(b.input) }),
              jsx('span', { className: COLS[2].w + ' shrink-0 text-right text-xs tabular-nums text-(--ui-text-secondary)', children: fmt(b.output) }),
              jsx('span', { className: COLS[3].w + ' shrink-0 text-right text-xs tabular-nums text-(--ui-text-secondary)', children: fmt(b.cached) }),
              jsxs('div', { className: COLS[4].w + ' flex shrink-0 items-center justify-end gap-2', children: [
                jsx('span', {
                  className: 'w-12 text-right text-xs font-semibold tabular-nums',
                  children: fmtPct(b.hit_rate_pct)
                }),
                FillBar({ pct: Number(b.hit_rate_pct) || 0, className: 'max-w-[3.5rem]' })
              ] })
            ]
          },
          name
        )
      ),
      jsx('div', {
        className: 'border-t border-(--ui-stroke-secondary) pt-2 text-[0.6875rem] leading-relaxed text-(--ui-text-quaternary)',
        children:
          'Hit rate = cached ÷ (in + cached + cache-written). "—" = provider reported no cache data.'
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

  const ok = data && data.available !== false;
  const totals = ok ? data.totals : null;
  const providers = ok ? data.providers : {};

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
                children: 'Token usage inside Hermes — per provider, with cache hit rates.'
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

      loading && !data
        ? jsxs(Fragment, {
            children: [
              jsx(Skeleton, { className: 'h-28 w-full rounded-xl' }),
              jsx('div', { className: 'grid grid-cols-1 gap-3 sm:grid-cols-3', children: [
                jsx(Skeleton, { className: 'h-28 w-full rounded-xl' }),
                jsx(Skeleton, { className: 'h-28 w-full rounded-xl' }),
                jsx(Skeleton, { className: 'h-28 w-full rounded-xl' })
              ] }),
              jsx(Skeleton, { className: 'h-48 w-full rounded-xl' })
            ]
          })
        : null,

      !error && !loading && !ok
        ? jsx(Card, {
            children: jsx('div', {
              className: 'text-xs text-(--ui-text-tertiary)',
              children: data && data.error ? String(data.error) : 'No usage data available for this period.'
            })
          })
        : null,

      ok && totals ? jsx(TotalsSection, { totals: totals, days: range }) : null,
      ok ? jsx(ProvidersTable, { providers: providers }) : null,

      jsxs('div', {
        className: 'flex items-center justify-between gap-3 border-t border-(--ui-stroke-secondary) pt-4',
        children: [
          jsx('p', {
            className: 'text-[0.6875rem] leading-relaxed text-(--ui-text-quaternary)',
            children:
              'Counts only tokens used directly inside Hermes (its own request log). Auto-refreshes every 20s.'
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
