import {
  AreaSeries,
  CandlestickSeries,
  ColorType,
  createChart,
  HistogramSeries,
  LineSeries,
  LineStyle,
  type IChartApi,
} from "lightweight-charts";
import { useEffect, useRef, useState } from "react";

export type Point = { time: string; value: number };
export type Candle = { time: string; open: number; high: number; low: number; close: number };

export type SeriesSpec =
  | { kind: "line" | "area"; data: Point[]; color?: string; title?: string; dashed?: boolean; scale?: string }
  | { kind: "candles"; data: Candle[] }
  | { kind: "volume"; data: Point[] };

function css(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function alpha(hex: string, a: number): string {
  const h = hex.replace("#", "");
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
  return `rgba(${r}, ${g}, ${b}, ${a})`;
}

/** Bumps when the colour scheme changes, so charts redraw with the new palette. */
export function useTheme(): number {
  const [version, setVersion] = useState(0);
  useEffect(() => {
    const bump = () => setVersion((v) => v + 1);
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    media.addEventListener("change", bump);
    const observer = new MutationObserver(bump);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => {
      media.removeEventListener("change", bump);
      observer.disconnect();
    };
  }, []);
  return version;
}

export function TimeChart({
  series,
  height = 320,
  percent = false,
}: {
  series: SeriesSpec[];
  height?: number;
  percent?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const theme = useTheme();

  useEffect(() => {
    if (!ref.current) return;
    const text = css("--text-3");
    const border = css("--border");
    const chart: IChartApi = createChart(ref.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: text,
        fontFamily: css("--font"),
        fontSize: 11,
        attributionLogo: false,
      },
      grid: { vertLines: { visible: false }, horzLines: { color: border } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false },
      crosshair: {
        vertLine: { color: css("--border-strong"), labelBackgroundColor: css("--text-2") },
        horzLine: { color: css("--border-strong"), labelBackgroundColor: css("--text-2") },
      },
      localization: percent ? { priceFormatter: (p: number) => `${p.toFixed(1)}%` } : undefined,
    });
    const accent = css("--accent");
    for (const s of series) {
      if (s.kind === "candles") {
        const up = css("--up");
        const down = css("--down");
        chart
          .addSeries(CandlestickSeries, {
            upColor: up,
            downColor: down,
            wickUpColor: up,
            wickDownColor: down,
            borderVisible: false,
          })
          .setData(s.data);
      } else if (s.kind === "volume") {
        const v = chart.addSeries(HistogramSeries, {
          priceScaleId: "volume",
          priceFormat: { type: "volume" },
          color: css("--border-strong"),
          lastValueVisible: false,
          priceLineVisible: false,
        });
        v.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
        v.setData(s.data);
      } else if (s.kind === "area") {
        const color = s.color ?? accent;
        chart
          .addSeries(AreaSeries, {
            lineColor: color,
            topColor: alpha(color, 0.22),
            bottomColor: alpha(color, 0),
            lineWidth: 2,
            title: s.title,
            priceScaleId: s.scale,
            priceLineVisible: false,
          })
          .setData(s.data);
      } else {
        chart
          .addSeries(LineSeries, {
            color: s.color ?? accent,
            lineWidth: s.dashed ? 1 : 2,
            lineStyle: s.dashed ? LineStyle.Dashed : LineStyle.Solid,
            title: s.title,
            priceScaleId: s.scale,
            priceLineVisible: false,
            lastValueVisible: !s.dashed,
          })
          .setData(s.data);
      }
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [series, theme, percent]);

  return <div ref={ref} style={{ width: "100%", height }} />;
}

/** Points from aligned series, dropping missing values. */
export function points(dates: string[], values: (number | null)[], scale = 1): Point[] {
  const out: Point[] = [];
  values.forEach((v, i) => {
    if (v != null) out.push({ time: dates[i], value: v * scale });
  });
  return out;
}
