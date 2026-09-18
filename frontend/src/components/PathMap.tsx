import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { MapStop } from "./PathMapCard";

/** Leaflet lives in its own chunk: this file is only loaded once a target page has a map to draw. */

const TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const TILE_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a> contributors';

function cssColor(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string);
}

export function stopTitle(stop: MapStop): string {
  if (stop.kind === "hop" && stop.hopNos.length > 1) return `Hops ${stop.hopNos[0]}–${stop.hopNos[stop.hopNos.length - 1]}`;
  return stop.title;
}

function popupHtml(stop: MapStop): string {
  const lines = [`<div class="font-semibold">${escapeHtml(stopTitle(stop))}</div>`];
  if (stop.place) lines.push(`<div>${escapeHtml(stop.place)}</div>`);
  stop.lines.forEach((l) => lines.push(`<div class="font-mono text-[11px] text-muted">${escapeHtml(l)}</div>`));
  return `<div class="text-xs leading-snug">${lines.join("")}</div>`;
}

function markerStyle(stop: MapStop, colors: Record<string, string>): { radius: number; color: string; fillColor: string; weight: number } {
  if (stop.kind === "source") return { radius: 8, color: colors.surface, fillColor: colors.accent, weight: 2 };
  if (stop.kind === "destination") return { radius: 8, color: colors.surface, fillColor: stop.reached === false ? colors.down : colors.up, weight: 2 };
  return { radius: 5, color: colors.surface, fillColor: colors.hop, weight: 1.5 };
}

export default function PathMap({ stops, paths, dark, height = 360 }: { stops: MapStop[]; paths: [number, number][][]; dark: boolean; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.LayerGroup | null>(null);
  const fittedRef = useRef<string>("");

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    // Scroll-wheel zoom stays off so the page keeps scrolling over the map; the zoom control and pinch still work.
    const map = L.map(el, { scrollWheelZoom: false, worldCopyJump: true, zoomControl: true, attributionControl: true });
    L.tileLayer(TILE_URL, { maxZoom: 18, attribution: TILE_ATTRIBUTION }).addTo(map);
    map.setView([25, 10], 2);
    const layer = L.layerGroup().addTo(map);
    mapRef.current = map;
    layerRef.current = layer;
    const ro = new ResizeObserver(() => map.invalidateSize());
    ro.observe(el);
    return () => {
      ro.disconnect();
      map.remove();
      mapRef.current = null;
      layerRef.current = null;
      fittedRef.current = "";
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    const layer = layerRef.current;
    if (!map || !layer) return;
    const colors = {
      accent: cssColor("--accent", "#38bdf8"),
      up: cssColor("--up", "#22c55e"),
      down: cssColor("--down", "#ef4444"),
      hop: cssColor("--paused", "#94a3b8"),
      surface: cssColor("--surface", "#ffffff"),
      text: cssColor("--text", "#0f172a"),
    };
    layer.clearLayers();
    paths.forEach((p) => {
      if (p.length > 1) L.polyline(p, { color: colors.accent, weight: 2, opacity: 0.75, dashArray: "6 6" }).addTo(layer);
    });
    stops.forEach((stop) => {
      const style = markerStyle(stop, colors);
      const marker = L.circleMarker([stop.lat, stop.lon], { ...style, fillOpacity: 1 }).addTo(layer);
      marker.bindPopup(popupHtml(stop), { closeButton: false, maxWidth: 260 });
      const title = stopTitle(stop);
      marker.bindTooltip(escapeHtml(title), { direction: "top", offset: [0, -style.radius], permanent: stop.kind !== "hop", opacity: 0.95, className: "path-map-label" });
    });
    const key = stops.map((s) => `${s.id}@${s.lat.toFixed(2)},${s.lon.toFixed(2)}`).join("|");
    if (key !== fittedRef.current && stops.length) {
      // Fit once per distinct set of points, so a poll refresh does not undo the operator's pan and zoom.
      fittedRef.current = key;
      const bounds = L.latLngBounds(stops.map((s) => [s.lat, s.lon] as [number, number]));
      if (bounds.getNorthEast().equals(bounds.getSouthWest())) map.setView(bounds.getCenter(), 6);
      else map.fitBounds(bounds, { padding: [28, 28], maxZoom: 9 });
    }
  }, [stops, paths, dark]);

  return <div ref={ref} className={`path-map ${dark ? "path-map-dark" : ""}`} style={{ height }} role="img" aria-label="Map of the monitoring path" />;
}
