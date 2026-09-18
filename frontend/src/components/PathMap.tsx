import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { MapPlace } from "./PathMapCard";

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

function popupHtml(place: MapPlace): string {
  const lines = [`<div class="font-semibold">${escapeHtml(place.title)}</div>`];
  if (place.place) lines.push(`<div>${escapeHtml(place.place)}</div>`);
  place.lines.forEach((l) => lines.push(`<div class="font-mono text-[11px] text-muted">${escapeHtml(l)}</div>`));
  return `<div class="text-xs leading-snug">${lines.join("")}</div>`;
}

function markerColor(place: MapPlace, colors: Record<string, string>): string {
  if (place.kind === "source") return colors.accent;
  if (place.kind === "destination") return place.reached === false ? colors.down : colors.up;
  return colors.hop;
}

export default function PathMap({ places, paths, dark, height = 360 }: { places: MapPlace[]; paths: [number, number][][]; dark: boolean; height?: number }) {
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
    };
    layer.clearLayers();
    paths.forEach((p) => {
      if (p.length > 1) L.polyline(p, { color: colors.accent, weight: 2, opacity: 0.75, dashArray: "6 6" }).addTo(layer);
    });
    places.forEach((place) => {
      const color = markerColor(place, colors);
      // A labelled pill instead of a plain dot: the hop numbers on the marker make the order readable and show
      // when several hops share one place (which is why two dashed lines can meet at the target).
      const icon = L.divIcon({
        className: `path-map-marker path-map-marker-${place.kind}`,
        html: `<span style="background:${color}" title="${escapeHtml(place.title)}">${escapeHtml(place.label)}</span>`,
        iconSize: [0, 0],
        iconAnchor: [0, 0],
      });
      const marker = L.marker([place.lat, place.lon], { icon, zIndexOffset: place.kind === "hop" ? 0 : 1000, keyboard: true, alt: place.title }).addTo(layer);
      marker.bindPopup(popupHtml(place), { closeButton: false, maxWidth: 280 });
    });
    const key = places.map((s) => `${s.id}@${s.lat.toFixed(2)},${s.lon.toFixed(2)}`).join("|");
    if (key !== fittedRef.current && places.length) {
      // Fit once per distinct set of points, so a poll refresh does not undo the operator's pan and zoom.
      fittedRef.current = key;
      const bounds = L.latLngBounds(places.map((s) => [s.lat, s.lon] as [number, number]));
      if (bounds.getNorthEast().equals(bounds.getSouthWest())) map.setView(bounds.getCenter(), 6);
      else map.fitBounds(bounds, { padding: [36, 36], maxZoom: 9 });
    }
  }, [places, paths, dark]);

  return <div ref={ref} className={`path-map ${dark ? "path-map-dark" : ""}`} style={{ height }} role="img" aria-label="Map of the monitoring path" />;
}
