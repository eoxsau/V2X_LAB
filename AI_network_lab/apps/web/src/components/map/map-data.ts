export type LatLng = [number, number];

export type EnvironmentMode = "current" | "research";

export type ProjectedPoint = {
  visible: boolean;
  x: number;
  y: number;
};

export type BaseStation = {
  id: string;
  label: string;
  position: LatLng;
  load: number;
  source: "public" | "synthetic";
  status: "stable" | "warning" | "danger";
};

export type UserMarkerData = {
  id: string;
  connectedTo: string;
  latencyMs: number;
  position: LatLng;
  speedKmh: number;
};

export const seoulCenter: LatLng = [37.5667, 126.9784];

export const mockBaseStations: BaseStation[] = [
  {
    id: "BS-SEO-01",
    label: "City Hall Sector",
    position: [37.5669, 126.9782],
    load: 42,
    source: "public",
    status: "stable"
  },
  {
    id: "BS-SEO-02",
    label: "Euljiro Edge",
    position: [37.5661, 126.9912],
    load: 63,
    source: "public",
    status: "stable"
  },
  {
    id: "BS-SEO-03",
    label: "Namdaemun Relay",
    position: [37.5598, 126.9771],
    load: 78,
    source: "public",
    status: "warning"
  },
  {
    id: "BS-SEO-04",
    label: "Gwanghwamun Node",
    position: [37.5759, 126.9768],
    load: 36,
    source: "public",
    status: "stable"
  },
  {
    id: "BS-SEO-05",
    label: "Myeongdong Microcell",
    position: [37.5637, 126.985],
    load: 91,
    source: "public",
    status: "danger"
  }
];

export const initialMockUsers: UserMarkerData[] = [
  {
    id: "UE-1842",
    connectedTo: "BS-SEO-01",
    latencyMs: 18,
    position: [37.5658, 126.9821],
    speedKmh: 18
  },
  {
    id: "UE-2031",
    connectedTo: "BS-SEO-03",
    latencyMs: 26,
    position: [37.5615, 126.9762],
    speedKmh: 31
  },
  {
    id: "UE-2258",
    connectedTo: "BS-SEO-04",
    latencyMs: 16,
    position: [37.5732, 126.9796],
    speedKmh: 12
  },
  {
    id: "UE-3110",
    connectedTo: "BS-SEO-05",
    latencyMs: 34,
    position: [37.5628, 126.9872],
    speedKmh: 24
  }
];

export const researchMockUsers: UserMarkerData[] = [
  ...initialMockUsers,
  {
    id: "UE-R601",
    connectedTo: "BS-SEO-02",
    latencyMs: 22,
    position: [37.5684, 126.9881],
    speedKmh: 28
  },
  {
    id: "UE-R602",
    connectedTo: "BS-SEO-03",
    latencyMs: 31,
    position: [37.5589, 126.9813],
    speedKmh: 37
  },
  {
    id: "UE-R603",
    connectedTo: "BS-SEO-05",
    latencyMs: 29,
    position: [37.5642, 126.9894],
    speedKmh: 21
  },
  {
    id: "UE-R604",
    connectedTo: "BS-SEO-04",
    latencyMs: 19,
    position: [37.5722, 126.9737],
    speedKmh: 34
  }
];

export const TILE_SIZE = 256;

export function latLngToWorldPixel([lat, lng]: LatLng, zoom: number) {
  const scale = TILE_SIZE * 2 ** zoom;
  const sinLatitude = Math.sin((lat * Math.PI) / 180);

  return {
    x: ((lng + 180) / 360) * scale,
    y:
      (0.5 - Math.log((1 + sinLatitude) / (1 - sinLatitude)) / (4 * Math.PI)) *
      scale
  };
}

export function pixelToLatLng(point: { x: number; y: number }, zoom: number): LatLng {
  const scale = TILE_SIZE * 2 ** zoom;
  const lng = (point.x / scale) * 360 - 180;
  const mercatorY = Math.PI * (1 - (2 * point.y) / scale);
  const lat = (Math.atan(Math.sinh(mercatorY)) * 180) / Math.PI;

  return [lat, lng];
}

export function projectToViewport(
  position: LatLng,
  center: LatLng,
  zoom: number,
  size: { height: number; width: number },
  offset: { x: number; y: number }
): ProjectedPoint {
  const point = latLngToWorldPixel(position, zoom);
  const centerPoint = latLngToWorldPixel(center, zoom);
  const x = point.x - centerPoint.x + size.width / 2 + offset.x;
  const y = point.y - centerPoint.y + size.height / 2 + offset.y;

  return {
    visible: x > -80 && x < size.width + 80 && y > -80 && y < size.height + 80,
    x,
    y
  };
}

export function normalizeTileX(tileX: number, zoom: number) {
  const maxTile = 2 ** zoom;
  return ((tileX % maxTile) + maxTile) % maxTile;
}
