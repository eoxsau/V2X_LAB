"use client";

import { useEffect, useMemo, useState } from "react";
import type { SimulationStreamPayload } from "@/components/map/map-types";

type StreamStatus = "connecting" | "connected" | "disconnected" | "error";

export function useSimulationStream() {
  const [payload, setPayload] = useState<SimulationStreamPayload | null>(null);
  const [status, setStatus] = useState<StreamStatus>("connecting");

  const wsUrl = useMemo(() => process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000", []);

  useEffect(() => {
    let reconnectTimer: number | undefined;
    let connectionTimer: number | undefined;
    let isClosedByEffect = false;
    let socket: WebSocket | null = null;
    let urlIndex = 0;
    const urls = [
      `${wsUrl.replace(/\/$/, "")}/ws/simulation`,
      `${wsUrl.replace("localhost", "127.0.0.1").replace(/\/$/, "")}/ws/simulation`
    ].filter((url, index, items) => items.indexOf(url) === index);

    function connect() {
      setStatus("connecting");
      socket = new WebSocket(urls[urlIndex]);
      connectionTimer = window.setTimeout(() => {
        if (socket?.readyState === WebSocket.CONNECTING) {
          socket.close();
        }
      }, 2500);

      socket.addEventListener("open", () => {
        if (connectionTimer) {
          window.clearTimeout(connectionTimer);
        }
        setStatus("connected");
      });

      socket.addEventListener("message", (event) => {
        try {
          setPayload(JSON.parse(event.data) as SimulationStreamPayload);
        } catch {
          setStatus("error");
        }
      });

      socket.addEventListener("close", () => {
        if (connectionTimer) {
          window.clearTimeout(connectionTimer);
        }
        if (isClosedByEffect) {
          return;
        }
        urlIndex = (urlIndex + 1) % urls.length;
        setStatus("disconnected");
        reconnectTimer = window.setTimeout(connect, 1500);
      });

      socket.addEventListener("error", () => {
        setStatus("error");
        socket?.close();
      });
    }

    connect();

    return () => {
      isClosedByEffect = true;
      if (reconnectTimer) {
        window.clearTimeout(reconnectTimer);
      }
      if (connectionTimer) {
        window.clearTimeout(connectionTimer);
      }
      socket?.close();
    };
  }, [wsUrl]);

  return { payload, status };
}
