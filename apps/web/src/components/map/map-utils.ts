import type { LatLng, MapViewport, ProjectedPoint } from "./map-types";

export const center: LatLng = [37.5667, 126.9784];
export const maxZoom = 17;
export const minZoom = 12;
export const tileSize = 256;
export const zoom = 14;

export function clampZoom(value: number) {
  return Math.min(maxZoom, Math.max(minZoom, value));
}

export function latLngToPixel([lat, lng]: LatLng, zoomLevel = zoom) {
  const scale = tileSize * 2 ** zoomLevel;
  const sinLat = Math.sin((lat * Math.PI) / 180);

  return {
    x: ((lng + 180) / 360) * scale,
    y: (0.5 - Math.log((1 + sinLat) / (1 - sinLat)) / (4 * Math.PI)) * scale
  };
}

export function pixelToLatLng(point: ProjectedPoint, zoomLevel = zoom): LatLng {
  const scale = tileSize * 2 ** zoomLevel;
  const lng = (point.x / scale) * 360 - 180;
  const n = Math.PI - (2 * Math.PI * point.y) / scale;
  const lat = (180 / Math.PI) * Math.atan(0.5 * (Math.exp(n) - Math.exp(-n)));

  return [lat, lng];
}

export function project(position: LatLng, viewport: MapViewport): ProjectedPoint {
  const point = latLngToPixel(position, viewport.zoom);
  const centerPoint = latLngToPixel(viewport.center, viewport.zoom);

  return {
    x: point.x - centerPoint.x + viewport.width / 2,
    y: point.y - centerPoint.y + viewport.height / 2
  };
}

export function routePoints(points: LatLng[], viewport: MapViewport) {
  return points
    .map((point) => {
      const projected = project(point, viewport);
      return `${projected.x},${projected.y}`;
    })
    .join(" ");
}
